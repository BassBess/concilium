import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost } from "../api";
import { Badge } from "../components/ui";
import type { PromptTemplate } from "../types";

const KINDS = ["system", "role", "workflow", "snippet"];

export default function PromptsPage() {
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [sel, setSel] = useState<PromptTemplate | null>(null);
  const [draft, setDraft] = useState<Partial<PromptTemplate>>({});

  const load = async () => setItems(await apiGet<PromptTemplate[]>("/prompts"));
  useEffect(() => { load(); }, []);

  const editing = draft && Object.keys(draft).length ? draft : sel;
  const save = async () => {
    if (!editing) return;
    if (editing.id) {
      await apiPatch(`/prompts/${editing.id}`, {
        name: editing.name, kind: editing.kind, content: editing.content });
    } else {
      await apiPost("/prompts", {
        name: editing.name, kind: editing.kind, content: editing.content });
    }
    setDraft({});
    await load();
  };

  return (
    <>
      <div className="topbar">
        <h2>Prompt library</h2>
        <span className="sub">Reusable system prompts, roles and workflow text. Variables: {"{{question}}"} etc.</span>
        <span style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => { setSel(null); setDraft({ name: "New template", kind: "system", content: "" }); }}>
          ＋ New template
        </button>
      </div>
      <div className="content" style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div className="card" style={{ width: 280, flexShrink: 0, padding: 8 }}>
          {items.map((t) => (
            <div key={t.id} className={`conv-item ${sel?.id === t.id && !Object.keys(draft).length ? "active" : ""}`}
              onClick={() => { setSel(t); setDraft({}); }}>
              <span className="title">{t.name}</span>
              <Badge>{t.kind}</Badge>
            </div>
          ))}
        </div>
        {editing ? (
          <div className="card" style={{ flex: 1 }}>
            <div className="row gap">
              <input style={{ maxWidth: 300 }} value={editing.name || ""}
                onChange={(e) => setDraft({ ...editing, name: e.target.value })} />
              <select style={{ maxWidth: 160 }} value={editing.kind}
                onChange={(e) => setDraft({ ...editing, kind: e.target.value })}>
                {KINDS.map((k) => <option key={k}>{k}</option>)}
              </select>
              <span style={{ flex: 1 }} />
              <button className="btn primary sm" onClick={save}>Save</button>
              {sel && <button className="btn sm danger" onClick={async () => {
                if (confirm("Delete template?")) { await apiDelete(`/prompts/${sel.id}`); setSel(null); load(); }
              }}>Delete</button>}
            </div>
            <textarea className="mt8" style={{ minHeight: 340, fontFamily: "var(--mono)", fontSize: 12.5 }}
              value={editing.content || ""}
              onChange={(e) => setDraft({ ...editing, content: e.target.value })} />
            <div className="mt8 small muted">
              Variables: {(editing as any).variables?.map?.((v: string) => <Badge key={v} kind="cap">{`{{${v}}}`}</Badge>)
                || detect(editing.content || "").map((v) => <Badge key={v} kind="cap">{`{{${v}}}`}</Badge>)}
              &nbsp;Supported everywhere: question, previous_answer, research, project_context, solutions, critiques.
            </div>
          </div>
        ) : (
          <div className="card muted small" style={{ flex: 1 }}>Select a template or create one.</div>
        )}
      </div>
    </>
  );
}

function detect(text: string) {
  return [...new Set([...text.matchAll(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g)].map((m) => m[1]))];
}
