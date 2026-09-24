# BahriaAI Policy Bot — App & API Guide

Yeh document **backend APIs** explain karta hai taake aap is project ke upar naya mobile ya web app bana sako. Existing React UI change kiye baghair inhi endpoints ko use karo.

Chat **guest** users ke liye open hai (login zaroori nahi). Documents, scraper, dashboard, aur sab users ki chats **staff login** ke baad milti hain.

---

## 1. Base URLs

| Environment | Base URL | API prefix |
|---|---|---|
| Local backend | `http://127.0.0.1:8000` | `/api/...` |
| Local Vite UI | `http://localhost:5173` | `/api/...` (Vite proxy → Django) |
| Production (campus) | `http://10.1.2.216` | disguised `/assets/*.js` paths (see §12) |

Local development ke liye **hamesha** `/api/` wale paths use karo.

Campus WAF `/api` aur URL mein `admin` block kar sakta hai. Production app ke liye §12 follow karo. Path ke andar `admin` word mat likho (staff UI `/console` hai, `/admin` nahi).

---

## 2. Auth, cookies, CSRF

Auth Django **session cookies** se hai. JWT nahi hai.

Har request par:

```http
Cookie: sessionid=...; csrftoken=...; bahria_chat_key=...
```

Native / Flutter / React Native app mein `credentials: include` jaisa cookie jar zaroori hai (`withCredentials`, `CookieJar`, etc.).

### Cookies

| Cookie | HttpOnly | Kaam |
|---|---|---|
| `sessionid` | yes | Logged-in user session |
| `csrftoken` | **no** (JS padh sakti hai) | POST/PUT/PATCH/DELETE ke liye |
| `bahria_chat_key` | yes | Guest chat ownership (30 days) |

### CSRF

GET/HEAD par CSRF header zaroori nahi.

Baaki methods par:

1. Pehle `GET /api/auth/csrf/` (yeh `csrftoken` cookie set karta hai).
2. Har mutating request par header:

```http
X-CSRFToken: <csrftoken cookie value>
Content-Type: application/json
```

`multipart/form-data` upload par `Content-Type` khud mat set karo (boundary browser/client set kare). `X-CSRFToken` phir bhi bhejo.

Chat ask/stream CSRF se exempt hain, lekin cookie phir bhi bhejo taake same guest session rahe.

### Permissions

| Role | Access |
|---|---|
| Guest (no login) | Chat ask/stream, own sessions, CSRF, me, health |
| Logged-in student (`is_staff: false`) | Same as guest, plus own account sessions |
| Staff (`is_staff: true`) | All of the above + documents, scraper, dashboard, all-user chats |

Staff nahi ho to staff APIs **403** deti hain:

```json
{ "detail": "Administrator privileges are required." }
```

Login email ya username dono chal sakte hain.

---

## 3. Common types

### User

```json
{
  "id": 1,
  "username": "arshadkhan@gmail.com",
  "first_name": "Arshad",
  "last_name": "Khan",
  "email": "arshadkhan@gmail.com",
  "is_staff": true,
  "is_superuser": true
}
```

### Source (citation)

```json
{
  "document_id": 1,
  "document": "Bahria University Student Handbook (official)",
  "category": "academic",
  "page": 122,
  "section": "GENERAL EXAMINATION RULES",
  "chunk_index": 14,
  "relevance_score": 0.81,
  "excerpt": "Students must maintain 75% attendance...",
  "source_type": "PDF",
  "source_url": null
}
```

### Chat message

```json
{
  "id": 42,
  "role": "assistant",
  "content": "You need 75% attendance...\n\nWould you like to know what happens if it falls below 75%?",
  "sources": [],
  "found": true,
  "created_at": "2026-09-24T06:00:00.000Z"
}
```

`role`: `user` | `assistant` | `system`  
`found`: `false` jab handbook mein topic na mile.

### Error

```json
{ "detail": "Invalid username or password." }
```

---

## 4. Suggested app flow (chat)

