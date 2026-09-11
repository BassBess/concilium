import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiPost, apiPatch, apiDelete } from "../api";
import { Badge, Modal } from "../components/ui";
import { useStore } from "../store";
import type { Workflow, WorkflowNode } from "../types";

const ROLES = [
  "researcher", "explorer", "critic", "skeptic", "programmer", "mathematician",
  "fact_checker", "summarizer", "planner", "proof_reviewer", "creative",
  "synthesizer", "judge",
];

function blankWorkflow(): Partial<Workflow> {
  return {
    name: "My workflow",
    description: "",
    definition: {
      nodes: [
        { id: "research", role: "researcher", count: 2 },
        { id: "critique", role: "critic", count: 1, depends_on: ["research"] },
        { id: "final", role: "synthesizer", count: 1, depends_on: ["critique", "research"], terminal: true },
      ],
    },
  };
}

export default function WorkflowsPage() {
  const workflows = useStore((s) => s.workflows);
  const refresh = useStore((s) => s.refreshWorkflows);
  const setStore = useStore.setState;
  const [selectedId, setSelectedId] = useState<string | null>(workflows[0]?.id || null);
  const [draft, setDraft] = useState<Partial<Workflow> | null>(null);
  const navigate = useNavigate();

  const selected = workflows.find((w) => w.id === selectedId);
  const editing: Partial<Workflow> | null = draft || selected || null;

  const run = (w: Workflow) => {
    const mode = (w.definition as any).participants ? "council" : "workflow";
    setStore({
      workflowId: w.id || null,
      mode: mode as any,
      council: (w.definition as any).participants ? { ...(w.definition as any) } : useStore.getState().council,
    });
    navigate("/");
  };

  const save = async () => {
    if (!editing) return;
    if (editing.id) {
      await apiPatch(`/workflows/${editing.id}`, {
        name: editing.name, description: editing.description, definition: editing.definition,
      });
    } else {
      await apiPost("/workflows", editing);
    }
    setDraft(null);
    await refresh();
  };

  const remove = async (w: Workflow) => {
    if (!w.id || w.builtin) return;
    if (!confirm(`Delete workflow "${w.name}"?`)) return;
    await apiDelete(`/workflows/${w.id}`);
    setSelectedId(null);
    await refresh();
  };

  const duplicate = async (w: Workflow) => {
    await apiPost("/workflows", { ...w, name: w.name + " (copy)", builtin: false });
    await refresh();
  };

  const nodes: WorkflowNode[] = (editing?.definition as any)?.nodes || [];
  const setNodes = (fn: (n: WorkflowNode[]) => WorkflowNode[]) =>
    setDraft({ ...editing!, definition: { ...(editing!.definition as any), nodes: fn(nodes) } });

  return (
    <>
      <div className="topbar">
        <h2>Workflows</h2>
        <span className="sub">Reusable multi-agent pipelines: parallel fan-out, critique, judging, synthesis.</span>
        <span style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => { setDraft(blankWorkflow()); setSelectedId(null); }}>
          ＋ New workflow
        </button>
      </div>
      <div className="content" style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div className="card" style={{ width: 280, flexShrink: 0, padding: 8 }}>
          {workflows.map((w) => (
            <div key={w.id}
              className={`conv-item ${(draft?.id || selectedId) === w.id ? "active" : ""}`}
              onClick={() => { setDraft(null); setSelectedId(w.id!); }}
              style={{ display: "flex", flexDirection: "column", alignItems: "stretch" }}>
              <div className="row between">
                <b className="small">{w.builtin ? "★ " : ""}{w.name}</b>
              </div>
              <div className="muted" style={{ fontSize: 11 }}>{w.description}</div>
            </div>
          ))}
        </div>

        {editing ? (
          <div className="card" style={{ flex: 1 }}>
            <div className="row gap">
              <input style={{ maxWidth: 320 }} value={editing.name || ""}
                disabled={selected?.builtin}
                onChange={(e) => setDraft({ ...editing, name: e.target.value })} />
              {selected?.builtin && <Badge>built-in — duplicate to edit</Badge>}
              <span style={{ flex: 1 }} />
              <button className="btn sm" onClick={() => run(editing as Workflow)}>▶ Run in chat</button>
              {selected?.builtin ? (
                <button className="btn sm" onClick={() => setDraft({
                  ...selected, id: undefined, name: selected.name + " (copy)",
                  definition: JSON.parse(JSON.stringify(selected.definition)),
                })}>Duplicate to edit</button>
              ) : (
                <button className="btn primary sm" onClick={save}>Save</button>
              )}
              {selected && !selected.builtin && (
                <button className="btn sm danger" onClick={() => remove(selected)}>Delete</button>
              )}
            </div>
            <input className="mt8" placeholder="Description" value={editing.description || ""}
              onChange={(e) => setDraft({ ...editing, description: e.target.value })} />

            {(editing.definition as any).participants ? (
              <CouncilSummary w={editing} />
            ) : (
              <>
                <div className="section-title">Pipeline preview</div>
                <div className="flow">
                  {(() => {
                    const ordered = topo(nodes);
                    return ordered.map((lvl, i) => (
                      <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                        <span className="flow-node">{lvl.map((n) => `${n.role}×${n.count || 1}`).join(", ")}{lvl.some((n) => n.terminal) ? " ⛳" : ""}</span>
                        {i < ordered.length - 1 && <span className="arrow">→</span>}
                      </span>
                    ));
                  })()}
                </div>

                <div className="section-title">Nodes</div>
                <table className="data">
                  <thead><tr><th>Stage id</th><th>Role</th><th>Agents</th><th>Depends on</th><th>Flags</th><th></th></tr></thead>
                  <tbody>
                    {nodes.map((n, i) => (
                      <tr key={i}>
                        <td><input value={n.id} onChange={(e) => setNodes((ns) => ns.map((x, j) => j === i ? { ...x, id: e.target.value.replace(/\s/g, "_") } : x))} /></td>
                        <td>
                          <select value={n.role} onChange={(e) => setNodes((ns) => ns.map((x, j) => j === i ? { ...x, role: e.target.value } : x))}>
                            {ROLES.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
                          </select>
                        </td>
                        <td><input style={{ width: 70 }} type="number" min={1} max={8} value={n.count || 1}
                          onChange={(e) => setNodes((ns) => ns.map((x, j) => j === i ? { ...x, count: Number(e.target.value) } : x))} /></td>
                        <td>
                          <select multiple style={{ height: 56, minWidth: 150 }}
                            value={n.depends_on || []}
                            onChange={(e) => {
                              const vals = [...e.target.selectedOptions].map((o) => o.value);
                              setNodes((ns) => ns.map((x, j) => j === i ? { ...x, depends_on: vals } : x));
                            }}>
                            {nodes.filter((x) => x.id !== n.id).map((x) => <option key={x.id} value={x.id}>{x.id}</option>)}
                          </select>
                        </td>
                        <td className="small">
                          <label className="row gap"><input type="checkbox" checked={!!n.terminal}
                            onChange={(e) => setNodes((ns) => ns.map((x, j) => j === i ? { ...x, terminal: e.target.checked } : x))} /> terminal</label>
                          <label className="row gap"><input type="checkbox" checked={!!n.tools}
                            onChange={(e) => setNodes((ns) => ns.map((x, j) => j === i ? { ...x, tools: e.target.checked } : x))} /> tools</label>
                        </td>
                        <td><button className="btn ghost sm danger"
                          onClick={() => setNodes((ns) => ns.filter((_, j) => j !== i))}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button className="btn sm mt8"
                  onClick={() => setNodes((ns) => [...ns, { id: `stage_${ns.length + 1}`, role: "researcher", count: 1, depends_on: ns.map((x) => x.id).slice(-1) }])}>
                  ＋ Add stage
                </button>
                <div className="small muted mt8">
                  Nodes without an explicit model auto-route to the best available models with
                  provider diversity. Terminal nodes collect everything upstream for the final answer.
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="card muted small" style={{ flex: 1 }}>Select or create a workflow.</div>
        )}
      </div>
    </>
  );
}

function CouncilSummary({ w }: { w: any }) {
  return (
    <div className="mt16 col gap">
      <div className="small muted">This is a saved council preset ({w.participants?.length} participants).</div>
      <div className="flow">
        {(w.participants || []).map((p: any, i: number) => (
          <span key={i} style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
            <span className="flow-node">{p.label} · {p.role.replace("_", " ")}</span>
            {i < w.participants.length - 1 && <span className="arrow">∥</span>}
          </span>
        ))}
        <span className="arrow">→</span>
        <span className="flow-node">critique ×{w.critique_rounds}{w.revision ? " → revisions" : ""}</span>
        <span className="arrow">→</span>
        <span className="flow-node">synthesizer ⛳</span>
      </div>
    </div>
  );
}

function topo(nodes: WorkflowNode[]): WorkflowNode[][] {
  const done = new Set<string>();
  const levels: WorkflowNode[][] = [];
  let remaining = [...nodes];
  while (remaining.length) {
    const level = remaining.filter((n) => !(new Set(n.depends_on || [])).size ||
      (n.depends_on || []).every((d) => done.has(d)));
    if (!level.length) break;
    level.forEach((n) => done.add(n.id));
    levels.push(level);
    remaining = remaining.filter((n) => !done.has(n.id));
  }
  return levels;
}
