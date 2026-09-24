import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DashboardStats } from "../api";

export function AdminDashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .stats()
      .then(setStats)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load dashboard."));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!stats) return <p>Loading dashboard…</p>;

  const cards = [
    ["Documents", stats.total_documents],
    ["Processed", stats.processed_documents],
    ["Processing", stats.processing_documents],
    ["Failed", stats.failed_documents],
    ["Questions asked", stats.total_queries],
    ["Conversations", stats.total_sessions],
    ["Unique IPs", stats.unique_ips ?? 0],
    ["Indexed chunks", stats.indexed_chunks],
  ] as const;

  return (
    <div>
      <div className="admin-page-head">
        <div>
          <h1 className="page-title">Operations overview</h1>
          <p className="page-subtitle">Knowledge base, Groq model status, and live chat traffic.</p>
        </div>
        <Link className="btn btn-navy" to="/console/chats">
          Review user chats
        </Link>
      </div>
      <div className="stats-grid">
        {cards.map(([label, value]) => (
          <div className="stat-card" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="panel">
        <h2>Groq AI status</h2>
        <p>
          <span className={`status-dot ${stats.groq.reachable ? "ok" : "bad"}`} />{" "}
          Groq {stats.groq.reachable ? "is online" : "is offline"} · {stats.groq.model}{" "}
          {stats.groq.model_available ? "is ready" : "was not found"}
        </p>
        {stats.groq.error ? <div className="error">{stats.groq.error}</div> : null}
      </div>
    </div>
  );
}