1. `GET /api/auth/csrf/`
2. `GET /api/auth/me/` (optional)
3. `GET /api/chat/sessions/` — purani chats
4. User question bhejo **stream** se: `GET /api/ask/?question=...&session_id=...`
5. SSE events padho (`meta` → `delta` → `done`)
6. `session_id` save karo (cookie `bahria_chat_key` guest ke liye auto set)
7. Next message same `session_id` ke sath bhejo
8. Bot ke last question ke baad user **yes / han / ji / ok** bole to wahi follow-up continue hota hai; **no / nahi** skip karta hai

Agar stream fail ho (WAF/HTML page), fallback:

`GET /api/reply/?question=...&session_id=...`

---

## 5. Health

### `GET /api/health/`

Auth: public

```json
{
  "status": "ok",
  "service": "bahria-policy-bot",
  "groq": {
    "reachable": true,
    "base_url": "https://api.groq.com/openai/v1",
    "model": "qwen/qwen3.8-27b",
    "model_available": true,
    "models": ["qwen/qwen3.8-27b"]
  },
  "embedding_provider": "sentence-transformers",
  "embedding_model": "all-MiniLM-L6-v2",
  "llm_model": "qwen/qwen3.8-27b"
}
```

---

## 6. Auth APIs

### `GET /api/auth/csrf/`

Sets CSRF cookie.

```json
{ "detail": "CSRF cookie set", "csrfToken": "...." }
```

### `POST /api/auth/login/`

```json
{ "username": "arshadkhan@gmail.com", "password": "your-password" }
```

Success `200`: User object  
Fail `401`: `{ "detail": "Invalid username or password." }`  
Disabled `403`: `{ "detail": "This account is disabled." }`

Login ke baad naya CSRF token lo.

### `POST /api/auth/logout/`

```json
{ "detail": "Logged out." }
```

### `GET /api/auth/me/`

Guest:

```json
{ "authenticated": false, "user": null }
```

Logged in:

```json
{ "authenticated": true, "user": { "id": 1, "username": "...", "is_staff": true } }
```

---

## 7. Chat APIs (public)

Question max **4000** characters. `session_id` UUID hai (optional pehli request par; server naya session bana dega).

### 7.1 Stream (recommended) — `GET /api/ask/`

Query:

| Param | Required | Description |
|---|---|---|
| `question` | yes | User message |
| `session_id` | no | Existing conversation UUID |

```http
GET /api/ask/?question=What%20is%20the%20attendance%20policy%3F&session_id=<uuid>
Accept: text/event-stream
Cookie: bahria_chat_key=...; sessionid=...
```

`POST /api/ask/` bhi chalega, body:

```json
{ "question": "What is the attendance policy?", "session_id": null }
```

Response: `Content-Type: text/event-stream`

Har event:

```text
data: {"type":"meta","session_id":"...","status":"retrieving"}

data: {"type":"status","status":"generating"}

data: {"type":"delta","text":"You need 75% attendance"}

data: {"type":"done","session_id":"...","answer":"...","sources":[],"found":true,"message":{}}

data: {"type":"close"}
```

| `type` | Meaning |
|---|---|
| `meta` | Session id; status usually `retrieving` |
| `status` | `generating` |
| `delta` | Full visible answer so far (append nahi; **replace** UI text) |
| `done` | Final `answer`, `sources`, `found`, saved `message` |
| `error` | `{ "detail": "The policy assistant could not complete this request. Please try again." }` |
| `close` | Stream end |

`delta.text` **cumulative** hai. UI mein previous + chunk mat joro; latest `text` dikhao.

Guest response `Set-Cookie: bahria_chat_key=...` bhejti hai. Usay store karo.

Also available as `GET/POST /api/chat/stream/` (same handler).

### 7.2 Non-stream JSON — `GET /api/reply/`

Same query params (or POST JSON). Waits for the full answer.

`200`:

```json
{
  "session_id": "b15b6157-2f85-4ad6-8dd0-24eecfaa27b3",
  "answer": "You need 75% attendance...\n\nWould you like to know what happens if it falls below 75%?",
  "sources": [ { "document": "...", "page": 122 } ],
  "found": true,
  "message": { "id": 10, "role": "assistant", "content": "...", "sources": [], "found": true, "created_at": "..." }
}
```

Groq down: `503` `{ "detail": "...", "session_id": "..." }`

Also: `GET/POST /api/chat/` (same handler).

