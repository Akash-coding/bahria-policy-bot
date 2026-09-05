export type User = {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  is_staff: boolean;
  is_superuser: boolean;
};

export type Source = {
  document_id?: number;
  document: string;
  category?: string;
  page?: number | null;
  section?: string | null;
  chunk_index?: number;
  relevance_score?: number;
  excerpt?: string;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  sources: Source[];
  found: boolean;
  created_at: string;
  streaming?: boolean;
};

export type StreamEvent =
  | { type: "meta"; session_id: string; status?: string }
  | { type: "status"; status: string }
  | { type: "delta"; text: string }
  | {
      type: "done";
      session_id: string;
      answer: string;
      sources: Source[];
      found: boolean;
      message: ChatMessage;
    }
  | { type: "close" }
  | { type: "error"; detail: string };

export type ChatSession = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  messages?: ChatMessage[];
};

export type AdminChatSession = ChatSession & {
  client_ip?: string | null;
  username: string;
  email: string;
  guest: boolean;
};

export type DocumentRecord = {
  id: number;
  title: string;
  category: string;
  category_label: string;
  department: string;
  description: string;
  file_url: string | null;
  file_name: string;
  file_type: string;
  version: string;
  status: "uploaded" | "processing" | "completed" | "failed";
  status_label: string;
  error_message: string;
  chunk_count: number;
  uploaded_by_username: string | null;
  created_at: string;
  updated_at: string;
  chunks?: Array<{
    id: number;
    chunk_index: number;
    page_number: number | null;
    content: string;
  }>;
};

export type DashboardStats = {
  total_documents: number;
  processed_documents: number;
  processing_documents: number;
  failed_documents: number;
  uploaded_documents: number;
  total_queries: number;
  total_sessions: number;
  unique_ips?: number;
  indexed_chunks: number;
  ollama: {
    reachable: boolean;
    model: string;
    model_available: boolean;
    error?: string;
  };
};

let csrfToken = "";

