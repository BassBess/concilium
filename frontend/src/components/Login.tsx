import { useState } from "react";
import { apiPost, setToken } from "../api";

export default function Login({ onAuthed }: { onAuthed: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { token } = await apiPost<{ token: string }>("/auth/login", { password });
      setToken(token);
      onAuthed();
    } catch (e: any) {
      setError(e.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand">
          <div className="logo">◈</div>
          <div>
            <h1>Concilium</h1>
            <small>multi-model AI orchestration</small>
          </div>
        </div>
        <label className="field">
          <span>Password</span>
          <input type="password" value={password} autoFocus
            onChange={(e) => setPassword(e.target.value)} placeholder="Server password" />
        </label>
        {error && <div className="error-box" style={{ marginBottom: 10 }}>{error}</div>}
        <button className="btn primary" style={{ width: "100%", justifyContent: "center" }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
