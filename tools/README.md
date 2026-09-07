# Tools

| Tool | Category | Purpose | Contract | Location |
|------|----------|---------|----------|----------|
| `search_jobs` | search | Search Indeed/LinkedIn/YC via `JobSource` interface + 24h cache (J2) | `docs/tools/search_jobs.md` | `tools/search/job_search.py` |
| `get_profile` | profile | Assemble `profile_snapshot` (P3: structured + resume + preferences) | `docs/tools/get_profile.md` | `tools/profile/get_profile.py` |
| `score_jobs` | scoring | Hybrid 0–50 deterministic + 0–50 LLM rubric, filter `>= threshold` (S3) | `docs/tools/score_jobs.md` | `tools/scoring/score_jobs.py` |
| `prepare_application` | application | Draft preview, no cover letter, persists `status: preparing` (A2) | `docs/tools/prepare_application.md` | `tools/application/prepare_application.py` |
| `query_database` | db | NL → SQL LangGraph, user-scoped reads | `docs/tools/query_database.md` | `tools/db/query_database.py` |
| `get_database_schema` | db | Schema introspection for `query_database` | `docs/tools/query_database.md` | `tools/db/get_database_schema.py` |

Guidelines: `docs/guidelines.md` — validator, prompt version (`Agent/prompts/<node>/v1.md`), cache TTL. Decisions: `Agent/ARCHITECTURE.md`.
