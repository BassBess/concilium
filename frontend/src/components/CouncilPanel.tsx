import { useMemo, useState } from "react";
import { fmtMoney, fmtMs, fmtNum } from "../api";
import { Markdown } from "./Markdown";
import { Badge, Dot, Spinner } from "./ui";
import type { TraceEvent } from "../types";

interface TNode {
  nodeId: string; label: string; role?: string; provider?: string; model?: string;
  provider_label?: string; status: string; attempt?: number; fallback?: boolean;
  text?: string; latency_ms?: number; usage?: any; logs: { level: string; message: string }[];
  tools: { name: string; status: string; output?: string; error?: string; latency_ms?: number }[];
}
interface TRound { title: string; status: string; nodeIds: string[]; }

function buildTimeline(events: TraceEvent[]) {
  const nodes: Record<string, TNode> = {};
  const rounds: TRound[] = [];
  let plan: any = null;
  const ensureNode = (id: string, label?: string): TNode => {
    if (!nodes[id]) nodes[id] = { nodeId: id, label: label || id, status: "waiting", logs: [], tools: [] };
    if (label) nodes[id].label = label;
    return nodes[id];
  };
  for (const e of events) {
    switch (e.type) {
      case "round_started":
      case "workflow_started":
        rounds.push({
          title: e.title || e.levels?.map((l: string[]) => l.join("+")).join(" → ") || "Stage",
          status: "running", nodeIds: [],
        });
        break;
      case "round_finished": {
        const r = [...rounds].reverse().find((x) => x.status === "running");
        if (r) r.status = "done";
        break;
      }
      case "agent_started": {
        const n = ensureNode(e.node_id, e.label);
        n.status = "running"; n.role = e.role; n.provider = e.provider;
        n.model = e.model; n.provider_label = e.provider_label;
        n.attempt = e.attempt; n.fallback = e.fallback;
        rounds.length && rounds[rounds.length - 1].nodeIds.push(e.node_id);
        break;
      }
      case "agent_finished": {
        const n = ensureNode(e.node_id, e.label);
        n.status = "done"; n.text = e.text; n.latency_ms = e.latency_ms;
        n.usage = e.usage; n.provider = e.provider; n.model = e.model; n.fallback = e.fallback;
        break;
      }
      case "agent_failed": {
        const n = ensureNode(e.node_id, e.label);
        n.status = "failed";
        n.logs.push({ level: "error", message: (e.errors || []).join(" | ") });
        break;
      }
      case "agent_log":
        ensureNode(e.node_id, e.label).logs.push({ level: e.level, message: e.message });
        break;
      case "cooldown_started": {
        const id = e.node_id || `cd-${e.provider}/${e.model}`;
        const n = ensureNode(id, `${e.provider}/${e.model}`);
        n.logs.push({ level: "error",
          message: `⏳ rate-limited — cooldown ${Math.ceil(e.retry_after)}s` });
        rounds.length && rounds[rounds.length - 1].nodeIds.push(id);
        break;
      }
      case "cooldown_skip": {
        const id = e.node_id || `skip-${e.provider}/${e.model}`;
        const n = ensureNode(id, `${e.provider}/${e.model} (cooled)`);
        n.status = "failed";
        n.logs.push({ level: "warn", message: e.message });
        rounds.length && rounds[rounds.length - 1].nodeIds.push(id);
        break;
      }
      case "tool_started": {
        const n = ensureNode(e.node_id);
        n.tools.push({ name: e.tool, status: "running" });
        break;
      }
      case "tool_finished": {
        const n = ensureNode(e.node_id);
        const t = [...n.tools].reverse().find((x) => x.name === e.tool && x.status === "running");
        const target = t || n.tools[n.tools.length - 1];
        if (target) { target.status = e.ok ? "done" : "failed"; target.output = e.output;
                      target.error = e.error; target.latency_ms = e.latency_ms; }
        break;
      }
      case "synthesis_started": {
        rounds.push({ title: "Synthesis", status: "running", nodeIds: [] });
        const n = ensureNode("synthesis", "Synthesizer");
        n.status = "running"; n.role = "synthesizer"; n.provider = e.provider;
        rounds[rounds.length - 1].nodeIds.push("synthesis");
        break;
      }
      case "synthesis_fallback":
        ensureNode("synthesis").logs.push({ level: "warn", message: e.message });
        break;
      case "routing_plan":
        plan = e;
        rounds.push({ title: "Routing decision", status: "done", nodeIds: ["routing"] });
        break;
    }
  }
  // de-duplicate nodeIds within rounds
  for (const r of rounds) r.nodeIds = [...new Set(r.nodeIds)];
  return { nodes, rounds, plan };
}

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <Spinner />;
  if (status === "done") return <Dot color="green" />;
  if (status === "failed") return <Dot color="red" />;
  return <Dot color="gray" />;
}

