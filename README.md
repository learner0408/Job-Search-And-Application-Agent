# Job Search and Application Agent

Search job listings from Indeed, LinkedIn, and Y Combinator startups, chat with an AI agent about jobs, manage your profile and applications, and auto-apply via a local Playwright-powered browser agent.

## Architecture

### Backend (K8s pod)
- **`api/main.py`** — FastAPI server on port 8000. Endpoints: `POST /api/search`, `POST /api/chat`, `GET/DELETE /api/history`, `GET/POST /api/profile`, `POST/GET /api/resume`, `POST /api/apply/prepare`, `GET /api/applications`, `GET /api/sessions`, `GET /api/sessions/{id}/messages`, `DELETE /api/sessions/{id}`, `GET /api/health`.
- **`Agent/agent.py`** — LangChain ReAct agent using Groq (`llama-3.3-70b-versatile`), with 8 tools across 4 modules. Injects recent chat history for context (current message filtered to avoid duplication in LLM context). Auto-retries on rate limits (70s wait).
- **`database.py`** — SQLite persistence with tables: `sessions`, `searches`, `search_results`, `chat_messages`, `user_profile`, `applications`. FK-safe deletion, paginated history queries, session-scoped chat history.
- **`tools/job_search.py`** — DuckDuckGo search (`ddgs`) for Indeed, LinkedIn, and Y Combinator jobs. `search_jobs` @tool with `source` param ("all", "indeed", "linkedin", "yc"). Descriptions truncated to 120 chars.
- **`tools/history_tools.py`** — `list_search_history` and `get_search_results` @tools.
- **`tools/profile_tools.py`** — `get_my_profile` and `update_profile` @tools.
- **`tools/application_tools.py`** — `list_applications`, `get_application`, and `update_application_status` @tools.
- **`tools/__init__.py`** — Exports all tools for agent registration.

### Frontend (K8s pod)
- **`web/server.js`** — Express server (port 3000), proxies `/api/*` to FastAPI, serves static files with `Cache-Control: no-store`.
- **`web/public/index.html`** — Single-page vanilla HTML/CSS/JS UI. Chat is the primary home page. Left conversation sidebar (rounded card, last 5 sessions, "+ New" button, hover-to-delete). Hamburger menu navigating to Job Search and Applications full-page sections. Applications displayed as a tile grid with status filter. Slide-out History and Profile side panels. Apply button with cover letter generation modal.

### Local Agent (not in K8s)
- **`auto-apply/service.py`** — Runs on your machine. Auto-starts Playwright MCP via `npx @playwright/mcp`, exposes `POST /apply/{app_id}?api_base=<origin>` (opens headed browser, dismisses overlays, pre-fills forms, waits for manual submit, marks applied) and `GET /status/{app_id}`.

## Quick Start

### Local Development (API only, no K8s)
```bash
uv run python api/main.py
# → http://localhost:8000
```

### Local Agent (auto-apply)
```bash
uv run python auto-apply/service.py
# → http://127.0.0.1:8765
```

### Kubernetes (minikube)
```bash
# Build images into minikube's Docker daemon
eval $(minikube docker-env)
docker build -t job-api:latest -f Dockerfile.api --no-cache .
docker build -t job-web:latest -f Dockerfile.web --no-cache .

# Deploy
kubectl apply -f k8s/

# Mount DB for persistence (path with spaces must be properly quoted)
minikube mount "project/data:/data/job-db" &

# Access
minikube service web --url
# → http://127.0.0.1:xxxxx
```

## API Keys

Set in K8s secret `api-env`:
- `GROQ_API_KEY` — for the chat agent and cover letter generation

```bash
kubectl create secret generic api-env \
  --from-literal=GROQ_API_KEY=...
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `CHAT_LLM_MODEL` | `llama-3.3-70b-versatile` | Groq model for agent and cover letters |
| `CHAT_LLM_PROVIDER` | `groq` | LLM provider |
| `DB_PATH` | `/app/data/history.db` | SQLite database path |

## Deployment Layout

| Component | Container | K8s Service | Port |
|-----------|-----------|-------------|------|
| API | `job-api:latest` (Python 3.13) | ClusterIP `api:8000` | 8000 |
| Web | `job-web:latest` (Node.js 20) | NodePort `web:30080` | 3000 |

API keys injected via `envFrom` + secret `api-env`. DB stored at `/app/data/history.db` via hostPath volume.

## Key Files

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI routes |
| `Agent/agent.py` | LangChain ReAct agent (8 tools, auto-retry) |
| `database.py` | SQLite CRUD |
| `tools/job_search.py` | Job search @tool via DuckDuckGo |
| `tools/history_tools.py` | Search history @tools |
| `tools/profile_tools.py` | Profile @tools |
| `tools/application_tools.py` | Application @tools |
| `tools/__init__.py` | Tool exports |
| `web/public/index.html` | Single-page frontend |
| `auto-apply/service.py` | Local Playwright MCP agent |
| `Dockerfile.api` | API container build |
| `Dockerfile.web` | Web container build |
| `k8s/api-deployment.yaml` | API pod spec |
| `k8s/api-service.yaml` | API ClusterIP service |
| `k8s/web-deployment.yaml` | Web pod spec |
| `k8s/web-service.yaml` | Web NodePort service |
| `.dockerignore` | Excludes `.venv`, `.git`, `.env` from images |
| `AGENTS.md` | Agent instructions for development |
| `pyproject.toml` | Python dependencies and project config |