### 7.3 List / create sessions — `GET|POST /api/chat/sessions/`

`GET`: current user/guest ki chats jisme kam az kam 1 message ho.

```json
[
  {
    "id": "b15b6157-2f85-4ad6-8dd0-24eecfaa27b3",
    "title": "What is the attendance policy?",
    "created_at": "...",
    "updated_at": "...",
    "message_count": 4
  }
]
```

`POST`: empty session (`201`). Title default `New conversation`.

### 7.4 Session by query — `GET|DELETE /api/chat/history/?session_id=<uuid>`

`GET` detail (messages included):

```json
{
  "id": "...",
  "title": "...",
  "created_at": "...",
  "updated_at": "...",
  "message_count": 4,
  "messages": [
    { "id": 1, "role": "user", "content": "What is the attendance policy?", "sources": [], "found": false, "created_at": "..." },
    { "id": 2, "role": "assistant", "content": "...", "sources": [], "found": true, "created_at": "..." }
  ]
}
```

`DELETE`: `204` — conversation delete.

Bina `session_id` ke `GET` last 20 sessions (without messages).

REST style bhi hai (local only; production WAF ke liye query-param wala use karo):

- `GET|DELETE /api/chat/sessions/<uuid>/`

### 7.5 Follow-up behaviour (app UI)

Assistant ke jawab ke end par **ek natural question** hoti hai (heading nahi, `Suggested question:` nahi).

Agla user message:

| User types | App should |
|---|---|
| `yes`, `yeah`, `ok`, `han`, `ji`, `theek hai` | Same `session_id` se bhejo — backend last bot question answer karega |
| `no`, `nahi`, `no thanks` | Same session — bot skip karega aur naya topic maangega |
| Naya policy question | Normal ask |

App ko yes/no locally rewrite karne ki zaroorat nahi; backend history se samajhta hai. `session_id` zaroor bhejo.

---

## 8. Staff: dashboard

### `GET /api/dashboard/stats/`

Auth: staff

```json
{
  "total_documents": 4,
  "processed_documents": 4,
  "processing_documents": 0,
  "failed_documents": 0,
  "uploaded_documents": 0,
  "total_queries": 120,
  "total_sessions": 40,
  "unique_ips": 18,
  "indexed_chunks": 671,
  "groq": { "reachable": true, "model": "qwen/qwen3.8-27b", "model_available": true }
}
```

---

## 9. Staff: documents

Auth: staff. Uploads: `.pdf`, `.docx`, `.txt`, max **20 MB**.

### Categories — `GET /api/documents/categories/`

```json
[
  { "value": "academic", "label": "Academic Policies" },
  { "value": "examination", "label": "Examination Policies" },
  { "value": "student_affairs", "label": "Student Affairs" },
  { "value": "hr", "label": "HR Policies" },
  { "value": "finance", "label": "Finance Policies" },
  { "value": "admissions", "label": "Admissions Policies" },
  { "value": "leave", "label": "Leave Policies" },
  { "value": "attendance", "label": "Attendance Policies" },
  { "value": "general", "label": "General University Policies" }
]
```

### List / upload — `GET|POST /api/documents/`

`GET` query:

| Param | Description |
|---|---|
| `search` | title / description / department |
| `category` | e.g. `attendance` |
| `status` | `uploaded` \| `processing` \| `completed` \| `failed` |

`POST` `multipart/form-data`:

| Field | Required |
|---|---|
| `title` | yes |
| `file` | yes |
| `category` | no (default `general`) |
| `department` | no |
| `description` | no |
| `version` | no (default `1.0`) |

`201` document object. Status pehle `uploaded` / `processing`, phir `completed` ya `failed`. Poll `GET` detail.

Document object:

```json
{
  "id": 1,
  "title": "Student Handbook",
  "category": "academic",
  "category_label": "Academic Policies",
  "department": "",
  "description": "",
  "file_url": "http://127.0.0.1:8000/media/policies/2026/09/handbook.pdf",
  "file_name": "handbook.pdf",
  "file_type": "pdf",
  "version": "1.0",
  "status": "completed",
  "status_label": "Completed",
  "error_message": "",
  "chunk_count": 322,
  "uploaded_by_username": "admin",
  "created_at": "...",
  "updated_at": "..."
}
```

