import { create } from "zustand";
import {
  apiGet, apiPost, apiPatch, apiDelete, uploadFile, eventSourceUrl, uidToRef,
} from "./api";
import type {
  Conversation, CouncilDefinition, Message, ModelInfo, Project, ProviderInstance,
  ProviderType, ToolDescriptor, TraceEvent, Workflow,
} from "./types";

export interface LiveTool { name: string; status: string; output?: string; error?: string; latency_ms?: number; }
export interface LiveNode {
  node_id: string; label: string; role?: string; provider?: string; model?: string;
  provider_label?: string; status: "waiting" | "running" | "done" | "failed";
  attempt?: number; fallback?: boolean; text?: string; latency_ms?: number;
  usage?: { input: number; output: number; cost: number; estimated?: boolean };
  logs: { level: string; message: string }[];
  tools: LiveTool[];
  stage?: string;
}
export interface LiveRun {
  runId: string;
  status: "running" | "ok" | "failed" | null;
  mode: string;
  question: string;
  nodes: Record<string, LiveNode>;
  nodeOrder: string[];
  rounds: { round: number; title: string; status: string }[];
  plan?: any;
  final: string;
  totals?: any;
  contributors?: any[];
  error?: string;
  events: TraceEvent[];
}

export type Mode = "single" | "router" | "council" | "workflow";

interface State {
  // auth / setup
  passwordRequired: boolean;
  setupCompleted: boolean;
  // data
  projects: Project[];
  projectId: string | null;
  conversations: Conversation[];
  conversationId: string | null;
  messages: Message[];
  providers: ProviderInstance[];
  providerTypes: ProviderType[];
  models: ModelInfo[];
  workflows: Workflow[];
  tools: ToolDescriptor[];
  // composer / mode draft
  mode: Mode;
  singleRef: string; // uid
  council: CouncilDefinition;
  workflowId: string | null;
  research: boolean;
  preferFree: boolean;
  // live execution
  live: LiveRun | null;
  busy: boolean;
  // actions
  bootstrap: () => Promise<void>;
  refreshProviders: () => Promise<void>;
  refreshModels: () => Promise<void>;
  refreshWorkflows: () => Promise<void>;
  refreshTools: () => Promise<void>;
  setProject: (id: string) => Promise<void>;
  newConversation: (mode?: Mode) => Promise<string>;
  selectConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  setMode: (m: Mode) => void;
  setCouncil: (c: Partial<CouncilDefinition>) => void;
  startRun: (question: string, files?: File[]) => Promise<void>;
  loadDefaultCouncil: () => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
}

const emptyLive = (runId: string, mode: string, question: string): LiveRun => ({
  runId, status: "running", mode, question,
  nodes: {}, nodeOrder: [], rounds: [], final: "", events: [],
});

function upsertNode(live: LiveRun, node_id: string, patch: Partial<LiveNode>): LiveNode {
  let n = live.nodes[node_id];
  if (!n) {
    n = { node_id, label: node_id, status: "waiting", logs: [], tools: [] };
    live.nodes[node_id] = n;
    live.nodeOrder.push(node_id);
  }
  Object.assign(n, patch);
  return n;
}

