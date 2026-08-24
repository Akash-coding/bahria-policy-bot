import { useEffect, useMemo, useState } from "react";
import { api, type AdminChatSession, type ChatMessage } from "../api";
import { MarkdownMessage } from "../markdown";

function formatTime(value: string) {
  return new Date(value).toLocaleString();
}

export function AdminChats() {
  const [sessions, setSessions] = useState<AdminChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminChatSession | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .adminSessions()
      .then((rows) => {
        setSessions(rows);
        if (rows[0]) setActiveId(rows[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load chats."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      return;
    }
    api
      .adminSession(activeId)
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load conversation."));
  }, [activeId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((item) =>
      [item.title, item.username, item.email, item.client_ip || ""]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [sessions, search]);

  const messages: ChatMessage[] = detail?.messages || [];

  return (
    <div>
      <div className="admin-page-head">
        <div>
          <h1 className="page-title">User conversations</h1>
          <p className="page-subtitle">
            Every chat on this bot, with the visitor&apos;s PC / network IP address.
          </p>
        </div>
        <div className="ip-count">{sessions.length} conversations</div>
      </div>
      {error ? <div className="error">{error}</div> : null}
      {loading ? <p>Loading conversations…</p> : null}
      <div className="chat-monitor">
        <aside className="chat-monitor-list">
          <input
            className="monitor-search"
            type="search"
            placeholder="Search user, email, IP, or title"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {filtered.length === 0 ? (
            <p className="sidebar-empty" style={{ color: "var(--muted)" }}>
              No conversations yet.
            </p>
          ) : (
            filtered.map((item) => (
              <button
                key={item.id}
                className={`monitor-item ${item.id === activeId ? "active" : ""}`}
                onClick={() => setActiveId(item.id)}
              >
                <strong>{item.title || "New conversation"}</strong>
                <span>
                  {item.guest ? "Guest" : item.username}
                  {item.email ? ` · ${item.email}` : ""}
                </span>
                <em>{item.client_ip || "IP unknown"}</em>
              </button>
            ))
          )}
        </aside>
        <section className="chat-monitor-thread">
          {detail ? (
            <>
              <header className="monitor-thread-head">
                <div>
                  <h2>{detail.title || "Conversation"}</h2>
                  <p>
                    {detail.guest ? "Guest user" : detail.username}
                    {detail.email ? ` · ${detail.email}` : ""} · {formatTime(detail.updated_at)}
                  </p>
                </div>
                <div className="ip-pill" title="Visitor PC / network IP">
                  {detail.client_ip || "IP not captured"}
                </div>
              </header>
              <div className="monitor-messages">
                {messages.length === 0 ? (
                  <p className="page-subtitle">No messages in this conversation.</p>
                ) : (
                  messages.map((message) => (
                    <article className={`monitor-bubble ${message.role}`} key={message.id}>
                      <span>{message.role === "user" ? "User" : "Bot"}</span>
                      {message.role === "assistant" ? (
                        <MarkdownMessage text={message.content} />
                      ) : (
                        <p>{message.content}</p>
                      )}
                    </article>
                  ))
                )}
              </div>
            </>
          ) : (
            <p className="page-subtitle">Select a conversation to inspect it.</p>
          )}
        </section>
      </div>
    </div>
  );
}
