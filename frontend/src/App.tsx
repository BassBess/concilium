import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import Login from "./components/Login";
import ChatPage from "./pages/ChatPage";
import ProvidersPage from "./pages/ProvidersPage";
import ModelsPage from "./pages/ModelsPage";
import WorkflowsPage from "./pages/WorkflowsPage";
import PromptsPage from "./pages/PromptsPage";
import ToolsPage from "./pages/ToolsPage";
import UsagePage from "./pages/UsagePage";
import SettingsPage from "./pages/SettingsPage";
import Wizard from "./pages/Wizard";
import { useStore } from "./store";
import { apiGet } from "./api";

export default function App() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const bootstrap = useStore((s) => s.bootstrap);
  const setupCompleted = useStore((s) => s.setupCompleted);
  const location = useLocation();

  useEffect(() => {
    (async () => {
      try {
        const status = await apiGet<{ password_required: boolean; authenticated: boolean }>("/auth/status");
        if (status.password_required && !status.authenticated) {
          setAuthRequired(true);
          setAuthed(false);
        } else {
          await bootstrap();
          setAuthed(true);
        }
      } finally {
        setReady(true);
      }
    })();
    const onUnauth = () => { setAuthed(false); setAuthRequired(true); };
    window.addEventListener("concilium:unauthenticated", onUnauth);
    return () => window.removeEventListener("concilium:unauthenticated", onUnauth);
  }, []);

  const afterLogin = async () => {
    await bootstrap();
    setAuthed(true);
    setAuthRequired(false);
  };

  if (!ready) return <div style={{ padding: 40 }} className="muted">Loading Concilium…</div>;
  if (authRequired && !authed) return <Login onAuthed={afterLogin} />;

  const isWizard = location.pathname.startsWith("/wizard");
  if (!setupCompleted && !isWizard) return <Navigate to="/wizard" replace />;

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/wizard" element={<Wizard />} />
          <Route path="/providers" element={<ProvidersPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/prompts" element={<PromptsPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/usage" element={<UsagePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}
