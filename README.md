# Job Search and Application Agent

Search job listings from Indeed, LinkedIn, and Y Combinator startups via a single API.

## Architecture

- **`tools/job_search.py`** — DuckDuckGo search (`ddgs`) targeted at `site:indeed.com`, `site:linkedin.com/jobs`, and `site:workatastartup.com/jobs`. Returns structured JSON with title, description, location, salary, and link.
- **`api/main.py`** — FastAPI server on port 8000. Exposes `POST /api/search` and `GET /api/health`. Logs every search request and result counts.
- **`web/`** — Express server (port 3000) that proxies `/api/*` to the FastAPI backend and serves a vanilla HTML/CSS/JS frontend.
- **`main.py`** — LangChain agent entry point (Gemini 2.0 Flash via `google_genai` provider).

## Quick Start

### 1. API

```bash
uv run python api/main.py
# → http://localhost:8000
```

### 2. Frontend

```bash
cd web && npm install && node server.js
# → http://localhost:3000
```

Search for any job title + location from the web UI.

## API

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"AI Engineer","location":"Bangalore","max_results":10}'
```

## Environment

- Python 3.13+ (managed via `uv`)
- Node.js 18+ (for the web frontend)
- `GEMINI_API_KEY` in `.env` (only needed for the LangChain agent in `main.py`)
