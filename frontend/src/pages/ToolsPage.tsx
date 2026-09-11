import { useState } from "react";
import { apiPost, apiPatch } from "../api";
import { Badge, Dot, Spinner, Toggle } from "../components/ui";
import { useStore } from "../store";

export default function ToolsPage() {
  const tools = useStore((s) => s.tools);
  const refresh = useStore((s) => s.refreshTools);
  const [testInput, setTestInput] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Record<string, any>>({});
  const [busyKey, setBusyKey] = useState("");

  const setEnabled = async (key: string, v: boolean) => {
    await apiPatch(`/tools/${key}`, { enabled: v });
    await refresh();
  };

  const saveSettings = async (t: any, settings: any) => {
    await apiPatch(`/tools/${t.key}`, { settings });
    await refresh();
  };

  const invoke = async (key: string, projectId?: string) => {
    setBusyKey(key);
    try {
      let args: any = {};
      const raw = testInput[key];
      if (raw) { try { args = JSON.parse(raw); } catch { args = { expression: raw, query: raw, code: raw }; } }
      const r = await apiPost(`/tools/${key}/invoke`, { arguments: args, project_id: projectId });
      setResult((x) => ({ ...x, [key]: r }));
    } catch (e: any) {
      setResult((x) => ({ ...x, [key]: { ok: false, error: e.message } }));
    } finally { setBusyKey(""); }
  };

  return (
    <>
      <div className="topbar">
        <h2>Tools</h2>
        <span className="sub">First-class, permission-gated capabilities. External content is always labeled untrusted.</span>
      </div>
      <div className="content">
        <div className="card-grid">
          {tools.map((t) => (
            <div className="card" key={t.key}>
              <div className="row between">
                <h3>{t.display_name}</h3>
                <Toggle checked={t.enabled} onChange={(v) => setEnabled(t.key, v)} />
              </div>
              <div className="tag-group mt8">
                <Badge>{t.category}</Badge>
                {t.enabled && t.available && <Badge kind="ok"><Dot color="green" /> available</Badge>}
                {t.enabled && !t.available && <Badge kind="warn">needs configuration</Badge>}
              </div>
              <p className="muted small">{t.description}</p>
              {t.enabled && !t.available && (
                <div className="error-box" style={{ padding: "8px 10px" }}>{t.unavailable_reason}</div>
              )}

              {t.key === "web_search" && (
                <div className="mt8">
                  <label className="field"><span>Tavily API key (free tier @ tavily.com)</span>
                    <input type="password" placeholder={t.settings.tavily_api_key?.masked || "tvly-…"}
                      onBlur={(e) => e.target.value && saveSettings(t, { tavily_api_key: e.target.value })} /></label>
                  <label className="field"><span>or self-hosted SearXNG URL</span>
                    <input placeholder="https://search.example.org"
                      onBlur={(e) => e.target.value && saveSettings(t, { searxng_url: e.target.value })} /></label>
                </div>
              )}

              {t.permissions && Object.keys(t.permissions).length > 0 && (
                <div className="small muted mt8">Permissions: {JSON.stringify(t.permissions)}</div>
              )}

              <details className="mt8">
                <summary className="small muted" style={{ cursor: "pointer" }}>Test invocation</summary>
                <textarea className="mt8" rows={3} placeholder={
                  t.key === "calculator" ? "e.g. sqrt(2)*8" :
                  t.key === "web_search" ? "query JSON, e.g. {\"query\": \"latest AI news\"}" :
                  t.key === "python_sandbox" ? "print(2**10)" : "{}"
                } value={testInput[t.key] || ""} onChange={(e) => setTestInput({ ...testInput, [t.key]: e.target.value })} />
                <button className="btn sm mt8" disabled={!t.enabled || busyKey === t.key} onClick={() => invoke(t.key)}>
                  {busyKey === t.key ? <Spinner /> : "Run"}
                </button>
                {result[t.key] && (
                  <pre className="small mt8" style={{ whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}>
                    {result[t.key].ok ? result[t.key].output : `ERROR: ${result[t.key].error}`}
                  </pre>
                )}
              </details>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
