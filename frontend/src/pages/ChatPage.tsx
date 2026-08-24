import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ChatMessage, type ChatSession, type Source } from "../api";
import { useAuth } from "../auth";
import { MarkdownMessage } from "../markdown";
import { ThemeToggle } from "../theme";

const PROMPT_CARDS = [
  { title: "Attendance policy", prompt: "What is the attendance policy?" },
  { title: "Student leaves", prompt: "How many leaves can a student take?" },
  { title: "Examinations", prompt: "What is the examination policy?" },
  { title: "Who are you?", prompt: "Who are you?" },
];

function SidebarIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
      <path d="M12 19V5M6 11l6-6 6 6" />
    </svg>
  );
}

function PolicySources({ sources }: { sources: Source[] }) {
  if (!sources?.length) return null;
  const unique: Source[] = [];
  const seen = new Set<string>();
  for (const source of sources) {
    const key = `${source.document}::${source.page ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(source);
  }
  if (!unique.length) return null;
  return (
    <details className="source-dropdown">
      <summary>
        Sources <span>{unique.length}</span>
      </summary>
      <ul>
        {unique.map((source, index) => (
          <li key={`${source.document}-${source.page}-${index}`}>
            <strong>{source.document}</strong>
            {source.section ? `, ${source.section}` : ""}
            {source.page ? `, page ${source.page}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ChatPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth > 860,
  );
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const loadSessions = async () => {
    try {
      setSessions(await api.sessions());
    } catch {
      setSessions([]);
    }
  };

  const openSession = async (id: string) => {
    setSessionId(id);
    const detail = await api.session(id);
    setMessages(detail.messages || []);
    if (window.innerWidth <= 860) setSidebarOpen(false);
  };

  useEffect(() => {
    void loadSessions();
  }, []);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [question]);

  const startNewChat = async () => {
    setError("");
    const created = await api.createSession();
    setSessionId(created.id);
    setMessages([]);
    await loadSessions();
    if (window.innerWidth <= 860) setSidebarOpen(false);
    inputRef.current?.focus();
  };

  const submit = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setError("");
    setQuestion("");
    setBusy(true);
    const optimistic: ChatMessage = {
      id: Date.now(),
      role: "user",
      content: trimmed,
      sources: [],
      found: true,
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);
    try {
      const streamId = Date.now() + 1;
      const placeholder: ChatMessage = {
        id: streamId,
        role: "assistant",
        content: "",
        sources: [],
        found: true,
        created_at: new Date().toISOString(),
        streaming: true,
      };
      setMessages((current) => [...current, placeholder]);

      await api.askStream(trimmed, sessionId, (event) => {
        if (event.type === "meta") setSessionId(event.session_id);
        if (event.type === "delta") {
          setMessages((current) =>
            current.map((item) =>
              item.id === streamId ? { ...item, content: event.text, streaming: true } : item,
            ),
          );
        }
        if (event.type === "done") {
          setSessionId(event.session_id);
          setMessages((current) => {
            const without = current.filter(
              (item) => item.id !== optimistic.id && item.id !== streamId,
            );
            return [...without, { ...optimistic, id: event.message.id - 1 }, event.message];
          });
        }
      });
      await loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not get an answer.");
      setMessages((current) =>
        current.filter((item) => item.id !== optimistic.id && !item.streaming),
      );
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(question);
  };

  const handleLogout = async () => {
    await logout();
    navigate("/", { replace: true });
  };

  const activeTitle = useMemo(
    () => sessions.find((item) => item.id === sessionId)?.title || "New chat",
    [sessions, sessionId],
  );
  const userInitial = (user?.username || "G").slice(0, 1).toUpperCase();

  return (
    <div className={`gpt-app ${sidebarOpen ? "sidebar-open" : ""}`}>
      {sidebarOpen ? (
        <button
          className="sidebar-backdrop"
          aria-label="Close sidebar"
          onClick={() => setSidebarOpen(false)}
        />
      ) : null}

      <aside className="gpt-sidebar">
        <div className="sidebar-header">
          <Link to="/" className="sidebar-brand">
            <span className="orb" aria-hidden="true" />
            <span>BahriaAI</span>
          </Link>
          <button
            className="icon-btn sidebar-only-wide"
            title="Close sidebar"
            onClick={() => setSidebarOpen(false)}
          >
            <SidebarIcon />
          </button>
        </div>

        <button className="new-chat-btn" onClick={() => void startNewChat()}>
          <PlusIcon />
          New chat
        </button>

        <div className="sidebar-section-label">Recent chats</div>
        <nav className="session-list">
          {sessions.length === 0 ? (
            <p className="sidebar-empty">No conversations yet</p>
          ) : (
            sessions.map((item) => (
              <button
                key={item.id}
                className={`session-item ${item.id === sessionId ? "active" : ""}`}
                onClick={() => void openSession(item.id)}
              >
                <span>{item.title || "New chat"}</span>
              </button>
            ))
          )}
        </nav>

        <div className="sidebar-footer">
          {user?.is_staff ? (
            <Link className="session-item" to="/admin-panel">
              Admin console
            </Link>
          ) : null}
          {user ? (
            <div className="user-row">
              <span className="avatar user">{userInitial}</span>
              <div className="user-meta">
                <strong>{user.username}</strong>
                <button type="button" className="logout-btn" onClick={() => void handleLogout()}>
                  Log out
                </button>
              </div>
            </div>
          ) : (
            <Link className="session-item" to="/login">
              Log in
            </Link>
          )}
          <ThemeToggle />
        </div>
      </aside>

      <main className="gpt-main">
        <header className="gpt-topbar">
          <button className="icon-btn" title="Open sidebar" onClick={() => setSidebarOpen((open) => !open)}>
            <SidebarIcon />
          </button>
          <div className="topbar-copy">
            <h1>{activeTitle}</h1>
            <p>Policy assistant</p>
          </div>
          <ThemeToggle compact />
          <button className="icon-btn mobile-only" title="New chat" onClick={() => void startNewChat()}>
            <PlusIcon />
          </button>
        </header>

        <div className="messages" ref={threadRef}>
          {messages.length === 0 && !busy ? (
            <div className="empty-state">
              <div className="empty-logo">AI</div>
              <h2>How can I help you today?</h2>
              <p>Ask about official Bahria University policies. If it is not in the documents, I will say so.</p>
              <div className="suggestions">
                {PROMPT_CARDS.map((item) => (
                  <button key={item.title} className="suggestion" onClick={() => void submit(item.prompt)}>
                    <strong>{item.title}</strong>
                    <span>{item.prompt}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message) => {
              const kind = message.role === "user" ? "user" : "assistant";
              return (
                <div className={`bubble-row ${kind}`} key={message.id}>
                  {kind === "assistant" ? <span className="avatar bot">AI</span> : null}
                  <div className={`bubble ${kind} ${message.streaming ? "streaming" : ""}`}>
                    {kind === "assistant" ? (
                      message.content ? (
                        <>
                          <MarkdownMessage text={message.content} />
                          {message.streaming ? <span className="stream-cursor" aria-hidden="true" /> : null}
                        </>
                      ) : (
                        <div className="typing" aria-label="Looking up policies">
                          <span />
                          <span />
                          <span />
                        </div>
                      )
                    ) : (
                      message.content
                    )}
                    {kind === "assistant" && !message.streaming && message.sources?.length ? (
                      <PolicySources sources={message.sources} />
                    ) : null}
                  </div>
                </div>
              );
            })
          )}
          {error ? <div className="error">{error}</div> : null}
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <div className="composer-box">
            <textarea
              ref={inputRef}
              rows={1}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask anything about university policies"
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit(question);
                }
              }}
            />
            <button className="send-btn" type="submit" disabled={busy || !question.trim()} aria-label="Send">
              <SendIcon />
            </button>
          </div>
          <p className="composer-hint">Answers come from uploaded Bahria University policy documents.</p>
        </form>
      </main>
    </div>
  );
}
