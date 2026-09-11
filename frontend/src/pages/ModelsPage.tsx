import { useMemo, useState } from "react";
import { fmtMoney, fmtNum } from "../api";
import { Badge, CapBadges, Dot } from "../components/ui";
import { useStore } from "../store";

const CAP_FILTERS = ["vision", "reasoning", "code", "tools", "long_context", "structured"];

export default function ModelsPage() {
  const models = useStore((s) => s.models);
  const providers = useStore((s) => s.providers);
  const [q, setQ] = useState("");
  const [cap, setCap] = useState("");
  const [onlyAvailable, setOnlyAvailable] = useState(false);

  const providerName = (m: any) =>
    providers.find((p) => p.id === m.instance_id)?.display_name || m.provider_name || m.provider;

  const filtered = useMemo(() => models.filter((m) => {
    if (onlyAvailable && !m.available) return false;
    if (cap && !m.capabilities.includes(cap)) return false;
    if (q && !`${m.id} ${m.provider}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [models, q, cap, onlyAvailable]);

  const counts = {
    total: models.length,
    available: models.filter((m) => m.available).length,
    free: models.filter((m) => m.free_tier).length,
  };

  return (
    <>
      <div className="topbar">
        <h2>Model registry</h2>
        <span className="sub">{counts.available}/{counts.total} available · {counts.free} free-tier/local</span>
      </div>
      <div className="content">
        <div className="card mb16" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <input style={{ maxWidth: 260 }} placeholder="Search model…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select style={{ maxWidth: 220 }} value={cap} onChange={(e) => setCap(e.target.value)}>
            <option value="">All capabilities</option>
            {CAP_FILTERS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <label className="row gap small muted">
            <input type="checkbox" checked={onlyAvailable} onChange={(e) => setOnlyAvailable(e.target.checked)} />
            available only
          </label>
          <span style={{ flex: 1 }} />
          <span className="muted small">Prices are list-price estimates; live usage is measured per call.</span>
        </div>

        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="data">
            <thead>
              <tr>
                <th>Model</th><th>Provider</th><th>Capabilities</th>
                <th>Context</th><th>In/Out $/1M</th><th>Tier</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((m) => (
                <tr key={m.uid || `${m.provider_type}-${m.id}`}>
                  <td>
                    <b>{m.name}</b>
                    {m.description && <div className="muted small">{m.description}</div>}
                  </td>
                  <td className="small">{providerName(m)}</td>
                  <td><CapBadges caps={m.capabilities} /></td>
                  <td className="small">{fmtNum(m.context_window)}{m.max_output ? <div className="muted">out {fmtNum(m.max_output)}</div> : null}</td>
                  <td className="small">
                    {m.input_price_per_1m == null ? <span className="muted">unknown</span>
                      : `${fmtMoney(m.input_price_per_1m)} / ${fmtMoney(m.output_price_per_1m)}`}
                  </td>
                  <td>
                    <div className="tag-group">
                      {m.free_tier && <Badge kind="free">free</Badge>}
                      {m.tier === "local" ? <Badge kind="local">local</Badge> : <Badge>remote</Badge>}
                    </div>
                  </td>
                  <td>
                    {m.available ? <span className="row gap small"><Dot color="green" pulse /> ready</span>
                      : m.configured ? <span className="row gap small"><Dot color="amber" /> disabled</span>
                      : <span className="row gap small muted"><Dot color="gray" /> no key</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
