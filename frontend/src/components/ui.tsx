import React from "react";

export function Badge({ children, kind = "", title }: { children: any; kind?: string; title?: string }) {
  return <span className={`badge ${kind}`} title={title}>{children}</span>;
}

export function Dot({ color = "gray", pulse }: { color?: "green" | "red" | "amber" | "gray"; pulse?: boolean }) {
  return <span className={`dot ${color}${pulse ? " pulse" : ""}`} />;
}

export function Spinner() { return <span className="spinner" />; }

export function Modal({ title, onClose, children, wide, footer }: {
  title: string; onClose: () => void; children: React.ReactNode; wide?: boolean;
  footer?: React.ReactNode;
}) {
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " wide" : ""}`}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="btn ghost sm" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="switch" title={label}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="slider" />
    </label>
  );
}

export function ToggleChip({ active, onClick, children, title }: {
  active: boolean; onClick: () => void; children: any; title?: string;
}) {
  return (
    <button className="toggle-chip" onClick={onClick} title={title}
      style={active ? { borderColor: "var(--accent)", color: "#fff", background: "rgba(109,141,255,0.16)" } : {}}>
      {children}
    </button>
  );
}

export function EmptyState({ icon, title, children }: { icon: string; title: string; children?: any }) {
  return (
    <div style={{ textAlign: "center", color: "var(--text-faint)", padding: "70px 20px" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>{icon}</div>
      <div style={{ fontSize: 15, color: "var(--text-dim)", marginBottom: 6 }}>{title}</div>
      <div className="small">{children}</div>
    </div>
  );
}

export function CapBadges({ caps }: { caps: string[] }) {
  const nice: Record<string, string> = {
    vision: "👁 vision", reasoning: "🧠 reasoning", tools: "🔧 tools", code: "💻 code",
    structured: "{} json", long_context: "📜 long-ctx", embeddings: "embeddings",
    image_gen: "🎨 image", local: "🏠 local", chat: "chat",
  };
  return (
    <div className="tag-group">
      {caps.filter((c) => c !== "chat").map((c) => (
        <Badge key={c} kind="cap">{nice[c] || c}</Badge>
      ))}
    </div>
  );
}
