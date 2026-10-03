# Job Search and Application Agent

Search job listings from Indeed, LinkedIn, and Y Combinator startups, chat with an AI agent about jobs, manage your profile and applications, and auto-apply via a local Playwright-powered browser agent.

## Architecture

### Backend (K8s pod)
- **`api/main.py`** — FastAPI server on port 8000. Endpoints: `POST /api/search`, `POST /api/chat`, `GET/DELETE /api/history`, `GET/POST /api/profile`, `POST/GET /api/resume`, `POST /api/apply/prepare`, `GET /api/applications`, `GET /api/sessions`, `GET /api/sessions/{id}/messages`, `DELETE /api/sessions/{id}`, `GET /api/health`.
- **`Agent/agent.py`** — LangChain ReAct agent using Groq (`llama-3.3-70b-versatile`), with 4 tools. Injects recent chat history for context (current message filtered to avoid duplication in LLM context). Auto-retries on rate limits (70s wait).
- **`Agent/db_query_agent.py`** — LangGraph state machine that classifies user queries against the DB schema, generates & executes SQL (with up to 2 retries on failure), and returns results.
- **`database.py`** — PostgreSQL persistence (via `psycopg2`, Neon-compatible) with tables: `sessions`, `searches`, `search_results`, `chat_messages`, `user_profile`, `applications`. Includes `get_schema_metadata()` for introspection and `execute_sql_query()` for safe SELECT-only execution via the SQL agent.
- **`tools/job_search.py`** — DuckDuckGo search (`ddgs`) for Indeed, LinkedIn, and Y Combinator jobs. `search_jobs` @tool with `source` param ("all", "indeed", "linkedin", "yc"). Descriptions truncated to 120 chars.
- **`tools/profile_tools.py`** — `update_profile` @tool (write-only; reads handled by `query_database`).
- **`tools/application_tools.py`** — `update_application_status` @tool (write-only; reads handled by `query_database`).
- **`tools/db_query_tool.py`** — `query_database` @tool wrapping the LangGraph SQL agent. Handles all DB read queries (profile, applications, search history, chat messages).
- **`tools/database_metadata_tool.py`** — `get_database_schema` @tool for schema introspection (used internally by the SQL agent).
- **`tools/__init__.py`** — Exports 6 tools: `search_jobs`, `query_database`, `update_profile`, `update_application_status`, `get_database_schema`, and search helpers.

### Frontend (K8s pod)
- **`web/server.js`** — Express server (port 3000), proxies `/api/*` to FastAPI, serves static files with `Cache-Control: no-store`.
- **`web/public/index.html`** — Single-page vanilla HTML/CSS/JS UI. Chat is the primary home page. Left conversation sidebar (rounded card, last 5 sessions, "+ New" button, hover-to-delete). Hamburger menu navigating to Job Search and Applications full-page sections. Applications displayed as a tile grid with status filter. Slide-out Profile side panel. Apply button with cover letter generation modal.

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
| `CHAT_LLM_MODEL` | `claude-sonnet-5` | Anthropic model for the chat agent (chat agent and DB query agent) |
| `CHAT_LLM_PROVIDER` | `anthropic` | LLM provider for the chat agent |
| `DATABASE_URL` | `postgresql://user:pass@host/db` | PostgreSQL connection string (Neon or local Postgres) |

## Deployment Layout

| Component | Container | K8s Service | Port |
|-----------|-----------|-------------|------|
| API | `job-api:latest` (Python 3.13) | ClusterIP `api:8000` | 8000 |
| Web | `job-web:latest` (Node.js 20) | NodePort `web:30080` | 3000 |

API keys injected via `envFrom` + secret `api-env`. Database connected via `DATABASE_URL` env var. Local K8s deployment is scaled down — the app is deployed on Fly.io with Neon Postgres. Manifests remain for reference.