export const useStore = create<State>((set, get) => ({
  passwordRequired: false,
  setupCompleted: true,
  projects: [],
  projectId: null,
  conversations: [],
  conversationId: null,
  messages: [],
  providers: [],
  providerTypes: [],
  models: [],
  workflows: [],
  tools: [],
  mode: "council",
  singleRef: "",
  council: {
    participants: [],
    parallel: true,
    critique_rounds: 1,
    revision: true,
    research: false,
    failover: true,
    temperature: 0.7,
    synthesizer: { role: "synthesizer", ref: { model: "" } },
  },
  workflowId: null,
  research: false,
  preferFree: true,
  live: null,
  busy: false,

  bootstrap: async () => {
    const auth = await apiGet("/auth/status");
    const wizard = await apiGet("/wizard/status");
    set({ passwordRequired: auth.password_required && !auth.authenticated,
          setupCompleted: wizard.setup_completed });
    const [projects, providers, ptypes, models, workflows, tools] = await Promise.all([
      apiGet<Project[]>("/projects"),
      apiGet<ProviderInstance[]>("/providers"),
      apiGet<ProviderType[]>("/providers/types"),
      apiGet<ModelInfo[]>("/models"),
      apiGet<Workflow[]>("/workflows"),
      apiGet<ToolDescriptor[]>("/tools"),
    ]);
    const projectId = projects[0]?.id || null;
    set({ projects, providers, providerTypes: ptypes, models, workflows, tools, projectId });
    if (projectId) await get().setProject(projectId);
    // default single model
    const firstAvail = models.find((m) => m.available);
    if (firstAvail) set({ singleRef: firstAvail.uid! });
  },

  refreshProviders: async () => {
    const [providers, models] = await Promise.all([
      apiGet<ProviderInstance[]>("/providers"),
      apiGet<ModelInfo[]>("/models"),
    ]);
    set({ providers, models });
  },

  refreshModels: async () => {
    set({ models: await apiGet<ModelInfo[]>("/models") });
  },

  refreshWorkflows: async () => {
    set({ workflows: await apiGet<Workflow[]>("/workflows") });
  },

  refreshTools: async () => {
    set({ tools: await apiGet<ToolDescriptor[]>("/tools") });
  },

  setProject: async (id) => {
    set({ projectId: id, conversationId: null, messages: [] });
    const convs = await apiGet<Conversation[]>(`/projects/${id}/conversations`);
    set({ conversations: convs });
    if (convs[0]) await get().selectConversation(convs[0].id);
  },

  newConversation: async (mode = get().mode) => {
    const pid = get().projectId!;
    const conv = await apiPost<Conversation>("/conversations", {
      project_id: pid, title: "New conversation", mode, config: {},
    });
    set((s) => ({ conversations: [conv, ...s.conversations], conversationId: conv.id,
                  messages: [], mode }));
    return conv.id;
  },

  selectConversation: async (id) => {
    const msgs = await apiGet<Message[]>(`/conversations/${id}/messages`);
    const conv = get().conversations.find((c) => c.id === id);
    set({ conversationId: id, messages: msgs, live: null,
          mode: (conv?.mode as Mode) || get().mode });
  },

  deleteConversation: async (id) => {
    await apiDelete(`/conversations/${id}`);
    const convs = get().conversations.filter((c) => c.id !== id);
    set({ conversations: convs });
    if (get().conversationId === id) {
      if (convs[0]) await get().selectConversation(convs[0].id);
      else set({ conversationId: null, messages: [] });
    }
  },

  setMode: (m) => set({ mode: m }),
  setCouncil: (c) => set((s) => ({ council: { ...s.council, ...c } })),

  loadDefaultCouncil: async () => {
    const cfg = await apiGet<CouncilDefinition>("/wizard/default-council?size=3");
    set({ council: cfg });
  },

  renameConversation: async (id, title) => {
    await apiPatch(`/conversations/${id}`, { title });
    set((s) => ({ conversations: s.conversations.map((c) => (c.id === id ? { ...c, title } : c)) }));
  },

  startRun: async (question, files) => {
    const s = get();
    if (!s.conversationId) await s.newConversation();
    const cid = get().conversationId!;
    if (files?.length) {
      for (const f of files) await uploadFile(s.projectId!, f);
    }
    const mode = get().mode;
    let config: any = { research: get().research, prefer_free: get().preferFree };
    if (mode === "single") {
      config.ref = uidToRef(get().singleRef);
    } else if (mode === "router") {
      // nothing else needed
    } else if (mode === "council") {
      config = { ...get().council, research: get().research, prefer_free: get().preferFree };
    } else if (mode === "workflow") {
      const wf = get().workflows.find((w) => w.id === get().workflowId);
      if (!wf) { alert("Choose a saved workflow first"); return; }
      config = { ...wf.definition, research: get().research };
    }
    set({ busy: true });
    const { run_id } = await apiPost<{ run_id: string }>(
      `/conversations/${cid}/runs`, { question, mode, config });
    const live = emptyLive(run_id, mode, question);
    set({ live });
    subscribeRun(run_id, set, get, cid);
  },
}));

