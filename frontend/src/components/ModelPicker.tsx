import { useEffect, useState } from "react";
import { apiGet } from "../api";
import type { ModelInfo } from "../types";

interface LiveGroup {
  instance_id: string;
  provider: string;
  label: string;
  error?: string;
  models: ModelInfo[];
}

let cache: LiveGroup[] | null = null;
let inflight: Promise<LiveGroup[]> | null = null;

export async function fetchLiveModels(force = false): Promise<LiveGroup[]> {
  if (cache && !force) return cache;
  if (inflight && !force) return inflight;
  inflight = apiGet<LiveGroup[]>("/models/live").then((g) => {
    cache = g;
    return g;
  });
  return inflight;
}

export default function ModelPicker({ value, onChange, allowEmpty, disabled }: {
  value: string;
  onChange: (uid: string, model: ModelInfo, instanceId: string) => void;
  allowEmpty?: string;
  disabled?: boolean;
}) {
  const [groups, setGroups] = useState<LiveGroup[]>(cache || []);
  const [loading, setLoading] = useState(!cache);

  useEffect(() => {
    fetchLiveModels().then((g) => { setGroups(g); setLoading(false); });
  }, []);

  const usable = groups.filter((g) => g.models.length > 0);
  return (
    <select disabled={disabled} value={value}
      onChange={(e) => {
        const [instanceId, ...rest] = e.target.value.split(":");
        const modelId = rest.join(":");
        const grp = groups.find((g) => g.instance_id === instanceId);
        const model = grp?.models.find((m) => m.id === modelId);
        if (model) onChange(e.target.value, model, instanceId);
      }}>
      {allowEmpty !== undefined && <option value="">{allowEmpty || "— select model —"}</option>}
      {loading && <option>Loading models…</option>}
      {usable.length === 0 && !loading && <option value="">No live models — configure a provider</option>}
      {usable.map((g) => (
        <optgroup key={g.instance_id} label={`${g.label} (${g.provider})`}>
          {g.models.map((m) => (
            <option key={`${g.instance_id}:${m.id}`} value={`${g.instance_id}:${m.id}`}>
              {m.id}{m.free_tier ? " · free" : ""}
              {m.tier === "local" ? " · local" : ""}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
