import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPut } from "../api";
import { Toggle } from "../components/ui";

export default function SettingsPage() {
  const [settings, setSettings] = useState<any>(null);
  const navigate = useNavigate();

  useEffect(() => { apiGet("/settings/app").then(setSettings); }, []);
  if (!settings) return <div className="content muted small">Loading…</div>;

  const save = async (patch: any) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    await apiPut("/settings/app", patch);
  };

  return (
    <>
      <div className="topbar"><h2>Settings</h2></div>
      <div className="content grid" style={{ gridTemplateColumns: "1fr 1fr", maxWidth: 1000 }}>
        <div className="card">
          <h3>Routing &amp; orchestration defaults</h3>
          <label className="field"><span>Default mode</span>
            <select value={settings.default_mode}
              onChange={(e) => save({ default_mode: e.target.value })}>
              <option value="single">Single model</option>
              <option value="router">Auto-route</option>
              <option value="council">Council</option>
              <option value="workflow">Workflow</option>
            </select>
          </label>
          <label className="field"><span>Default council size</span>
            <input type="number" min={1} max={12} defaultValue={settings.default_council_size}
              onBlur={(e) => save({ default_council_size: Number(e.target.value) })} /></label>
          <label className="field"><span>Global concurrency</span>
            <input type="number" min={1} max={32} defaultValue={settings.default_concurrency}
              onBlur={(e) => save({ default_concurrency: Number(e.target.value) })} /></label>
          <div className="row gap mt8">
            <Toggle checked={settings.prefer_free} onChange={(v) => save({ prefer_free: v })} />
            <span className="small">Prefer free / local models when routing</span>
          </div>
          <div className="row gap mt8">
            <Toggle checked={settings.failover} onChange={(v) => save({ failover: v })} />
            <span className="small">Automatic failover across healthy models</span>
          </div>
        </div>

        <div className="card">
          <h3>Setup &amp; security</h3>
          <button className="btn" onClick={() => navigate("/wizard")}>🧙 Re-run setup wizard</button>
          <div className="small muted mt16" style={{ lineHeight: 1.7 }}>
            <b>Credentials</b> are encrypted at rest (Fernet/AES) and never sent to the browser —
            only a masked “configured” indicator. All model calls happen server-side.<br /><br />
            <b>Code execution</b> is disabled by default; enable with
            <code> CONCILIUM_ENABLE_PYTHON_SANDBOX=true</code>. It runs in a resource-limited
            subprocess; use Docker for strong isolation.<br /><br />
            <b>Login</b>: set <code>CONCILIUM_PASSWORD</code> to require a password for the UI/API.<br /><br />
            <b>SSRF</b> requests to private/link-local/metadata IPs are blocked; no arbitrary host
            commands are executed; uploaded documents are treated as UTF-8 text and presented to
            models as untrusted external content.
          </div>
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h3>About</h3>
          <div className="small muted">
            Concilium is a universal multi-model AI orchestration layer: one interface over many
            providers, local models and tools — with parallel councils, critique/debate rounds,
            workflows, smart routing, honest rate-limit handling with cooldowns and failover, full
            execution transparency and persistent project memory.
          </div>
        </div>
      </div>
    </>
  );
}