function subscribeRun(runId: string, set: any, get: any, cid: string) {
  const es = new EventSource(eventSourceUrl(`/runs/${runId}/events`));
  const finish = async (ok: boolean) => {
    es.close();
    set({ busy: false });
    const msgs = await apiGet<Message[]>(`/conversations/${cid}/messages`);
    set({ messages: msgs });
    const convs = await apiGet<Conversation[]>(
      `/projects/${get().projectId}/conversations`);
    set({ conversations: convs });
  };
  const handler = (name: string) => (e: MessageEvent) => {
    const evt = JSON.parse(e.data);
    const live = get().live;
    if (!live || live.runId !== runId) return;
    const next: LiveRun = { ...live, nodes: { ...live.nodes }, nodeOrder: [...live.nodeOrder],
                            rounds: [...live.rounds], events: [...live.events, evt] };
    switch (name) {
      case "run_started":
        next.status = "running"; break;
      case "routing_plan":
        next.plan = evt; break;
      case "workflow_started":
        next.rounds.push({ round: -1, title: `Workflow stages: ${evt.levels.map((l: any) => l.join("+")).join(" → ")}`, status: "running" });
        break;
      case "round_started":
        next.rounds.push({ round: evt.round, title: evt.title || `Round ${evt.round + 1}`, status: "running" });
        break;
      case "round_finished": {
        const r = [...next.rounds].reverse().find((x) => x.status === "running");
        if (r) r.status = "done";
        break;
      }
      case "agent_started": {
        const n = upsertNode(next, evt.node_id, {
          label: evt.label, role: evt.role, provider: evt.provider,
          provider_label: evt.provider_label, model: evt.model,
          status: "running", attempt: evt.attempt, fallback: evt.fallback,
        });
        if (evt.fallback && !n.logs.some((l) => l.message.includes("failover"))) {
          n.logs.push({ level: "warn", message: `Failover → ${evt.provider_label}/${evt.model}` });
        }
        break;
      }
      case "agent_log":
        upsertNode(next, evt.node_id, {}).logs.push({ level: evt.level, message: evt.message });
        break;
      case "cooldown_started": {
        const n = upsertNode(next, evt.node_id ?? `${evt.provider}/${evt.model}`, {
          label: `${evt.provider}/${evt.model}`, provider: evt.provider, model: evt.model,
          status: "running" });
        n.logs.push({ level: "error",
          message: `Rate limited — cooldown ${Math.ceil(evt.retry_after)}s: ${evt.message}` });
        break;
      }
      case "cooldown_skip": {
        const n = upsertNode(next, evt.node_id ?? `skip-${evt.provider}/${evt.model}`, {
          label: `${evt.provider}/${evt.model} (cooling)`, provider: evt.provider,
          model: evt.model, status: "failed" });
        n.logs.push({ level: "warn", message: `Skipped (cooldown): ${evt.message}` });
        break;
      }
      case "tool_started": {
        const n = upsertNode(next, evt.node_id, {});
        n.tools.push({ name: evt.tool, status: "running" });
        break;
      }
      case "tool_finished": {
        const n = upsertNode(next, evt.node_id, {});
        const t = [...n.tools].reverse().find((x) => x.name === evt.tool && x.status === "running");
        const target = t || n.tools[n.tools.length - 1];
        if (target) { target.status = evt.ok ? "done" : "failed"; target.output = evt.output;
                      target.error = evt.error; target.latency_ms = evt.latency_ms; }
        break;
      }
      case "agent_finished": {
        upsertNode(next, evt.node_id, {
          label: evt.label, provider: evt.provider, model: evt.model,
          status: "done", text: evt.text, latency_ms: evt.latency_ms,
          usage: evt.usage, attempt: evt.attempt, fallback: evt.fallback,
        });
        break;
      }
      case "agent_failed": {
        const n = upsertNode(next, evt.node_id, { label: evt.label, status: "failed" });
        n.logs.push({ level: "error", message: (evt.errors || []).join(" | ") });
        break;
      }
      case "synthesis_started":
        upsertNode(next, "synthesis", { label: "Synthesizer", role: "synthesizer",
                                        provider: evt.provider, status: "running" });
        break;
      case "synthesis_fallback":
        upsertNode(next, "synthesis", {}).logs.push({ level: "warn", message: evt.message });
        break;
      case "final_chunk":
        next.final += evt.text; break;
      case "run_finished":
        next.status = "ok"; next.final = evt.final || next.final;
        next.totals = evt.totals; next.contributors = evt.contributors;
        set({ live: next });
        finish(true); return;
      case "run_failed":
        next.status = "failed"; next.error = evt.error;
        set({ live: next });
        finish(false); return;
    }
    set({ live: next });
  };
  [
    "run_started", "routing_plan", "workflow_started", "round_started", "round_finished",
    "agent_started", "agent_log", "cooldown_started", "cooldown_skip",
    "tool_started", "tool_finished", "agent_finished", "agent_failed",
    "synthesis_started", "synthesis_fallback", "final_chunk",
    "run_finished", "run_failed",
  ].forEach((t) => es.addEventListener(t, handler(t) as any));
  es.onerror = () => { /* browser auto-retries; run status reconciles on finish */ };
}
