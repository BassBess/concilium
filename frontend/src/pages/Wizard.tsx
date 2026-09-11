import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPatch, apiPost, eventSourceUrl } from "../api";
import CouncilPanel from "../components/CouncilPanel";
import { Badge, Dot, Spinner } from "../components/ui";
import { useStore } from "../store";

const STEPS = ["Scope", "Providers", "Detect", "Connections", "Council", "Test", "Done"];

export default function Wizard() {
  const [step, setStep] = useState(0);
  const [scope, setScope] = useState<"both" | "local" | "remote">("both");
  const [providers, setProviders] = useState<any[]>([]);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [enableMap, setEnableMap] = useState<Record<string, boolean>>({});
  const [detect, setDetect] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [council, setCouncil] = useState<any>(null);
  const [testRun, setTestRun] = useState<any>({ events: [], final: "", status: null, totals: null });
  const [err, setErr] = useState("");
  const navigate = useNavigate();
  const bootstrap = useStore((s) => s.bootstrap);

  useEffect(() => { apiGet("/providers").then((p: any) => {
    setProviders(p);
    setEnableMap(Object.fromEntries(p.map((x: any) => [x.id, x.enabled])));
  }); }, []);

  const saveProviders = async () => {
    setBusy(true); setErr("");
    try {
      for (const p of providers) {
        const patch: any = {};
        if (keys[p.id]) patch.api_key = keys[p.id];
        const wantsEnabled = enableMap[p.id] || !!keys[p.id];
        if (wantsEnabled !== p.enabled) patch.enabled = wantsEnabled;
        if (Object.keys(patch).length) await apiPatch(`/providers/${p.id}`, patch);
      }
      await bootstrap();
      setProviders(await apiGet("/providers"));
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const runDetect = async () => {
    setBusy(true);
    const r = await apiPost("/wizard/detect", {
      enable_local: scope !== "remote", enable_remote: scope !== "local",
    });
    setDetect(r);
    setBusy(false);
  };

  const loadCouncil = async (size = 3) => {
    setCouncil(await apiGet(`/wizard/default-council?size=${size}`));
  };

  const runTest = async (question?: string) => {
    setTestRun({ events: [], final: "", status: "running" });
    const r = await apiPost("/wizard/test", {
      question: question || "What are the trade-offs between SQL and NoSQL? Be concise.",
    });
    const es = new EventSource(eventSourceUrl(`/runs/${r.run_id}/events`));
    const all: any[] = [];
    const wire = (type: string) => (e: MessageEvent) => {
      const evt = JSON.parse(e.data); evt.type = type; all.push(evt);
      if (type === "final_chunk") {
        setTestRun((x: any) => ({ ...x, events: [...all], final: x.final + evt.text }));
      } else if (type === "run_finished") {
        setTestRun({ events: [...all], final: evt.final, status: "ok", totals: evt.totals,
                     contributors: evt.contributors });
        es.close();
      } else if (type === "run_failed") {
        setTestRun((x: any) => ({ ...x, events: [...all], status: "failed" }));
        es.close();
      } else {
        setTestRun((x: any) => ({ ...x, events: [...all] }));
      }
    };
    ["run_started", "round_started", "round_finished", "agent_started", "agent_finished",
     "agent_failed", "agent_log", "cooldown_started", "synthesis_started", "final_chunk",
     "tool_started", "tool_finished", "run_finished", "run_failed"]
      .forEach((t) => es.addEventListener(t, wire(t) as any));
  };

  const finish = async () => {
    await apiPost("/wizard/complete", { setup_completed: true });
    await bootstrap();
    navigate("/");
  };

  const localProviders = providers.filter((p) => p.kind === "local");
  const remoteProviders = providers.filter((p) => p.kind === "remote");
  const toggle = (id: string, v: boolean) => setEnableMap((m) => ({ ...m, [id]: v }));

  return (
    <div className="wizard-shell">
      <div className="card">
        <div className="row between mb">
          <h2 style={{ margin: 0 }}>🧙 Concilium setup</h2>
          <button className="btn ghost sm" onClick={finish}>skip →</button>
        </div>
        <div className="steps">
          {STEPS.map((_, i) => (
            <div key={i} className={`step ${i === step ? "active" : i < step ? "done" : ""}`} />
          ))}
        </div>

        {step === 0 && (
          <div className="col gap">
            <p className="muted">How do you want to use Concilium? You can change everything later.</p>
            {[
              { id: "both", icon: "🌐", t: "Local + remote (recommended)",
                d: "Free local mock models now; add API keys and/or Ollama anytime." },
              { id: "local", icon: "🏠", t: "Local only",
                d: "Mock models plus Ollama / LM Studio. No account or key needed." },
              { id: "remote", icon: "☁️", t: "Remote APIs only",
                d: "Google/Groq free tiers, OpenAI, Anthropic, OpenRouter, etc." },
            ].map((o) => (
              <div key={o.id} className={`card ${scope === o.id ? "" : ""}`}
                style={{ cursor: "pointer", borderColor: scope === o.id ? "var(--accent)" : undefined,
                         background: scope === o.id ? "rgba(109,141,255,0.08)" : undefined }}
                onClick={() => setScope(o.id as any)}>
                <b>{o.icon} {o.t}</b>
                <div className="small muted">{o.d}</div>
              </div>
            ))}
          </div>
        )}

        {step === 1 && (
          <div className="col gap">
            <p className="small muted">
              Built-in <b>mock models</b> are already enabled so the app works offline. Add keys for
              any provider you use (stored encrypted, server-side only), or enable local servers.
            </p>
            {scope !== "remote" && (
              <>
                <div className="section-title" style={{ margin: "4px 0" }}>🏠 Local</div>
                {localProviders.map((p) => (
                  <ProviderKeyRow key={p.id} p={p} enabled={enableMap[p.id]}
                    onToggle={(v) => toggle(p.id, v)} />
                ))}
              </>
            )}
            {scope !== "local" && (
              <>
                <div className="section-title" style={{ margin: "4px 0" }}>☁️ Remote</div>
                {remoteProviders.map((p) => (
                  <ProviderKeyRow key={p.id} p={p}
                    keyVal={keys[p.id] || ""}
                    onKey={(v) => setKeys((k) => ({ ...k, [p.id]: v }))}
                    enabled={enableMap[p.id] || !!keys[p.id]}
                    onToggle={(v) => toggle(p.id, v)} />
                ))}
              </>
            )}
            {err && <div className="error-box">{err}</div>}
          </div>
        )}

        {step === 2 && (
          <div>
            <p className="small muted">Contacting enabled providers to list their models…</p>
            {!detect && <button className="btn primary" disabled={busy} onClick={runDetect}>
              {busy ? <Spinner /> : "🔍 Detect models now"}</button>}
            {detect && (
              <div className="col gap mt8">
                {detect.results.map((r: any) => (
                  <div key={r.id} className="row gap small">
                    {r.health.status === "ok" ? <Dot color="green" />
                      : r.health.status === "unconfigured" ? <Dot color="gray" />
                      : <Dot color="red" />}
                    <b style={{ minWidth: 160 }}>{r.label}</b>
                    <span className="muted">{r.models.length} models</span>
                    {r.models.length > 0 && (
                      <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.models.slice(0, 4).join(", ")}{r.models.length > 4 ? "…" : ""}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div>
            <p className="small muted">Connection tests — unconfigured providers are simply skipped.</p>
            <div className="col gap mt8">
              {(detect?.results || []).map((r: any) => (
                <div key={r.id} className="card" style={{ padding: 12 }}>
                  <div className="row gap">
                    {r.health.status === "ok" ? <Badge kind="ok">OK</Badge>
                      : r.health.status === "unconfigured" ? <Badge>skipped</Badge>
                      : <Badge kind="err">unreachable</Badge>}
                    <b>{r.label}</b>
                    <span className="small muted">{r.health.detail}</span>
                  </div>
                </div>
              ))}
              {!detect && <div className="muted small">Run detection on the previous step first.</div>}
            </div>
          </div>
        )}

        {step === 4 && (
          <div>
            <p className="small muted">
              Your default council is auto-composed from the best available, preferably free/local
              models — one diverse panel with a synthesizer. Adjust the size:
            </p>
            <div className="row gap mt8">
              {[2, 3, 4, 5].map((n) => (
                <button key={n} className={`btn ${council?.participants?.length === n ? "primary" : ""}`}
                  onClick={() => loadCouncil(n)}>{n} agents</button>
              ))}
            </div>
            {council && (
              <div className="mt16 col gap">
                {council.participants.map((p: any, i: number) => (
                  <div key={i} className="card" style={{ padding: 10 }}>
                    <b>{p.label}</b> <Badge kind="cap">{p.role}</Badge>
                  </div>
                ))}
                <div className="card" style={{ padding: 10 }}>
                  <b>Synthesizer</b> → {council.synthesizer.ref.provider_id?.slice(0, 8)}:{council.synthesizer.ref.model}
                </div>
                <div className="flow small muted">
                  independent answers → critique → revisions → synthesis
                </div>
              </div>
            )}
            {!council && <button className="btn primary mt16" onClick={() => loadCouncil(3)}>Compose council</button>}
          </div>
        )}

        {step === 5 && (
          <div>
            <p className="small muted">Run a real council end-to-end to see the system work.</p>
            {testRun.status === null && (
              <button className="btn primary" onClick={() => runTest()}>▶ Run test question</button>
            )}
            {testRun.status && (
              <div style={{ height: 440, marginTop: 12, border: "1px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
                <CouncilPanel events={testRun.events} final={testRun.final}
                  status={testRun.status} totals={testRun.totals}
                  contributors={testRun.contributors} />
              </div>
            )}
          </div>
        )}

        {step === 6 && (
          <div className="col gap">
            <div style={{ fontSize: 40 }}>✅</div>
            <h3 style={{ margin: 0 }}>Concilium is ready</h3>
            <ul className="small muted" style={{ lineHeight: 1.9 }}>
              <li>Start chatting in <b>Single</b>, <b>Auto-route</b>, <b>Council</b> or <b>Workflow</b> mode.</li>
              <li>Add keys anytime under <b>Providers</b>; rate limits trigger automatic cooldowns and failover.</li>
              <li>Build reusable pipelines under <b>Workflows</b>; track spend under <b>Usage</b>.</li>
              <li>Run Ollama locally for unlimited, private models alongside remote free tiers.</li>
            </ul>
          </div>
        )}

        <div className="row between mt24">
          <button className="btn" disabled={step === 0 || busy} onClick={() => setStep(step - 1)}>← Back</button>
          <div className="small muted">Step {step + 1} / {STEPS.length}</div>
          {step < STEPS.length - 1 ? (
            <button className="btn primary" disabled={busy} onClick={async () => {
              if (step === 0 && scope !== "local") setEnableMap((m) => ({ ...m }));
              if (step === 1) { await saveProviders(); setStep(step + 1); return; }
              if (step === 2 && !detect) { await runDetect(); }
              if (step === 3) { await loadCouncil(); }
              setStep(step + 1);
            }}>
              {busy ? <Spinner /> : "Next →"}
            </button>
          ) : (
            <button className="btn primary" onClick={finish}>Enter Concilium</button>
          )}
        </div>
      </div>
    </div>
  );
}

function ProviderKeyRow({ p, keyVal, onKey, enabled, onToggle }: {
  p: any; keyVal?: string; onKey?: (v: string) => void; enabled: boolean; onToggle: (v: boolean) => void;
}) {
  const needsKey = p.secret_fields.includes("api_key");
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="row gap">
        <label className="switch" title="Enable">
          <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
          <span className="slider" />
        </label>
        <div style={{ flex: 1 }}>
          <b>{p.kind === "local" ? "🏠" : "☁️"} {p.display_name}</b>
          {p.configured && !keyVal && <Badge kind="ok">configured</Badge>}
          <div className="small muted">
            {needsKey ? (p.signup_url ? <a href={p.signup_url} target="_blank" rel="noreferrer">get a key ↗</a> : "") : "no key needed"}
          </div>
        </div>
      </div>
      {needsKey && onKey && (
        <input className="mt8" type="password"
          placeholder={(p.settings.api_key as any)?.masked || (needsKey ? "paste API key" : "")}
          value={keyVal} onChange={(e) => onKey(e.target.value)} />
      )}
    </div>
  );
}