function shortText(value: string, limit = 180): string {
  const text = value.replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}…`;
}

async function readResponseBody(response: Response): Promise<unknown> {
  const raw = await response.text();
  const trimmed = raw.trim();
  if (!trimmed) return {};
  try {
    return JSON.parse(trimmed);
  } catch {
    throw new Error(
      shortText(trimmed) || `Request failed (${response.status || "unknown"})`,
    );
  }
}

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

export async function ensureCsrf(): Promise<string> {
  const fromCookie = readCookie("csrftoken");
  if (fromCookie) {
    csrfToken = fromCookie;
    return fromCookie;
  }
  const response = await fetch("/api/auth/csrf/", { credentials: "include" });
  const data = (await readResponseBody(response)) as { csrfToken?: string };
  csrfToken = data.csrfToken || readCookie("csrftoken");
  if (!csrfToken) {
    throw new Error("Could not get a CSRF token from the API.");
  }
  return csrfToken;
}

export function clearCsrfCache() {
  csrfToken = "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    const token = await ensureCsrf();
    headers.set("X-CSRFToken", token);
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
  });
  if (response.status === 204) {
    return undefined as T;
  }
  const data = await readResponseBody(response);
  if (!response.ok) {
    const detail =
      (data as { detail?: string })?.detail ||
      (data as { error?: string })?.error ||
      (typeof data === "string" ? data : "Request failed");
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return data as T;
}

function chatQueryString(question: string, sessionId?: string | null): string {
  const params = new URLSearchParams();
  params.set("question", question);
  if (sessionId) params.set("session_id", sessionId);
  return params.toString();
}

async function askJson(
  question: string,
  sessionId?: string | null,
): Promise<{
  session_id: string;
  answer: string;
  sources: Source[];
  found: boolean;
  message: ChatMessage;
}> {
  return request(`/api/reply/?${chatQueryString(question, sessionId)}`);
}

function emitDone(
  result: {
    session_id: string;
    answer: string;
    sources: Source[];
    found: boolean;
    message: ChatMessage;
  },
  onEvent: (event: StreamEvent) => void,
) {
  onEvent({
    type: "done",
    session_id: result.session_id,
    answer: result.answer,
    sources: result.sources,
    found: result.found,
    message: result.message,
  });
}

async function askStream(
  question: string,
  sessionId: string | null | undefined,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  try {
    await askStreamSse(question, sessionId, onEvent);
  } catch {
    emitDone(await askJson(question, sessionId), onEvent);
  }
}

async function askStreamSse(
  question: string,
  sessionId: string | null | undefined,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`/api/ask/?${chatQueryString(question, sessionId)}`, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "text/event-stream" },
  });
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok || contentType.includes("text/html") || !response.body) {
    throw new Error("stream-unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  let lastDelta = "";

  const consume = (chunk: string) => {
    const parts = chunk.split("\n\n");
    const rest = parts.pop() || "";
    for (const part of parts) {
      const line = part
        .split("\n")
        .map((item) => item.trim())
        .find((item) => item.startsWith("data:"));
      if (!line) continue;
      const payload = line.replace(/^data:\s?/, "");
      if (!payload || payload === "[DONE]") continue;
      let event: StreamEvent;
      try {
        event = JSON.parse(payload) as StreamEvent;
      } catch {
        continue;
      }
      if (event.type === "error") {
        throw new Error(event.detail || "Could not get an answer.");
      }
      if (event.type === "delta") lastDelta = event.text;
      if (event.type === "close") continue;
      onEvent(event);
      if (event.type === "done") completed = true;
    }
    return rest;
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = consume(buffer);
  }
  buffer += decoder.decode();
  if (buffer.trim()) consume(`${buffer}\n\n`);

  if (completed) return;
  if (lastDelta) {
    onEvent({
      type: "done",
      session_id: sessionId || "",
      answer: lastDelta,
      sources: [],
      found: true,
      message: {
        id: Date.now(),
        role: "assistant",
        content: lastDelta,
        sources: [],
        found: true,
        created_at: new Date().toISOString(),
      },
    });
    return;
  }
  throw new Error("stream-unavailable");
}

export const api = {
  me: () =>
    request<{ authenticated?: boolean; user: User | null }>("/api/auth/me/"),
  login: async (username: string, password: string) => {
    const user = await request<User>("/api/auth/login/", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    clearCsrfCache();
    await ensureCsrf();
    return user;
  },
  logout: async () => {
    try {
      return await request<{ detail: string }>("/api/auth/logout/", { method: "POST" });
    } finally {
      clearCsrfCache();
    }
  },
  ask: (question: string, sessionId?: string | null) => askJson(question, sessionId),
  askStream: (
    question: string,
    sessionId: string | null | undefined,
    onEvent: (event: StreamEvent) => void,
  ) => askStream(question, sessionId, onEvent),
  sessions: async () => {
    const data = await request<ChatSession[] | { results?: ChatSession[] }>("/api/chat/sessions/");
    if (Array.isArray(data)) return data;
    if (Array.isArray(data?.results)) return data.results;
    return [];
  },
  createSession: () =>
    request<ChatSession>("/api/chat/sessions/", { method: "POST" }),
  session: (id: string) => request<ChatSession>(`/api/chat/history/?session_id=${id}`),
  deleteSession: (id: string) =>
    request<void>(`/api/chat/sessions/${id}/`, { method: "DELETE" }),
  health: () => request<Record<string, unknown>>("/api/health/"),
  stats: () => request<DashboardStats>("/api/dashboard/stats/"),
  documents: (query = "") => request<DocumentRecord[]>(`/api/documents/${query}`),
  document: (id: number) => request<DocumentRecord>(`/api/documents/${id}/`),
  deleteDocument: (id: number) =>
    request<void>(`/api/documents/${id}/`, { method: "DELETE" }),
  reprocess: (id: number) =>
    request<DocumentRecord>(`/api/documents/${id}/reprocess/`, { method: "POST" }),
  categories: () => request<Array<{ value: string; label: string }>>("/api/documents/categories/"),
  upload: (form: FormData) =>
    request<DocumentRecord>("/api/documents/", { method: "POST", body: form }),
  adminSessions: () => request<AdminChatSession[]>("/api/chat/admin/sessions/"),
  adminSession: (id: string) =>
    request<AdminChatSession>(`/api/chat/admin/sessions/${id}/`),
};
