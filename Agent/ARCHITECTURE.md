# Architecture: Job Search and Application Agent — Agent Subsystem

## 1. Project Overview

Agent subsystem that routes dynamic natural-language job intents through a hybrid-planner orchestrator to help users search and apply for jobs. Examples: “Get me open positions for AI Engineer in Bangalore”, “Apply to 'X' job which matches my profile”, “What applications have I applied to?”. The planner generates a validated DAG of tools per query, so mixes like `db-read + search` are handled without a fixed pipeline. Goals are intent coverage, source extensibility, and auditable matching. Non-goals for this doc are cloud auto-apply hardening and resume object-storage migration (tracked in `BACKLOG.md`).

## 2. Tech Stack

Python 3.13, `uv`, LangChain + LangGraph, `langchain-anthropic` via `init_chat_model` everywhere (check LangChain/LangGraph abstractions first before adding new helpers). Models: `claude-haiku-4-5` for high-volume planner + scoring rubric (`Agent/prompts/planner/v1.md`, `Agent/prompts/scoring/v1.md`) and `claude-sonnet-5` for chat (`Agent/prompts/chat/v1.md`), versioned per node. Search via `ddgs` (DuckDuckGo) behind a `JobSource` interface; persistence via `psycopg2` + Neon Postgres; testing via `pytest` + `/test-case-writer`.

## 3. Directory Structure

Layered by category — first-order children only.

```
Agent/
├── agent.py
├── db_query_agent.py
├── orchestrator.py
├── planner/
├── prompts/
│   ├── planner/
│   ├── scoring/
│   └── chat/
└── ARCHITECTURE.md
tools/
├── search/
├── profile/
├── scoring/
├── application/
├── db/
└── README.md
docs/
├── tools/
└── guidelines.md
```

- `Agent/` — orchestrator, planner + validator, prompts, and this arch doc
- `tools/search`, `tools/profile`, `tools/scoring`, `tools/application`, `tools/db` — category subfolders, each tool has a succinct `docs/tools/<tool>.md`
- `docs/tools/` — per-tool contracts
- `docs/guidelines.md` — validator, prompt-version, and cache rules

## 4. Module / Component Breakdown

| Module | Responsibility | Files |
|---|---|---|
| orchestrator | Intent classification, DAG validation, LangGraph wiring, ephemeral state + checkpoint | `Agent/orchestrator.py`, `Agent/planner/planner.py`, `Agent/planner/validator.py` |
| search | Dispatch to `JobSource` implementations, 24h cache, normalize description/location/salary | `tools/search/` |
| profile | Assemble profile snapshot (structured + resume ~2k tokens + preferences) | `tools/profile/` |
| scoring | Hybrid scorer: deterministic 0–50 + LLM rubric 0–50, filter by threshold with breakdown | `tools/scoring/` |
| application | Draft preview without mandatory cover letter, persist `status: preparing` | `tools/application/` |
| db-read | Natural-language to SQL via LangGraph state machine, user-scoped reads | `tools/db/`, `Agent/db_query_agent.py` |
| chat | Thin router for general conversation, delegates job intents to orchestrator | `Agent/agent.py` |

## 5. Data Flow

How data moves between modules. Mark each flow `[Input] → [Step] → [Output]`.

- `[User NL query] → [Agent/orchestrator classify_intent] → [intent]`
- `[NL query with mixed intent] → [planner proposes DAG {"steps":[{tool,args,depends_on}]} → validator] → [validated DAG]`
- `[search intent] → [tools/search job_search → JobSource + 24h cache] → [jobs[]]`
- `[mixed search + profile intent] → [tools/profile get_profile (structured + resume + preferences) || tools/search in parallel] → [profile_snapshot, jobs[]]`
- `[profile_snapshot + jobs[] + threshold] → [tools/scoring score_jobs (deterministic 50 + scoring/v1.md rubric 50)] → [scored_jobs[] with score + breakdown] → [code filter score ≥ threshold]`
- `[scored_jobs[]] → [tools/application prepare_application → DB status: preparing] → [preview: top scored jobs + breakdown]`
- `[User "apply to #2" / "apply to all ≥70%"] → [orchestrator resumes via checkpoint] → [application status update → status: applied]`
- `[DB-read intent] → [tools/db query_database (classify → generate → execute)] → [human-readable rows]`, all reads filtered `WHERE user_id = current_setting('app.current_user_id')`

## 6. Contracts & Interfaces

- `JobSource` interface: `search(title, location, employment_type, remote, max_results) → jobs[]` implemented by `tools/search/sources/base.py`, `indeed.py`, `linkedin.py`, `yc.py`
- Per-tool JSON I/O: `docs/tools/search_jobs.md`, `docs/tools/get_profile.md`, `docs/tools/score_jobs.md`, `docs/tools/prepare_application.md`, `docs/tools/query_database.md`
- `OrchestratorState` TypedDict: `user_id, session_id, raw_query, intent, profile_snapshot, jobs, scored_jobs, threshold, drafts, error`
- Planner DAG schema: `{"steps": [{id, tool, args, depends_on}]}` — `depends_on` references prior `id`s

## 8. Conventions Reference

Project `CONVENTIONS.md` at the repo root applies: concise code, docstrings with `Args:`/`Returns:`, ask-before-edit/push, LangChain by default and LangGraph for custom flows, check LangChain/LangGraph abstractions first before creating new helpers, keep `README.md` current. Deviation: agent architecture lives at `Agent/ARCHITECTURE.md` (not project-root) because this doc scopes to the agent subsystem only; tool contracts live at `docs/tools/<tool>.md` with a mapping in `tools/README.md` and guardrails in `docs/GUARDRAILS.md`.

## 9. Open Questions

No unresolved architecture decisions at write time. Deferred follow-ups are tracked in `BACKLOG.md`: planner re-plan on node failure (R3), preferences storage shape for profile, embedding provider for deterministic skills overlap, checkpoint cutover from `MemorySaver` to Postgres, and real job-search API selection.