**Keep-alive (cron-job.org + self-warm):** Render's free tier spins down after 15 minutes idle (~55s cold boot on the next request, which exceeds cron-job.org's 30s timeout and 64 KB response cap). Two cron-job.org jobs run every 10 minutes (schedule `*/10 11-22 * * *`, timezone Asia/Tokyo) and hit only the fast web service: one `GET https://jobsearch-web.onrender.com/ping`, the other `GET https://jobsearch-web.onrender.com/wake`. The `/wake` endpoint replies `ok` instantly and, in background, pings `https://jobsearch-api-1bbx.onrender.com/api/health`; the web service also self-warms the API every 10 minutes while running. This exposes the API only to the web service's own `fetch` (which waits out the slow boot), keeping it warm for the 11:00–22:59 JST window. Both services sleep overnight; upgrading to Render Starter ($7/mo/service) removes spin-down entirely.

## Fly.io Deployment

### Deploy API
```bash
cd fly/api
fly secrets set DATABASE_URL=$NEON_URL ANTHROPIC_API_KEY=$ANTHROPIC_KEY
fly launch --dockerfile ../../Dockerfile.api
```

### Deploy Web
```bash
cd fly/web
fly secrets set API_URL=http://jobsearch-api.internal:8000
fly launch --dockerfile ../../Dockerfile.web
```

### Set up Cloudflare DNS
```bash
fly ips list -a jobsearch-web
# Use the IPv4 address in Cloudflare DNS A record
```

## Database Migration

One-time migration scripts live in `scripts/` and are **gitignored** (run locally, not deployed).

To migrate existing data from a local SQLite database to a fresh Neon Postgres instance:

```bash
DATABASE_URL=postgresql://user:pass@ep-xxx.neon.tech/neondb uv run python scripts/migrate_to_postgres.py
```

To upgrade an existing (single-user) Postgres database to the multi-user schema — **wipes all existing data**; run once before deploying the multi-user release:

```bash
DATABASE_URL=postgresql://user:pass@ep-xxx.neon.tech/neondb uv run python scripts/migrate_multiuser.py
```

## Prerequisites

- **Python 3.13+** (managed via `uv`)
- **PostgreSQL** — either local instance or a [Neon](https://neon.tech) serverless Postgres (free tier: 500MB, never sleeps)
- **API keys** for LLM providers (Anthropic, Groq, etc.)

## Key Files

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI routes |
| `api/auth.py` | JWT, Argon2 password hashing, OAuth (Google/GitHub), rate limiting, session cookies |
| `Agent/agent.py` | LangChain ReAct agent (4 tools, auto-retry) |
| `Agent/db_query_agent.py` | LangGraph SQL query agent (classify → generate → execute, 2 retries) |
| `database.py` | PostgreSQL CRUD + `get_schema_metadata()` + `execute_sql_query()` |
| `tools/job_search.py` | Job search @tool via DuckDuckGo |
| `tools/db_query_tool.py` | `query_database` @tool (natural language → SQL) |
| `tools/database_metadata_tool.py` | `get_database_schema` @tool |
| `tools/profile_tools.py` | `update_profile` @tool |
| `tools/application_tools.py` | `update_application_status` @tool |
| `tools/__init__.py` | Tool exports |
| `web/public/index.html` | Single-page frontend |
| `web/public/login.html` | Login / signup entry point (email/password + Google/GitHub) |
| `auto-apply/service.py` | Local Playwright MCP agent |
| `Dockerfile.api` | API container build |
| `Dockerfile.web` | Web container build |
| `k8s/api-deployment.yaml` | API pod spec |
| `k8s/api-service.yaml` | API ClusterIP service |
| `k8s/web-deployment.yaml` | Web pod spec |
| `k8s/web-service.yaml` | Web NodePort service |
| `.dockerignore` | Excludes `.venv`, `.git`, `.env` from images |
| `scripts/` | One-time migration scripts (gitignored, run locally) |

## Future Scope

Planned work deferred from the current release:

- **Cloud auto-apply**: move `auto-apply/service.py` (Playwright) onto a separate Render service with per-user delegated context and short-lived tokens; `SERVICE_TOKEN` will be removed once this ships.
- **Email verification & password reset**: account activation emails and self-service password reset (currently "contact admin").
- **Resume object storage**: move resume blobs from Postgres `bytea` to S3/R2.
| `AGENTS.md` | Agent instructions for development |
| `pyproject.toml` | Python dependencies and project config |
