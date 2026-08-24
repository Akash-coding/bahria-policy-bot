import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { ChatPage } from "./pages/ChatPage";
import { LoginPage } from "./pages/LoginPage";
import { AdminDashboard } from "./pages/AdminDashboard";
import { AdminDocuments } from "./pages/AdminDocuments";
import { AdminDocumentDetail } from "./pages/AdminDocumentDetail";
import { AdminUpload } from "./pages/AdminUpload";
import { AdminChats } from "./pages/AdminChats";
import { ThemeToggle } from "./theme";

function StaffGate({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="auth-page">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_staff) return <Navigate to="/" replace />;
  return children;
}

function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="admin-shell">
      <aside className="admin-nav">
        <div className="brand">
          <span className="orb" aria-hidden="true" />
          <div>
            <h1>BahriaAI</h1>
            <p>Policy console</p>
          </div>
        </div>
        <nav className="admin-links">
          <NavLink to="/admin-panel" end>
            Dashboard
          </NavLink>
          <NavLink to="/admin-panel/chats">User chats</NavLink>
          <NavLink to="/admin-panel/documents">Documents</NavLink>
          <NavLink to="/admin-panel/upload">Upload policy</NavLink>
          <NavLink to="/">Open chatbot</NavLink>
        </nav>
        <div className="admin-user">
          <div className="admin-user-meta">
            <span className="avatar user">{(user?.username || "A").slice(0, 1).toUpperCase()}</span>
            <div>
              <strong>{user?.username}</strong>
              <span>{user?.email || "Administrator"}</span>
            </div>
          </div>
          <div className="admin-user-actions">
            <ThemeToggle />
            <button type="button" className="logout-btn" onClick={() => void handleLogout()}>
              Log out
            </button>
          </div>
        </div>
      </aside>
      <main className="admin-content">
        <Routes>
          <Route index element={<AdminDashboard />} />
          <Route path="chats" element={<AdminChats />} />
          <Route path="documents" element={<AdminDocuments />} />
          <Route path="documents/:id" element={<AdminDocumentDetail />} />
          <Route path="upload" element={<AdminUpload />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<ChatPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/admin-panel/*"
          element={
            <StaffGate>
              <AdminLayout />
            </StaffGate>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
