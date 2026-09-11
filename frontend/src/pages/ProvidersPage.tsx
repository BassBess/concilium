import { useState } from "react";
import { apiGet, apiPatch, apiPost, apiDelete } from "../api";
import { Badge, Dot, Modal, Spinner, Toggle } from "../components/ui";
import { useStore } from "../store";
import type { ProviderInstance, ProviderType } from "../types";

const LIMIT_FIELDS = [
  { key: "rpm", label: "Requests/min" },
  { key: "tpm", label: "Tokens/min (0=off)" },
  { key: "daily_requests", label: "Daily request cap" },
  { key: "concurrency", label: "Concurrency" },
  { key: "cooldown_seconds", label: "Cooldown (s)" },
  { key: "max_retries", label: "Retries" },
];

export default function ProvidersPage() {
  const { providers, providerTypes, refreshProviders } = useStore();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [addOpen, setAddOpen] = useState(false);
  const [health, setHealth] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState<string>("");

  const test = async (p: ProviderInstance) => {
    setBusy(p.id);
    try {
      const h = await apiGet(`/providers/${p.id}/health`);
      setHealth((x) => ({ ...x, [p.id]: h }));
    } finally { setBusy(""); }
  };

  const refreshModels = async (p: ProviderInstance) => {
    setBusy(p.id + ":models");
    try {
      const models = await apiGet(`/providers/${p.id}/models?refresh=1`);
      setHealth((x) => ({ ...x, [p.id]: { status: "ok", models: models.length,
        detail: `Discovered ${models.length} models: ${models.slice(0, 6).map((m: any) => m.id).join(", ")}${models.length > 6 ? "…" : ""}` } }));
      await refreshProviders();
    } catch (e: any) {
      setHealth((x) => ({ ...x, [p.id]: { status: "error", detail: e.message } }));
    } finally { setBusy(""); }
  };

  return (
    <>
      <div className="topbar">
        <h2>Providers</h2>
        <span className="sub">Credentials are encrypted at rest and only used in server-side requests.</span>
        <span style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => setAddOpen(true)}>＋ Add provider / endpoint</button>
      </div>
      <div className="content">
        <div className="card-grid">
          {providers.map((p) => (
            <ProviderCard key={p.id} p={p}
              open={!!expanded[p.id]}
              health={health[p.id]}
              busy={busy === p.id || busy === p.id + ":models"}
              onToggle={() => setExpanded((x) => ({ ...x, [p.id]: !x[p.id] }))}
              onTest={() => test(p)}
              onRefresh={() => refreshModels(p)}
              onChange={() => refreshProviders()} />
          ))}
        </div>
      </div>
      {addOpen && <AddProviderModal types={providerTypes} onClose={() => setAddOpen(false)}
        onSaved={() => { setAddOpen(false); refreshProviders(); }} />}
    </>
  );
}