### Detail / delete / reprocess (WAF-safe)

| Action | Method | URL |
|---|---|---|
| Detail (with up to 20 chunks) | `GET` | `/api/documents/file/?id=1` |
| Delete | `DELETE` | `/api/documents/file/?id=1` |
| Reprocess (async) | `POST` | `/api/documents/file/?id=1` |
| Reprocess wait | `POST` | `/api/documents/file/?id=1&sync=true` |

Delete `204`. Missing id `400` `{ "detail": "Document id is required." }`  
Not found `404` `{ "detail": "Document not found." }`

Local REST aliases (path mein numeric id; production WAF ke liye `file/?id=` prefer karo):

- `GET|DELETE /api/documents/<id>/`
- `POST /api/documents/<id>/reprocess/`

---

## 10. Staff: all user chats

Path mein `admin` word **production URL** par mat use karo. Frontend `/assets/vendor-inbox.js` use karta hai.

### `GET /api/chat/admin/sessions/`

```json
[
  {
    "id": "...",
    "title": "attendance policy",
    "created_at": "...",
    "updated_at": "...",
    "message_count": 4,
    "client_ip": "10.1.2.50",
    "username": "Guest",
    "email": "",
    "guest": true
  }
]
```

### One conversation

`GET /api/chat/admin/sessions/?session_id=<uuid>`

Same fields + `messages` array.

Local: `GET /api/chat/admin/sessions/<uuid>/`

---

## 11. Staff: website scraper

### `GET|POST /api/scraper/`

`GET`: website list (pages nahi).

`POST`:

```json
{ "url": "https://bahria.edu.pk" }
```

`201` naya, `200` existing domain update + rescrape.

### Item — `GET|POST|DELETE /api/scraper/item/?id=<id>`

| Method | Query | Result |
|---|---|---|
| `GET` | `id` | Detail + `pages[]` |
| `DELETE` | `id` | `204` + vectors remove |
| `POST` | `id&action=rescrape` | crawl again |
| `POST` | `id&action=reindex` | already-downloaded pages dubara index |

List item:

```json
{
  "id": 1,
  "seed_url": "https://bahria.edu.pk",
  "domain": "bahria.edu.pk",
  "title": "",
  "status": "completed",
  "status_label": "Completed",
  "progress_detail": "",
  "page_count": 12,
  "chunk_count": 80,
  "error_message": "",
  "last_scraped_at": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 12. Production URL map (campus WAF)

Production frontend **nahi** `/api/ask/` call karta. Nginx in files ko backend par map karta hai. Naya production app **yahi paths** use kare:

| Local Django | Production path |
|---|---|
| `GET /api/health/` | `/assets/vendor-health.js` |
| `GET /api/reply/` | `/assets/bootstrap.js` |
| `GET /api/ask/` | `/assets/polyfill.js` or `/query` |
| `GET /api/auth/csrf/` | `/assets/csrf.js` |
| `GET /api/auth/me/` | `/assets/user.js` |
| `POST /api/auth/login/` | `/assets/login.js` |
| `POST /api/auth/logout/` | `/assets/logout.js` |
| `GET\|POST /api/chat/sessions/` | `/assets/history.js` |
| `GET\|DELETE /api/chat/history/` | `/assets/thread.js` |
| `GET /api/dashboard/stats/` | `/assets/vendor-metrics.js` |
| `GET\|POST /api/documents/` | `/assets/vendor-catalog.js` |
| `GET /api/documents/categories/` | `/assets/vendor-taxonomy.js` |
| `GET\|POST\|DELETE /api/documents/file/` | `/assets/vendor-source.js` |
| `GET /api/chat/admin/sessions/` | `/assets/vendor-inbox.js` |
| `GET\|POST /api/scraper/` | `/assets/vendor-crawl.js` |
| `GET\|POST\|DELETE /api/scraper/item/` | `/assets/vendor-crawl-item.js` |
| Any other `/api/...` | `/assets/runtime/...` |

Query string same rehti hai, maslan:

```text
http://10.1.2.216/assets/polyfill.js?question=attendance%20policy&session_id=<uuid>
```

Staff web UI: `http://10.1.2.216/login` → `http://10.1.2.216/console`

