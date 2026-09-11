import { useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPatch, fmtMs, fmtMoney, fmtNum, uidToRef } from "../api";
import CouncilPanel from "../components/CouncilPanel";
import { Markdown } from "../components/Markdown";
import ModelPicker, { fetchLiveModels } from "../components/ModelPicker";
import { Badge, Dot, EmptyState, Modal, Spinner, Toggle, ToggleChip } from "../components/ui";
import { useStore, type Mode } from "../store";

const ROLES = [
  "researcher", "explorer", "critic", "skeptic", "programmer", "mathematician",
  "fact_checker", "summarizer", "planner", "proof_reviewer", "creative",
  "synthesizer", "judge",
];

const MODES: { id: Mode; icon: string; label: string; hint: string }[] = [
  { id: "single", icon: "🤖", label: "Single model", hint: "One chosen model, with automatic failover" },
  { id: "router", icon: "🎯", label: "Auto-route", hint: "Router picks the best model for each task" },
  { id: "council", icon: "👥", label: "Council", hint: "Parallel models, critique rounds, synthesis" },
  { id: "workflow", icon: "🔀", label: "Workflow", hint: "Reusable staged pipelines" },
];

export default function ChatPage() {
  const s = useStore();
  const [input, setInput] = useState("");
  const [showConfig, setShowConfig] = useState(true);
  const [showPanel, setShowPanel] = useState(true);
  const [traceRun, setTraceRun] = useState<any>(null);
  const [routingPreview, setRoutingPreview] = useState<any>(null);
  const [attachments, setAttachments] = useState<File[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (s.mode === "council" && s.council.participants.length === 0) {
      s.loadDefaultCouncil();
    }
    fetchLiveModels(true).catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [s.messages.length, s.live?.final, s.live?.nodeOrder.length]);

  const live = s.live;
  const visibleMessages = useMemo(() => s.messages.filter((m) => m.role !== "agent"), [s.messages]);
  const conv = s.conversations.find((c) => c.id === s.conversationId);

  const send = async () => {
    const q = input.trim();
    if (!q || s.busy) return;
    setInput("");
    const files = attachments;
    setAttachments([]);
    try {
      await s.startRun(q, files);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const addParticipant = async () => {
    const groups = await fetchLiveModels();
    const g = groups.find((x) => x.models.length);
    const uid = g ? `${g.instance_id}:${g.models[0].id}` : "";
    s.setCouncil({
      participants: [...s.council.participants, {
        label: `Agent ${s.council.participants.length + 1}`,
        role: "researcher", ref: uid ? uidToRef(uid) : { model: "" }, allow_tools: true,
      }],
    });
  };

  return (
    <>
      <div className="topbar">
        <h2>{conv?.title || "New conversation"}</h2>
        <span className="sub">{MODES.find((m) => m.id === s.mode)?.hint}</span>
        <span style={{ flex: 1 }} />
        {conv && (
          <button className="btn ghost sm"
            onClick={() => {
              const title = prompt("Conversation title", conv.title);
              if (title) s.renameConversation(conv.id, title);
            }}>✎ Rename</button>
        )}
        <button className="btn ghost sm" onClick={() => setShowPanel(!showPanel)}>
          {showPanel ? "▶ Hide trace" : "◀ Show trace"}
        </button>
      </div>

      <div className="chat-layout">
        <div className="chat-main">
          <div className="messages" ref={scrollRef}>
            {visibleMessages.length === 0 && !live && (
              <EmptyState icon="◈" title="Ask anything — one model or a whole council">
                Pick a mode below. Council mode questions several models in parallel, lets them
                critique each other, and synthesizes a final answer you can fully inspect.
              </EmptyState>
            )}
            {visibleMessages.map((m) => (
              <MessageView key={m.id} m={m} onTrace={() => openTrace(m.run_id, setTraceRun)} />
            ))}
            {live && (
              <>
                <div className="msg-row user">
                  <div className="avatar user">U</div>
                  <div className="bubble">{live.question}</div>
                </div>
                <LiveAnswer />
              </>
            )}
          </div>

          <div className="composer-wrap">
            <div className="composer-inner">
              <div className="mode-bar">
                {MODES.map((m) => (
                  <button key={m.id} className={`mode-tab ${s.mode === m.id ? "active" : ""}`}
                    onClick={() => s.setMode(m.id)}>
                    {m.icon} {m.label}
                  </button>
                ))}
                <span style={{ flex: 1 }} />
                <ToggleChip active={s.research} onClick={() => useStore.setState({ research: !s.research })}
                  title="Run a web search first (requires a search tool in Tools settings)">
                  🔎 Deep research
                </ToggleChip>
                <ToggleChip active={s.preferFree} onClick={() => useStore.setState({ preferFree: !s.preferFree })}
                  title="Prefer free/local models when routing">
                  🆓 Prefer free
                </ToggleChip>
                <button className="btn ghost sm" onClick={() => setShowConfig(!showConfig)}>
                  ⚙ Configure {showConfig ? "▾" : "▸"}
                </button>
              </div>

              {showConfig && (
                <div className="config-panel">
                  {s.mode === "single" && (
                    <div className="participant-row">
                      <span className="small muted" style={{ minWidth: 90 }}>Model</span>
                      <ModelPicker value={s.singleRef}
                        onChange={(uid) => useStore.setState({ singleRef: uid })} />
                    </div>
                  )}
                  {s.mode === "router" && (
                    <div className="row between">
                      <span className="small muted">
                        The analyzer detects coding, math, vision, reasoning, long-context and research
                        intent, then ranks every healthy model by fit, speed, free-tier and cost.
                      </span>
                      <button className="btn sm" onClick={async () => {
                        if (!input.trim()) { alert("Type your question first"); return; }
                        const plan = await (await fetch(`/api/router/preview`, {
                          method: "POST", headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ text: input, prefer_free: s.preferFree }),
                        })).json();
                        setRoutingPreview(plan);
                      }}>🧮 Preview routing</button>
                    </div>
                  )}
                  {s.mode === "workflow" && <WorkflowConfig />}
                  {s.mode === "council" && <CouncilConfig addParticipant={addParticipant} />}
                </div>
              )}

              <div className="composer-box">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
                  placeholder={
                    s.mode === "council"
                      ? "Ask the council… models answer in parallel, critique, revise, and synthesize (Ctrl+Enter)"
                      : "Send a message… (Ctrl+Enter)"}
                  rows={1}
                />
                <input ref={fileRef} type="file" multiple hidden
                  accept=".txt,.md,.markdown,.csv,.json,.log,.py,.js,.ts,.tsx,.yaml,.yml,.html,.css"
                  onChange={(e) => setAttachments([...(e.target.files || [])])} />
                <button className="btn ghost" title="Attach text documents to project memory"
                  onClick={() => fileRef.current?.click()}>📎</button>
                <button className="btn primary" disabled={s.busy || !input.trim()} onClick={send}>
                  {s.busy ? <Spinner /> : "Send"}
                </button>
              </div>
              <div className="small muted mt8" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                {attachments.map((f, i) => (
                  <span key={i} className="badge local">
                    📄 {f.name}
                    <span style={{ cursor: "pointer", marginLeft: 6 }}
                      onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>✕</span>
                  </span>
                ))}
                {attachments.length === 0 && "Failover, cooldowns and retries are automatic."}
              </div>
            </div>
          </div>
        </div>

        {showPanel && (
          <CouncilPanel
            events={live?.events || []}
            final={live?.final || ""}
            status={live?.status || null}
            totals={live?.totals}
            contributors={live?.contributors}
            error={live?.error}
            plan={live?.plan}
            onClose={() => setShowPanel(false)}
          />
        )}
      </div>

      {traceRun && (
        <Modal wide title={`Execution trace · ${traceRun.mode} run`} onClose={() => setTraceRun(null)}>
          <div style={{ height: "70vh", overflow: "hidden", display: "flex", border: "1px solid var(--border)", borderRadius: 10 }}>
            <div style={{ flex: 1, overflowY: "auto" }}>
              <CouncilPanel
                events={traceRun.trace?.events || []}
                final={traceRun.final || ""}
                status={traceRun.status}
                totals={traceRun.trace?.totals}
                contributors={traceRun.trace?.contributors}
                error={traceRun.error}
              />
            </div>
          </div>
        </Modal>
      )}

      {routingPreview && (
        <Modal title="Routing decision" onClose={() => setRoutingPreview(null)}
          footer={<button className="btn" onClick={() => setRoutingPreview(null)}>Close</button>}>
          <div className="small muted mb8">Detected: {routingPreview.profile?.reasons?.join(" · ")}</div>
          <table className="data">
            <thead><tr><th>Rank</th><th>Model</th><th>Score</th><th>Why</th></tr></thead>
            <tbody>
              {routingPreview.plan?.map((p: any, i: number) => (
                <tr key={p.uid}>
                  <td>{i + 1}{p.cooling && <Badge kind="err"> cooling</Badge>}</td>
                  <td><b>{p.provider_label}</b><div className="muted small">{p.model}</div></td>
                  <td>{p.score}</td>
                  <td className="small muted">{p.reasons.join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
    </>
  );
}

async function openTrace(runId?: string | null, set?: any) {
  if (!runId) return;
  const run = await apiGet(`/runs/${runId}`);
  set(run);
}

function MessageView({ m, onTrace }: { m: any; onTrace: () => void }) {
  const isUser = m.role === "user";
  return (
    <div className={`msg-row ${isUser ? "user" : ""}`}>
      <div className={`avatar ${isUser ? "user" : "ai"}`}>{isUser ? "U" : "◈"}</div>
      <div style={{ minWidth: 0 }}>
        {!isUser && (
          <div className="msg-meta">
            <span className="who">Synthesized answer</span>
            {m.meta?.mode && <Badge>{m.meta.mode}</Badge>}
            {m.latency_ms ? <span className="stat">⏱ {fmtMs(m.latency_ms)}</span> : null}
            {(m.input_tokens + m.output_tokens) > 0 && (
              <span className="stat">
                {fmtNum(m.input_tokens + m.output_tokens)} tok
                {m.provider ? ` · ${m.provider}` : ""}
              </span>
            )}
            {m.cost > 0 && <span className="stat">{fmtMoney(m.cost)}</span>}
            {m.run_id && <button className="btn ghost sm" onClick={onTrace}>🔬 Inspect trace</button>}
          </div>
        )}
        <div className="bubble">
          <Markdown text={m.content} />
          {!isUser && m.meta?.contributors?.length > 0 && (
            <div className="mt8 tag-group">
              {m.meta.contributors.map((c: any, i: number) => (
                <span className="contributor-chip" key={i}
                  title={`role: ${c.role} · ${c.output_tokens} out tokens`}>
                  {c.label || c.model} · {c.provider}/{c.model}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function LiveAnswer() {
  const live = useStore((s) => s.live)!;
  const running = live.status === "running";
  return (
    <div className="msg-row">
      <div className="avatar ai">{running ? <Spinner /> : "◈"}</div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="msg-meta">
          <span className="who">
            {live.mode === "council" ? "Council synthesis" :
              live.mode === "workflow" ? "Workflow result" :
              live.mode === "router" ? "Auto-routed answer" : "Answer"}
          </span>
          {running && live.final === "" ? <span className="stat">orchestrating…</span> : null}
        </div>
        <div className="bubble">
          {live.final ? <Markdown text={live.final} /> :
            live.status === "failed" ? <div className="error-box">{live.error}</div> :
            <span className="muted small">
              {live.nodeOrder.some((n) => live.nodes[n]?.status === "running")
                ? "Models are working — watch the trace panel →" : "Starting…"}
            </span>}
          {running && live.final && <span className="dot pulse" style={{ background: "var(--accent)", marginLeft: 6 }} />}
        </div>
      </div>
    </div>
  );
}

function CouncilConfig({ addParticipant }: { addParticipant: () => void }) {
  const s = useStore();
  const c = s.council;
  const update = (patch: any) => s.setCouncil(patch);
  const updateP = (i: number, patch: any) => {
    const participants = c.participants.map((p, j) => (j === i ? { ...p, ...patch } : p));
    update({ participants });
  };

  return (
    <div>
      {c.participants.map((p, i) => (
        <div className="participant-row" key={i}>
          <input style={{ width: 130 }} placeholder="Label"
            value={p.label} onChange={(e) => updateP(i, { label: e.target.value })} />
          <ModelPicker
            value={p.ref.provider_id ? `${p.ref.provider_id}:${p.ref.model}` : ""}
            onChange={(_uid, _m, instanceId) => {
              const [, ...rest] = _uid.split(":");
              updateP(i, { ref: { provider_id: instanceId, model: rest.join(":") } });
            }}
          />
          <select style={{ width: 150 }} value={p.role} onChange={(e) => updateP(i, { role: e.target.value })}>
            {ROLES.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
          </select>
          <ToggleChip active={!!p.allow_tools} onClick={() => updateP(i, { allow_tools: !p.allow_tools })}
            title="Allow tools (web search, calculator, knowledge base) on tool-capable models">🔧</ToggleChip>
          <button className="btn ghost sm danger"
            onClick={() => update({ participants: c.participants.filter((_, j) => j !== i) })}>✕</button>
        </div>
      ))}
      <div className="row wrap gap mt8">
        <button className="btn sm" onClick={addParticipant}>＋ Add participant</button>
        <button className="btn sm" onClick={() => s.loadDefaultCouncil()}>✨ Auto-build council</button>
        <span style={{ flex: 1 }} />
        <label className="small muted row gap">
          Parallel <Toggle checked={c.parallel} onChange={(v) => update({ parallel: v })} />
        </label>
        <label className="small muted row gap">
          Critique rounds
          <input style={{ width: 60 }} type="number" min={0} max={5}
            value={c.critique_rounds}
            onChange={(e) => update({ critique_rounds: Number(e.target.value) })} />
        </label>
        <label className="small muted row gap">
          Revisions <Toggle checked={c.revision} onChange={(v) => update({ revision: v })} />
        </label>
      </div>
      <div className="participant-row mt16">
        <span className="small muted" style={{ minWidth: 90 }}>Synthesizer</span>
        <ModelPicker
          value={c.synthesizer.ref.provider_id ? `${c.synthesizer.ref.provider_id}:${c.synthesizer.ref.model}` : ""}
          onChange={(_uid, _m, instanceId) => {
            const [, ...rest] = _uid.split(":");
            update({ synthesizer: { role: "synthesizer", ref: { provider_id: instanceId, model: rest.join(":") } } });
          }}
        />
        <span className="small muted">Fails over automatically if unavailable.</span>
      </div>
    </div>
  );
}

function WorkflowConfig() {
  const s = useStore();
  const wf = s.workflows.find((w) => w.id === s.workflowId);
  return (
    <div>
      <div className="participant-row">
        <select value={s.workflowId || ""} onChange={(e) => useStore.setState({ workflowId: e.target.value })}>
          <option value="">— choose a saved workflow —</option>
          {s.workflows.map((w) => <option key={w.id} value={w.id}>{w.builtin ? "★ " : ""}{w.name}</option>)}
        </select>
      </div>
      {wf && (
        <>
          <div className="small muted mb8">{wf.description}</div>
          <div className="flow">
            {(wf.definition as any).nodes?.map((n: any, i: number, arr: any[]) => (
              <span key={n.id} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <span className="flow-node">
                  {n.role.replace("_", " ")} ×{n.count || 1}
                  {n.terminal ? " ⛳" : ""}{n.tools ? " 🔧" : ""}
                </span>
                {i < arr.length - 1 && <span className="arrow">→</span>}
              </span>
            ))}
            {(wf.definition as any).participants && (
              <span className="flow-node">
                👥 council · {(wf.definition as any).participants.length} agents ·{" "}
                {(wf.definition as any).critique_rounds} critique rounds → synthesizer
              </span>
            )}
          </div>
          <div className="small muted mt8">
            Build and save your own workflows on the Workflows page.
          </div>
        </>
      )}
    </div>
  );
}
