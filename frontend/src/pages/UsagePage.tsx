import { useEffect, useState } from "react";
import { apiPost, fmtMoney, fmtMs, fmtNum } from "../api";
import { Badge, Dot } from "../components/ui";

export default function UsagePage() {
  const [stats, setStats] = useState<any>(null);
  const [quotas, setQuotas] = useState<any[]>([]);

  const load = async () => {
    setStats(await (await fetch("/api/usage/stats?days=30")).json());
    setQuotas(await (await fetch("/api/usage/quotas")).json());
  };
  useEffect(() => { load(); }, []);

  if (!stats) return <div className="content muted small">Loading…</div>;
  const days = Object.entries(stats.by_day).sort(([a], [b]) => a.localeCompare(b));
  const maxReq = Math.max(1, ...days.map(([, v]: any) => v.requests));
  const cooling = quotas.filter((q) => q.in_cooldown);

  const resetCooldown = async (key?: string) => {
    await apiPost("/usage/reset", { key, cooldowns_only: true });
    load();
  };

  return (
    <>
      <div className="topbar">
        <h2>Usage &amp; cost</h2>
        <span className="sub">Live request accounting, free-tier consumption, cooldowns and failures.</span>
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => resetCooldown()}>Clear all cooldowns</button>
      </div>
      <div className="content">
        <div className="stat-tiles">
          <Tile label="Requests" value={fmtNum(stats.totals.requests)} />
          <Tile label="Successful" value={fmtNum(stats.totals.success)} />
          <Tile label="Rate limits" value={fmtNum(stats.totals.ratelimit)} warn={stats.totals.ratelimit > 0} />
          <Tile label="Errors" value={fmtNum(stats.totals.errors + stats.totals.retries)} warn={stats.totals.errors > 0} />
          <Tile label="Tokens" value={fmtNum(stats.totals.input_tokens + stats.totals.output_tokens)} />
          <Tile label="Est. cost" value={fmtMoney(stats.totals.cost)} />
          <Tile label="Avg latency" value={fmtMs(stats.totals.avg_latency_ms)} />
          <Tile label="In cooldown" value={String(cooling.length)} warn={cooling.length > 0} />
        </div>

        {cooling.length > 0 && (
          <>
            <div className="section-title">Active cooldowns</div>
            <div className="card" style={{ padding: 0 }}>
              <table className="data">
                <thead><tr><th>Model</th><th>Reason</th><th>Remaining</th><th></th></tr></thead>
                <tbody>
                  {cooling.map((c) => (
                    <tr key={c.key}>
                      <td className="mono small">{c.key}</td>
                      <td className="small">{c.cooldown_reason}</td>
                      <td><Badge kind="warn">{c.cooldown_remaining}s</Badge></td>
                      <td><button className="btn sm" onClick={() => resetCooldown(c.key)}>Reset</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className="section-title">Requests per day (30d)</div>
        <div className="card">
          <div className="bars">
            {days.map(([d, v]: any) => (
              <div className="bar" key={d} style={{ height: `${(v.requests / maxReq) * 100}%` }}
                title={`${d}: ${v.requests} requests, ${v.errors} errors`} />
            ))}
          </div>
          <div className="small muted mt8">{days[0]?.[0]} → {days[days.length - 1]?.[0]}</div>
        </div>

        <div className="section-title">By provider</div>
        <div className="card" style={{ padding: 0 }}>
          <table className="data">
            <thead><tr><th>Provider</th><th>Requests</th><th>Tokens in/out</th><th>Est. cost</th><th>Errors / limits</th><th>Avg latency</th></tr></thead>
            <tbody>
              {Object.entries(stats.by_provider).map(([k, v]: any) => (
                <tr key={k}>
                  <td><Dot color={v.errors ? "amber" : "green"} /> <b>{k}</b></td>
                  <td>{v.requests}</td>
                  <td className="small">{fmtNum(v.input_tokens)} / {fmtNum(v.output_tokens)}</td>
                  <td>{fmtMoney(v.cost)}</td>
                  <td>{v.errors}{v.ratelimit ? <Badge kind="warn" >{v.ratelimit} rl</Badge> : null}</td>
                  <td>{fmtMs(v.avg_latency_ms)}</td>
                </tr>
              ))}
              {Object.keys(stats.by_provider).length === 0 && (
                <tr><td colSpan={6} className="muted small">No traffic yet — run a question.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="section-title">By model</div>
        <div className="card" style={{ padding: 0 }}>
          <table className="data">
            <thead><tr><th>Model</th><th>Requests</th><th>Tokens</th><th>Est. cost</th><th>Errors</th></tr></thead>
            <tbody>
              {Object.entries(stats.by_model).map(([k, v]: any) => (
                <tr key={k}>
                  <td className="mono small">{k}</td>
                  <td>{v.requests}</td>
                  <td className="small">{fmtNum(v.input_tokens + v.output_tokens)}</td>
                  <td>{fmtMoney(v.cost)}</td>
                  <td>{v.errors || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted mt16">
          Costs are estimates from published list prices (or zero for free/local models when pricing
          is unknown). Actual billed amounts appear on your provider dashboards.
        </p>
      </div>
    </>
  );
}

function Tile({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="stat-tile">
      <div className="v" style={{ color: warn ? "var(--amber)" : undefined }}>{value}</div>
      <div className="l">{label}</div>
    </div>
  );
}