function ProviderCard({ p, open, health, busy, onToggle, onTest, onRefresh, onChange }: {
  p: ProviderInstance; open: boolean; health?: any; busy: boolean;
  onToggle: () => void; onTest: () => void; onRefresh: () => void; onChange: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [keyDraft, setKeyDraft] = useState<Record<string, string>>({});

  const status = p.enabled && p.configured ? "ok" : p.configured ? "off" : "unconfigured";
  const save = async () => {
    const settings: Record<string, any> = {};
    for (const [k, v] of Object.entries(draft)) if (v !== "" && v != null) settings[k] = Number.isNaN(Number(v)) ? v : Number(v);
    const apiKey = keyDraft.api_key;
    await apiPatch(`/providers/${p.id}`, {
      enabled: p.enabled, settings,
      api_key: apiKey || undefined,
      base_url: draft.base_url !== undefined ? draft.base_url : undefined,
    });
    setDraft({}); setKeyDraft({});
    onChange();
  };

  const setEnabled = async (v: boolean) => {
    await apiPatch(`/providers/${p.id}`, { enabled: v });
    onChange();
  };

  return (
    <div className="card">
      <div className="row between">
        <div className="row gap">
          {p.kind === "local" ? "🏠" : "☁️"}
          <div>
            <h3>{p.label}</h3>
            <div className="muted small">{p.type} ·{" "}
              {status === "ok" ? <><Dot color="green" /> available</>
                : status === "off" ? <><Dot color="amber" /> configured, disabled</>
                : <><Dot color="gray" /> unconfigured</>}
            </div>
          </div>
        </div>
        <Toggle checked={p.enabled} onChange={setEnabled} />
      </div>

      <div className="row wrap gap mt8">
        <Badge kind={p.kind === "local" ? "local" : ""}>{p.kind}</Badge>
        {p.configured && <Badge kind="ok">key configured</Badge>}
        {p.builtin && <Badge>built-in</Badge>}
        {p.signup_url && <a href={p.signup_url} target="_blank" rel="noreferrer" className="small">get key ↗</a>}
        {p.docs_url && <a href={p.docs_url} target="_blank" rel="noreferrer" className="small">docs ↗</a>}
      </div>

      {health && (
        <div className={`mt8 small ${health.status === "ok" ? "" : "error-box"}`}>
          {health.status === "ok"
            ? `✅ ${health.detail || `OK — ${health.models} models`}`
            : `⚠️ ${health.detail || health.status}`}
        </div>
      )}

      <div className="row gap mt8">
        <button className="btn sm" onClick={onTest} disabled={busy}>{busy ? <Spinner /> : "🩺 Test"}</button>
        <button className="btn sm" onClick={onRefresh} disabled={busy}>🔄 Discover models</button>
        <button className="btn sm ghost" onClick={onToggle}>{open ? "Hide settings ▾" : "Settings ▸"}</button>
        {!p.builtin && (
          <button className="btn sm danger" style={{ marginLeft: "auto" }}
            onClick={async () => { if (confirm(`Delete ${p.label}?`)) { await apiDelete(`/providers/${p.id}`); onChange(); } }}>
            Delete
          </button>
        )}
      </div>

      {open && (
        <div className="mt16">
          {p.secret_fields.includes("api_key") && (
            <label className="field">
              <span>API key{(p.settings.api_key as any)?.set ? " — set (enter to replace)" : ""}</span>
              <input type="password" placeholder={(p.settings.api_key as any)?.masked || "sk-…"}
                value={keyDraft.api_key || ""}
                onChange={(e) => setKeyDraft({ api_key: e.target.value })} />
            </label>
          )}
          {p.setting_fields.includes("base_url") && (
            <label className="field">
              <span>Base URL</span>
              <input placeholder={p.type === "ollama" ? "http://localhost:11434" : "https://…/v1"}
                value={draft.base_url ?? (typeof p.settings.base_url === "string" ? p.settings.base_url : "")}
                onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} />
            </label>
          )}
          <div className="small muted mb8">Local scheduling / quota limits (free-tier conservative defaults)</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {LIMIT_FIELDS.map((f) => (
              <label className="field" key={f.key} style={{ margin: 0 }}>
                <span>{f.label}</span>
                <input type="number" placeholder={String(p.settings[f.key] ?? "")}
                  value={draft[f.key] ?? ""}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
              </label>
            ))}
          </div>
          <button className="btn primary sm mt8" onClick={save}>Save settings</button>
        </div>
      )}
    </div>
  );
}

function AddProviderModal({ types, onClose, onSaved }: {
  types: ProviderType[]; onClose: () => void; onSaved: () => void;
}) {
  const [type, setType] = useState("openai_compatible");
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [err, setErr] = useState("");

  const meta = types.find((t) => t.type === type);
  const create = async () => {
    try {
      const r = await apiPost("/providers", {
        type, label: label || meta?.display_name || type,
        base_url: baseUrl || undefined, api_key: apiKey || undefined, enabled,
      });
      if ((r as any)?.id) onSaved();
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <Modal title="Add provider or OpenAI-compatible endpoint" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={create}>Add</button></>}>
      <label className="field">
        <span>Provider type</span>
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {types.filter((t) => t.supports_multiple_instances).map((t) =>
            <option key={t.type} value={t.type}>{t.display_name}</option>)}
        </select>
      </label>
      <div className="small muted">
        Built-in providers (OpenAI, Anthropic, Google, Groq, …) are pre-registered — enable them
        from the cards behind this dialog. Use this form for additional OpenAI-compatible servers:
        vLLM, llama.cpp, LocalAI, text-generation-webui, gateway proxies, etc.
      </div>
      <label className="field mt16"><span>Label</span>
        <input value={label} placeholder={meta?.display_name} onChange={(e) => setLabel(e.target.value)} /></label>
      {meta?.setting_fields.includes("base_url") && (
        <label className="field"><span>Base URL</span>
          <input value={baseUrl} placeholder={meta?.default_base_url || "http://localhost:8080/v1"}
            onChange={(e) => setBaseUrl(e.target.value)} /></label>
      )}
      {meta?.secret_fields.includes("api_key") && (
        <label className="field"><span>API key (optional for local servers)</span>
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></label>
      )}
      <label className="row gap small muted"><Toggle checked={enabled} onChange={setEnabled} /> enabled</label>
      {err && <div className="error-box mt8">{err}</div>}
    </Modal>
  );
}
