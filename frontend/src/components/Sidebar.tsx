import { NavLink, useNavigate } from "react-router-dom";
import { useStore } from "../store";

const NAV = [
  { to: "/", icon: "💬", label: "Chat", end: true },
  { to: "/workflows", icon: "🔀", label: "Workflows" },
  { to: "/prompts", icon: "📝", label: "Prompts" },
  { to: "/models", icon: "🧩", label: "Models" },
  { to: "/providers", icon: "🔌", label: "Providers" },
  { to: "/tools", icon: "🛠️", label: "Tools" },
  { to: "/usage", icon: "📊", label: "Usage" },
  { to: "/settings", icon: "⚙️", label: "Settings" },
];

export default function Sidebar() {
  const {
    projects, projectId, setProject, conversations, conversationId, selectConversation,
    newConversation, deleteConversation, setupCompleted,
  } = useStore();
  const navigate = useNavigate();

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="logo">◈</div>
        <div>
          <h1>Concilium</h1>
          <small>multi-model orchestration</small>
        </div>
      </div>

      <div className="sidebar-section">
        <select
          value={projectId || ""}
          onChange={(e) => setProject(e.target.value)}
          title="Active project"
        >
          {projects.map((p) => <option key={p.id} value={p.id}>📁 {p.name}</option>)}
        </select>
      </div>

      <div className="sidebar-section" style={{ paddingTop: 0 }}>
        <button className="btn primary" style={{ width: "100%", justifyContent: "center" }}
          onClick={async () => { const id = await newConversation(); navigate("/"); void id; }}>
          ＋ New conversation
        </button>
      </div>

      <div className="sec-title" style={{ padding: "4px 18px" }}>Conversations</div>
      <div className="conv-list">
        {conversations.map((c) => (
          <div key={c.id}
            className={`conv-item ${c.id === conversationId ? "active" : ""}`}
            onClick={() => { selectConversation(c.id); navigate("/"); }}>
            <span>{c.mode === "council" ? "👥" : c.mode === "workflow" ? "🔀" : c.mode === "router" ? "🎯" : "🤖"}</span>
            <span className="title">{c.title}</span>
            <button className="del"
              onClick={(e) => { e.stopPropagation(); deleteConversation(c.id); }}>✕</button>
          </div>
        ))}
        {conversations.length === 0 && <div className="small muted" style={{ padding: "6px 10px" }}>No conversations yet.</div>}
      </div>

      <div className="sidebar-section" style={{ paddingBottom: 14 }}>
        <div className="spacer" />
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}
            className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
            <span className="ico">{n.icon}</span> {n.label}
          </NavLink>
        ))}
        {!setupCompleted && (
          <NavLink to="/wizard" className="nav-item">
            <span className="ico">🧙</span> Setup wizard
          </NavLink>
        )}
      </div>
    </aside>
  );
}
