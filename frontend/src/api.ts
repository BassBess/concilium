// Thin API client. All provider calls happen server-side; the browser only
// talks to this backend.

const TOKEN_KEY = "concilium_token";

export const getToken = () => localStorage.getItem(TOKEN_KEY) || "";
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(`/api${path}`, { ...options, headers });
  if (resp.status === 401) {
    clearToken();
    window.dispatchEvent(new CustomEvent("concilium:unauthenticated"));
    throw new ApiError(401, "Authentication required");
  }
  const text = await resp.text();
  const data = text ? JSON.parse(text) : {};
  if (!resp.ok) {
    throw new ApiError(resp.status, data.detail || resp.statusText);
  }
  return data as T;
}

export function eventSourceUrl(path: string) {
  const t = getToken();
  const sep = path.includes("?") ? "&" : "?";
  return `/api${path}${t ? `${sep}token=${encodeURIComponent(t)}` : ""}`;
}

// ---- Typed convenience wrappers ------------------------------------------

export const apiGet = <T = any>(path: string) => api<T>(path);
export const apiPost = <T = any>(path: string, body?: any) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
export const apiPatch = <T = any>(path: string, body?: any) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) });
export const apiPut = <T = any>(path: string, body?: any) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
export const apiDelete = <T = any>(path: string) => api<T>(path, { method: "DELETE" });

export function uploadFile(pid: string, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return api(`/projects/${pid}/documents`, { method: "POST", body: fd });
}

export function fmtNum(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

export function fmtMoney(n: number | undefined | null) {
  if (n == null) return "—";
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(5)}`;
  return `$${n.toFixed(3)}`;
}

export function fmtMs(ms: number) {
  if (!ms) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function uidToRef(uid: string) {
  // uid forms: "<instanceId>:<modelId>" or "type:<type>:<modelId>"
  if (uid.startsWith("type:")) {
    const rest = uid.slice(5);
    const i = rest.indexOf(":");
    return { provider_type: rest.slice(0, i), model: rest.slice(i + 1) };
  }
  const i = uid.indexOf(":");
  return { provider_id: uid.slice(0, i), model: uid.slice(i + 1) };
}