function AgentCard({ n }: { n: TNode }) {
  const [open, setOpen] = useState(n.nodeId === "synthesis");
  const failed = n.logs.some((l) => l.level === "error");
  return (
    <div className={`agent-card ${n.status}${failed && n.status !== "failed" ? " failed" : ""}`}>
      <div className="agent-head" onClick={() => setOpen(!open)}>
        <StatusIcon status={n.status} />
        <div className="name">
          {n.label}
          <small>
            {n.provider || "?"}{n.model ? ` / ${n.model}` : ""}
            {n.attempt ? ` · attempt ${n.attempt + 1}` : ""}
          </small>
        </div>
        <div className="agent-stats">
          {n.fallback && <Badge kind="warn" title="Failover model">failover</Badge>}
          {n.status === "done" && n.latency_ms ? <span>⏱ {fmtMs(n.latency_ms)}</span> : null}
          {n.status === "done" && n.usage ? (
            <span title={`in ${n.usage.input} / out ${n.usage.output} tokens`}>
              {fmtNum(n.usage.input + n.usage.output)} tok
              {n.usage.estimated ? "~" : ""}
            </span>
          ) : null}
          {n.status === "done" && n.usage?.cost > 0 && <span>{fmtMoney(n.usage.cost)}</span>}
          <span>{open ? "▾" : "▸"}</span>
        </div>
      </div>
      {n.tools.map((t, i) => (
        <div className="tool-pill" key={i}>
          {t.status === "running" ? <Spinner /> : t.status === "done" ? "✅" : "⚠️"}
          🔧 {t.name}{t.latency_ms ? ` · ${fmtMs(t.latency_ms)}` : ""}
        </div>
      ))}
      {n.logs.map((l, i) => (
        <div className={`agent-log ${l.level}`} key={i}>• {l.message}</div>
      ))}
      {open && n.text && (
        <div className="agent-body"><Markdown text={n.text} /></div>
      )}
      {open && n.tools.map((t, i) => t.output && (
        <div className="agent-body" key={`o${i}`} style={{ maxHeight: 160 }}>
          <div className="muted small">🔧 {t.name} result</div>{t.output}
        </div>
      ))}
    </div>
  );
}

export default function CouncilPanel({ events, final, status, totals, contributors, error,
                                       plan, onClose }: {
  events: TraceEvent[]; final: string; status: string | null; totals?: any;
  contributors?: any[]; error?: string; plan?: any; onClose?: () => void;
}) {
  const view = useMemo(() => buildTimeline(events), [events]);
  const routingPlan = plan || view.plan;

  return (
    <aside className="council-panel">
      <div className="council-head">
        <span>🔬</span>
        <h3>Execution trace</h3>
        {status === "running" ? <Spinner /> : status === "failed" ? <Dot color="red" /> : <Dot color="green" />}
        {onClose && <button className="btn ghost sm" onClick={onClose}>✕</button>}
      </div>
      <div className="council-body">
        {routingPlan?.selected && (
          <>
            <div className="round-label">🎯 Routed to</div>
            <div className="agent-card done">
              <div className="agent-head" style={{ cursor: "default" }}>
                <Dot color="green" />
                <div className="name">
                  {routingPlan.selected.provider_label} · {routingPlan.selected.model}
                  <small>{routingPlan.profile?.reasons?.join(" · ")}</small>
                </div>
              </div>
            </div>
          </>
        )}
        {view.rounds.length === 0 && (
          <div className="muted small" style={{ padding: 20, textAlign: "center" }}>
            Live orchestration detail appears here — per-model status, latency,
            tokens, retries, cooldowns, critique rounds and synthesis.
          </div>
        )}
        {view.rounds.map((r, ri) => (
          <div key={ri}>
            <div className="round-label">
              {r.status === "running" ? <Spinner /> : r.status === "done" ? "✅" : "•"}
              {r.title}
            </div>
            {r.nodeIds.includes("routing") && routingPlan?.selected && (
              <div className="small muted" style={{ padding: "0 4px 8px" }}>
                score {routingPlan.selected.score} · {routingPlan.selected.reasons.slice(0, 3).join(" · ")}
              </div>
            )}
            {[...new Set(r.nodeIds)].filter((id) => id !== "routing").map((id) => {
              const n = view.nodes[id];
              return n ? <AgentCard key={id} n={n} /> : null;
            })}
          </div>
        ))}

        {(final || status === "running") && events.some((e) => e.type === "final_chunk") && (
          <>
            <div className="round-label">📜 Final answer {status === "running" && <Spinner />}</div>
            <div className="agent-card done">
              <div className="agent-body" style={{ maxHeight: "none" }}>
                <Markdown text={final} />
              </div>
            </div>
          </>
        )}
        {error && <div className="error-box mt8">{error}</div>}
      </div>
      {totals && (
        <div className="totals-bar">
          <div>Model calls <b>{totals.calls}</b></div>
          <div>Tokens <b>{fmtNum((totals.input_tokens || 0) + (totals.output_tokens || 0))}</b></div>
          <div>Est. cost <b>{fmtMoney(totals.cost)}</b></div>
          <div>Contributors <b>{(contributors || []).length}</b></div>
        </div>
      )}
    </aside>
  );
}