---

## 13. Copy-paste examples (local)

CSRF + health:

```bash
curl -c cookies.txt -b cookies.txt http://127.0.0.1:8000/api/auth/csrf/
curl -b cookies.txt http://127.0.0.1:8000/api/health/
```

Ask (JSON):

```bash
curl -c cookies.txt -b cookies.txt \
  "http://127.0.0.1:8000/api/reply/?question=What%20is%20the%20attendance%20policy%3F"
```

Ask (SSE):

```bash
curl -N -c cookies.txt -b cookies.txt \
  -H "Accept: text/event-stream" \
  "http://127.0.0.1:8000/api/ask/?question=What%20is%20the%20attendance%20policy%3F"
```

Login + staff stats:

```bash
TOKEN=$(grep csrftoken cookies.txt | awk '{print $NF}')
curl -c cookies.txt -b cookies.txt -X POST http://127.0.0.1:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: $TOKEN" \
  -d '{"username":"YOUR_EMAIL","password":"YOUR_PASSWORD"}'

curl -b cookies.txt http://127.0.0.1:8000/api/dashboard/stats/
```

SSE client (JS):

```javascript
const params = new URLSearchParams({ question, session_id: sessionId || "" });
const res = await fetch(`http://127.0.0.1:8000/api/ask/?${params}`, {
  credentials: "include",
  headers: { Accept: "text/event-stream" },
});
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const parts = buffer.split("\n\n");
  buffer = parts.pop();
  for (const part of parts) {
    const line = part.split("\n").find((l) => l.startsWith("data:"));
    if (!line) continue;
    const event = JSON.parse(line.replace(/^data:\s?/, ""));
    if (event.type === "delta") render(event.text);      // replace, do not append
    if (event.type === "done") saveSession(event.session_id, event);
    if (event.type === "error") throw new Error(event.detail);
  }
}
```

---

## 14. CORS / native apps

Local Django `CORS_ALLOW_CREDENTIALS=true` hai. App kisi aur origin se ho to `.env` mein us origin ko add karo:

```env
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://YOUR_APP_ORIGIN
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:5173,http://YOUR_APP_ORIGIN
```

Mobile app same machine ke Django ko `http://127.0.0.1:8000` se hit kare. Android emulator ke liye aksar `http://10.0.2.2:8000`.

---

## 15. Endpoint checklist

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/health/` | public | |
| GET | `/api/auth/csrf/` | public | sets cookie |
| POST | `/api/auth/login/` | public | session cookie |
| POST | `/api/auth/logout/` | public | |
| GET | `/api/auth/me/` | public | |
| GET/POST | `/api/ask/` | public | SSE chat |
| GET/POST | `/api/reply/` | public | JSON chat |
| GET/POST | `/api/chat/` | public | same as reply |
| GET/POST | `/api/chat/stream/` | public | same as ask |
| GET/POST | `/api/chat/sessions/` | public | own sessions |
| GET/DELETE | `/api/chat/sessions/<uuid>/` | public | own session |
| GET/DELETE | `/api/chat/history/` | public | `?session_id=` |
| GET | `/api/chat/admin/sessions/` | staff | `?session_id=` for one |
| GET | `/api/dashboard/stats/` | staff | |
| GET/POST | `/api/documents/` | staff | list / upload |
| GET | `/api/documents/categories/` | staff | |
| GET/POST/DELETE | `/api/documents/file/` | staff | `?id=` |
| GET/DELETE | `/api/documents/<id>/` | staff | local REST |
| POST | `/api/documents/<id>/reprocess/` | staff | local REST |
| GET/POST | `/api/scraper/` | staff | |
| GET/POST/DELETE | `/api/scraper/item/` | staff | `?id=` `&action=` |

Django admin (not for the student app): `http://127.0.0.1:8000/django-admin/`

---

## 16. Local backend start (API test)

```powershell
cd "D:\behria chatbot"
.\.venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

Health check: [http://127.0.0.1:8000/api/health/](http://127.0.0.1:8000/api/health/)

Project setup, Groq key, aur Docker ke liye root `README.md` dekho.
