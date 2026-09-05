# Bahria University Policy Bot

A private, local **RAG (Retrieval-Augmented Generation)** assistant for Bahria University policies.

Users ask policy questions in a chat UI. Answers are generated only from uploaded official documents (PDF, DOCX, TXT). The large language model runs on **Ollama** with **Gemma 3 4B**. Documents never leave the local server.

If a question is not covered by the knowledge base, the bot replies:

> I could not find this information in the available university policies.

It does not invent rules, dates, penalties, or procedures.

```text
User question
    → embed query (local)
    → local vector similarity search
    → relevant policy chunks (threshold filtered)
    → Ollama + Gemma 3 4B
    → answer + document/page citations
```

## 1. Requirements

- Python 3.11 or 3.12
- Node.js 20+
- [Ollama](https://ollama.com) running locally
- Optional: PostgreSQL 15+ (SQLite is the default for development)
- Optional: Docker Desktop

RAM: 8 GB minimum; 16 GB recommended for Gemma 3 4B.

## 2. Python setup

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 3. Ollama installation

Windows / macOS: install from [https://ollama.com/download](https://ollama.com/download).

Linux:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Confirm the API is up:

```bash
ollama --version
curl http://localhost:11434/api/tags
```

## 4. Gemma 3 model installation

Pull the official 4B Gemma 3 tag used by this project:

```bash
ollama pull gemma3:4b
```

Smoke test:

```bash
ollama run gemma3:4b "Reply with the word ready."
```

## 5. Embedding model setup

The default embedding provider is **Ollama** with `nomic-embed-text` (local, no cloud API):

```bash
ollama pull nomic-embed-text
```

Alternative (Python, still local):

```env
EMBEDDING_PROVIDER=sentence-transformers
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

Then install:

```bash
pip install "sentence-transformers>=3.0,<4"
```

Tests use `EMBEDDING_PROVIDER=lexical` (bag-of-words vectors) so they do not need Ollama.

## 6. Environment variables

Copy `.env.example` to `.env` and edit secrets. Important keys:

| Variable | Purpose | Default |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django secret | change in production |
| `DJANGO_DEBUG` | Debug mode | `true` |
| `DATABASE_ENGINE` | `sqlite` or `postgres` | `sqlite` |
| `OLLAMA_BASE_URL` | Local Ollama HTTP API | `http://localhost:11434` |
| `OLLAMA_MODEL` | Chat model | `gemma3:4b` |
| `EMBEDDING_PROVIDER` | `ollama`, `sentence-transformers`, or `lexical` | `ollama` |
| `EMBEDDING_MODEL` | Embedding model name | `nomic-embed-text` |
| `VECTOR_DB_PATH` | Local vector index directory | `./data/chroma` |
| `SIMILARITY_THRESHOLD` | Minimum cosine similarity | `0.28` |
| `RAG_TOP_K` | Chunks retrieved per question | `6` |
| `MAX_UPLOAD_MB` | Upload size limit | `20` |
| `PROCESS_DOCUMENTS_ASYNC` | Background indexing | `true` |

Never commit `.env`.

## 7. Database setup

Development (SQLite) needs no extra service. From `backend/`:

```powershell
cd backend
python manage.py migrate
```

Production PostgreSQL:

```env
DATABASE_ENGINE=postgres
POSTGRES_DB=bahria_policy_bot
POSTGRES_USER=bahria
POSTGRES_PASSWORD=choose-a-strong-password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

Then run `python manage.py migrate` again.

## 8. Running Django

```powershell
cd backend
python manage.py runserver 8000
```

API health:

```text
GET http://127.0.0.1:8000/api/health/
```

Django admin (optional):

```text
http://127.0.0.1:8000/django-admin/
```

## 9. Running the frontend

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Vite proxies `/api` to Django.

Production frontend build:

```powershell
cd frontend
npm run build
```

## 10. Creating an admin account

```powershell
cd backend
python manage.py create_admin --username admin --password "ChooseALongPassword" --email admin@bahria.edu.pk
```

Sign in at `/login`. Staff users can open **Admin Panel**.

## 11. Uploading documents

1. Log in as admin.
2. Open **Upload policy**.
3. Provide title, category, department, version, and a PDF / DOCX / TXT file.
4. The pipeline runs automatically:

```text
Upload → extract text → clean → chunk → embed → local vector index → searchable
```

Statuses: **Uploaded**, **Processing**, **Completed**, **Failed** (error shown on the document page).

Re-process or delete from the document list. Deleting a document also removes its vectors.

Load bundled **sample** policies (for local testing only — replace before production):

```powershell
cd backend
python manage.py load_sample_policies
```

## 12. Testing the chatbot

Example questions (after samples or official policies are indexed):

- What is the attendance policy?
- How many leaves can a student take?
- What is the procedure for academic probation?
- What is the policy for fee refunds?
- What are the requirements for a semester freeze?
- What is the examination policy?

Each answer shows **sources** (document name, page when available, relevance). Follow-up questions stay in the same session. **New Chat** starts a fresh conversation.

Questions outside the knowledge base should return the not-found sentence, not a guessed policy.

Backend tests (no GPU / no live Gemma required):

```powershell
cd backend
python manage.py test
```

## 13. Docker deployment

Ollama runs **inside Compose** (`ollama` service). The first start pulls `gemma3:4b` and `nomic-embed-text` into a Docker volume. That download can take several minutes.

```powershell
copy .env.example .env
docker compose up --build
```

- App: [http://localhost](http://localhost)
- API: [http://localhost:8000/api/health/](http://localhost:8000/api/health/)

Create an admin user:

```powershell
docker compose exec backend python manage.py create_admin --username admin --password "ChooseALongPassword"
```

## 14. Production deployment

1. Set `DJANGO_DEBUG=false` and a long `DJANGO_SECRET_KEY`.
2. Use PostgreSQL (`DATABASE_ENGINE=postgres`).
3. Set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` to the real HTTPS origin.
4. Serve the Vite `dist/` folder (or the frontend container) behind HTTPS.
5. Keep Ollama on the private network; do not expose it publicly.
6. Replace sample policies with official Bahria University documents.
7. Restrict `/api/documents/` to staff (already enforced).
8. Run `python manage.py collectstatic` and put media/chroma on persistent disks.
9. Take backups of PostgreSQL, `data/documents/`, and `data/chroma/`.

Gunicorn (already used in Docker):

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --timeout 180
```

## Project layout

```text
bahria-policy-bot/
├── backend/          Django + DRF (accounts, documents, chat, rag, api)
├── frontend/         React + Vite chatbot and admin UI
├── sample_policies/  Demonstration TXT policies (not official)
├── data/             Uploads and local vector index (gitignored content)
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

Uploads and the vector index live under `data/`. The default index is a **local cosine store** (JSON files). It needs no cloud service and no C++ compiler.

REST examples:

```text
POST   /api/auth/login/
POST   /api/chat/
GET    /api/chat/history/?session_id=
POST   /api/documents/
GET    /api/documents/
GET    /api/documents/{id}/
DELETE /api/documents/{id}/
POST   /api/documents/{id}/reprocess/
GET    /api/dashboard/stats/
GET    /api/health/
```
