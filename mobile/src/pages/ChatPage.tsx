import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ChatMessage, type ChatSession, type Source } from "../api";
import { useAuth } from "../auth";
import { MarkdownMessage } from "../markdown";
import { ThemeToggle } from "../theme";
import { stripThinking } from "../visibleAnswer";

const PROMPT_CARDS = [
  { title: "Attendance", subtitle: "Classes, shortfall, and exams", prompt: "What is the attendance policy?", icon: "📅" },
  { title: "Student leaves", subtitle: "Casual, medical, and semester leave", prompt: "How many leaves can a student take?", icon: "📝" },
  { title: "Examinations", subtitle: "Papers, grading, and retakes", prompt: "What is the examination policy?", icon: "🎓" },
  { title: "Who are you?", subtitle: "Meet the Bahria policy assistant", prompt: "Who are you?", icon: "💬" },
];

function toSpokenText(markdown: string) {
  return markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`]/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function speakAnswer(text: string) {
  const spoken = toSpokenText(text);
  if (!spoken) return;
  const bridge = nativeVoice();
  if (bridge?.isNative?.()) {
    bridge.speak(spoken);
    return;
  }
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(spoken);
  utter.lang = /[\u0600-\u06FF]/.test(spoken) ? "ur-PK" : "en-US";
  utter.rate = 1;
  window.speechSynthesis.speak(utter);
}

function stopSpeaking() {
  const bridge = nativeVoice();
  if (bridge?.isNative?.()) {
    bridge.silence();
    return;
  }
  if (typeof window !== "undefined") window.speechSynthesis?.cancel();
}

type NativeVoice = {
  isNative?: () => boolean;
  start: () => void;
  stop: () => void;
  speak: (text: string) => void;
  silence: () => void;
};

type BrowserSpeech = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onstart: () => void;
  onend: () => void;
  onerror: (event?: { error?: string }) => void;
  onresult: (event: {
    results: { length: number; [index: number]: { 0?: { transcript?: string }; isFinal?: boolean } };
  }) => void;
  start: () => void;
  stop: () => void;
};

function nativeVoice() {
  if (typeof window === "undefined") return undefined;
  return (window as unknown as { BahriaVoice?: NativeVoice }).BahriaVoice;
}

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

function MicIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
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
    const key = source.source_url || `${source.source_type || ""}::${source.document}::${source.page ?? ""}`;
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
      <ol>
        {unique.map((source, index) => {
          const kind = source.source_type || "Policy Document";
          const label = source.source_url || source.document;
          return (
            <li key={`${label}-${source.page}-${index}`}>
              <strong>{kind}:</strong>{" "}
              {source.source_url ? (
                <a href={source.source_url} target="_blank" rel="noreferrer">
                  {source.source_url}
                </a>
              ) : (
                <>
                  {source.document}
                  {source.section ? `, ${source.section}` : ""}
                  {source.page ? `, page ${source.page}` : ""}
                </>
              )}
            </li>
          );
        })}
      </ol>
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
  const [listening, setListening] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const busyRef = useRef(false);
  const voiceTurnRef = useRef(false);
  const submitVoiceRef = useRef<(text: string) => void>(() => undefined);
  const liveTranscriptRef = useRef("");

  useEffect(() => {
    return () => stopSpeaking();
  }, []);

  useEffect(() => {
    const onNativeVoice = (event: Event) => {
      const detail = (event as CustomEvent<{ type?: string; text?: string }>).detail || {};
      if (detail.type === "start") {
        setListening(true);
        setError("");
        if (detail.text) {
          liveTranscriptRef.current = detail.text;
          setLiveTranscript(detail.text);
        } else {
          liveTranscriptRef.current = "";
          setLiveTranscript("");
        }
        return;
      }
      if (detail.type === "partial") {
        liveTranscriptRef.current = detail.text || "";
        setLiveTranscript(detail.text || "");
        return;
      }
      if (detail.type === "final") {
        setListening(false);
        const spoken = (detail.text || liveTranscriptRef.current).trim();
        liveTranscriptRef.current = "";
        setLiveTranscript("");
        if (spoken) submitVoiceRef.current(spoken);
        else setError("Could not hear that. Please try the mic again.");
        return;
      }
      if (detail.type === "error") {
        setListening(false);
        const spoken = liveTranscriptRef.current.trim();
        liveTranscriptRef.current = "";
        setLiveTranscript("");
        if (spoken) {
          submitVoiceRef.current(spoken);
          return;
        }
        setError(detail.text || "Could not hear that. Please try the mic again.");
      }
    };
    window.addEventListener("bahria-voice", onNativeVoice);
    return () => window.removeEventListener("bahria-voice", onNativeVoice);
  }, []);

  const loadSessions = async () => {
    try {
      const rows = await api.sessions();
      setSessions(
        Array.isArray(rows) ? rows.filter((item) => (item.message_count ?? 1) > 0) : [],
      );
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

  const startNewChat = () => {
    stopSpeaking();
    voiceTurnRef.current = false;
    setError("");
    setSessionId(null);
    setMessages([]);
    setSidebarOpen(false);
    setLiveTranscript("");
  };

  const submit = async (text: string, viaVoice = false) => {
    const trimmed = text.trim();
    if (!trimmed || busyRef.current) return;
    stopSpeaking();
    voiceTurnRef.current = viaVoice;
    busyRef.current = true;
    setError("");
    setQuestion("");
    setBusy(true);
    setListening(false);
    setLiveTranscript("");
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

      let finalAnswer = "";
      await api.askStream(trimmed, sessionId, (event) => {
        if (event.type === "meta" && event.session_id) setSessionId(event.session_id);
        if (event.type === "delta") {
          finalAnswer = stripThinking(event.text);
          setMessages((current) =>
            current.map((item) =>
              item.id === streamId ? { ...item, content: stripThinking(event.text), streaming: true } : item,
            ),
          );
        }
        if (event.type === "done") {
          if (event.session_id) setSessionId(event.session_id);
          const incoming = stripThinking(event.answer || "");
          finalAnswer = incoming || finalAnswer;
          setMessages((current) =>
            current.map((item) => {
              if (item.id !== streamId) return item;
              const streamed = stripThinking(item.content || "");
              return {
                ...item,
                id: event.message?.id || item.id,
                role: "assistant" as const,
                content: incoming || streamed,
                sources: event.sources || event.message?.sources || [],
                found: event.found,
                streaming: false,
              };
            }),
          );
        }
      });
      const spoken = finalAnswer || "";
      if (voiceTurnRef.current && spoken) speakAnswer(spoken);
      void loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not get an answer.");
      setMessages((current) =>
        current.map((item) =>
          item.streaming
            ? {
                ...item,
                streaming: false,
                content: stripThinking(item.content) || "The assistant could not finish that reply. Please try again.",
              }
            : item,
        ),
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  submitVoiceRef.current = (text: string) => {
    void submit(text, true);
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(question);
  };

  const handleLogout = async () => {
    await logout();
    navigate("/", { replace: true });
  };

  const sessionList = Array.isArray(sessions) ? sessions : [];
  const activeTitle = useMemo(
    () => sessionList.find((item) => item.id === sessionId)?.title || "New chat",
    [sessionList, sessionId],
  );
  const userInitial = (user?.username || "G").slice(0, 1).toUpperCase();
  const helloName = user?.first_name || user?.username || "there";

  const isHome = messages.length === 0 && !busy && !listening;

  const startVoice = () => {
    stopSpeaking();
    setError("");
    const bridge = nativeVoice();
    if (bridge?.isNative?.()) {
      setListening(true);
      setLiveTranscript("");
      bridge.start();
      return;
    }
    const SpeechEngine = (
      window as unknown as {
        webkitSpeechRecognition?: new () => BrowserSpeech;
        SpeechRecognition?: new () => BrowserSpeech;
      }
    ).webkitSpeechRecognition || (
      window as unknown as { SpeechRecognition?: new () => BrowserSpeech }
    ).SpeechRecognition;
    if (!SpeechEngine) {
      setError("Voice is not supported here. Please type your question.");
      inputRef.current?.focus();
      return;
    }
    const recognition = new SpeechEngine();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.onstart = () => {
      setListening(true);
      setLiveTranscript("");
      setError("");
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = (event) => {
      setListening(false);
      if (event?.error === "aborted") return;
      setError("Could not hear that. Please try the mic again.");
    };
    recognition.onresult = (event) => {
      const last = event.results[event.results.length - 1];
      const spoken = last?.[0]?.transcript?.trim();
      if (!spoken) return;
      if (last?.isFinal) {
        recognition.stop();
        void submit(spoken, true);
      } else {
        setLiveTranscript(spoken);
      }
    };
    recognition.start();
  };

  return (
    <div className={`gpt-app ${sidebarOpen ? "sidebar-open" : ""} ${isHome ? "home-screen" : ""} ${listening ? "listening-screen" : ""}`}>
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

        <button className="new-chat-btn" onClick={startNewChat}>
          <PlusIcon />
          New chat
        </button>

        <div className="sidebar-section-label">Recent chats</div>
        <nav className="session-list">
          {sessionList.length === 0 ? (
            <p className="sidebar-empty">No conversations yet</p>
          ) : (
            sessionList.map((item) => (
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
            <Link className="session-item" to="/console">
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
          <button className="icon-btn" title="Menu" onClick={() => setSidebarOpen((open) => !open)}>
            <SidebarIcon />
          </button>
          {messages.length > 0 || (busy && !listening) ? (
            <>
              <div className="topbar-copy">
                <h1>{activeTitle}</h1>
              </div>
              <button className="icon-btn" title="New chat" onClick={startNewChat}>
                <PlusIcon />
              </button>
            </>
          ) : null}
        </header>

        <div className="messages" ref={threadRef}>
          {listening ? (
            <div className="listen-screen">
              <div className="listen-orb" aria-hidden="true" />
              <p className="listen-label">Listening</p>
              <p className="listen-copy">
                {liveTranscript || "Ask a Bahria University policy question."}
              </p>
            </div>
          ) : messages.length === 0 && !busy ? (
            <div className="empty-state">
              <span className="home-badge">Bahria Policy Assistant</span>
              <h2 className="hero-hi">Hi, {helloName}</h2>
              <p className="hero-sub">
                Ask about attendance, exams, leaves, and other official Bahria University policies.
              </p>
              <div className="topic-list">
                {PROMPT_CARDS.map((item) => (
                  <button key={item.title} className="topic-card" onClick={() => void submit(item.prompt)}>
                    <span className="topic-icon" aria-hidden="true">
                      {item.icon}
                    </span>
                    <span className="topic-copy">
                      <strong>{item.title}</strong>
                      <span>{item.subtitle}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="home-hint">Type a question or use the microphone. English and Urdu both work.</p>
            </div>
          ) : (
            <>
              {messages.map((message) => {
                const kind = message.role === "user" ? "user" : "assistant";
                const shown = kind === "assistant" ? stripThinking(message.content) : message.content;
                return (
                  <div className={`bubble-row ${kind}`} key={message.id}>
                    {kind === "assistant" ? <span className="bot-name">BahriaAI</span> : null}
                    <div className={`bubble ${kind} ${message.streaming ? "streaming" : ""}`}>
                      {kind === "assistant" ? (
                        shown ? (
                          <>
                            <MarkdownMessage text={shown} />
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
                    {kind === "user" ? <span className="avatar user">{userInitial}</span> : null}
                  </div>
                );
              })}
            </>
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
            <button
              className={`mic-btn ${listening ? "listening" : ""}`}
              type="button"
              aria-label="Voice input"
              onClick={startVoice}
            >
              <MicIcon />
            </button>
            <button className="send-btn" type="submit" disabled={busy || !question.trim()} aria-label="Send">
              <SendIcon />
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}
