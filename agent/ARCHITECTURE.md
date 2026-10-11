# Architecture: Job Search and Application Agent — Agent Subsystem

## 1. Project Overview

Agent subsystem that routes dynamic natural-language job intents through a hybrid-planner orchestrator to help users search and apply for jobs. Examples: “Get me open positions for AI Engineer in Bangalore”, “Apply to 'X' job which matches my profile”, “What applications have I applied to?”. The planner generates a validated DAG of tools per query, so mixes like `db-read + search` are handled without a fixed pipeline. Goals are intent coverage, source extensibility, and auditable matching. Non-goals for this doc are cloud auto-apply hardening and resume object-storage migration (tracked in `BACKLOG.md`).

## 2. Tech Stack

Python 3.13, `uv`, LangChain + LangGraph, `langchain-anthropic` via `init_chat_model` everywhere (check LangChain/LangGraph abstractions first before adding new helpers). Models: `claude-haiku-4-5` for high-volume planner + scoring rubric (`agent/prompts/planner/v1.md`, `agent/prompts/scoring/v1.md`) and `claude-sonnet-5` for chat (`agent/prompts/chat/v1.md`), versioned per node. Search via Adzuna official API + `ddgs` (DuckDuckGo) behind a `JobSource` interface (primaries Adzuna/YC, fallbacks LinkedIn/Indeed); persistence via `psycopg2` + Neon Postgres; testing via `pytest` + `/test-case-writer`.

## 3. Directory Structure

Layered by category — first-order children only.

```
agent/
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

- `agent/` — orchestrator, planner + validator, prompts, and this arch doc
- `tools/search`, `tools/profile`, `tools/scoring`, `tools/application`, `tools/db` — category subfolders, each tool has a succinct `docs/tools/<tool>.md`
- `docs/tools/` — per-tool contracts
- `docs/guidelines.md` — validator, prompt-version, and cache rules

## 4. Module / Component Breakdown

| Module | Responsibility | Files |
|---|---|---|
| orchestrator | Intent classification, DAG validation, LangGraph wiring, ephemeral state + checkpoint | `agent/orchestrator.py`, `agent/planner/planner.py`, `agent/planner/validator.py` |
| search | Dispatch to `JobSource` implementations, 24h cache, normalize description/location/salary | `tools/search/` |
| profile | Assemble profile snapshot (structured + resume ~2k tokens + preferences) | `tools/profile/` |
| scoring | Hybrid scorer: deterministic 0–50 + LLM rubric 0–50, filter by threshold with breakdown | `tools/scoring/` |
| application | Draft preview without mandatory cover letter, persist `status: preparing` | `tools/application/` |
| db-read | Natural-language to SQL via LangGraph state machine, user-scoped reads | `tools/db/`, `agent/db_query_agent.py` |
| chat | Thin router for general conversation, delegates job intents to orchestrator | `agent/agent.py` |

## 5. Data Flow

How data moves between modules. Mark each flow `[Input] → [Step] → [Output]`.

```
[Browser chat] → [Express /api/* proxy] → [POST /api/chat] → [run_agent]
                                                                     |
                                                         [guard: on_topic?]
                                                          /           \
                                                    (reject)         (pass)
                                                        |               |
                                              [format: refusal]  [classify: 1-of-7 intent]
                                                                 /  |  |  |  |  |  \
                                                                /   |  |  |  |  |   \
                                                     [search] [plan→execute] [db_read] [profile] [update] [apply] [chat→ReAct]
                                                      (plain)  (score+   (SQL)  (snapshot (writes) (tool (fallback
                                                                prepare)          +summary)          dummy)  renderer)
                                                                    \     |     /
                                                                     \    |    /
                                                                      [format reply]
```

| Intent | Example query | Path |
|---|---|---|
| `search` | "AI Engineer jobs in Bangalore" | `[search] → [profile] → [score] → [prepare] → [format]` |
| `search_score_prepare` | "…match ≥70%" | `[plan] → [execute: validated DAG] → [format]` (invalid plan → fixed chain) |
| `db_read` | "what have I applied to" | `[db_read: classify → generate → execute] → [format]` |
| `profile` | "fetch my resume" | `[profile snapshot] → [cached LLM summary] → [format]` |
| `update` | "update my phone" | `[extract args] → [validated write] → [format]` |
| `apply` | "apply to #2" | `[resolve #N/all] → [apply_application tool] → [format]` |
| `chat` / `off_topic` | "hello" / joke | `[ReAct renderer]` / `[refusal]` |

## 6. Contracts & Interfaces

- `JobSource` interface: `search(title, location, employment_type, remote, max_results) → jobs[]` implemented by `tools/search/sources/base.py`, `adzuna.py`, `indeed.py`, `linkedin.py`, `yc.py` (primaries Adzuna/YC, fallbacks LinkedIn/Indeed on shortfall)
- Per-tool JSON I/O: `docs/tools/search_jobs.md`, `docs/tools/get_profile.md`, `docs/tools/score_jobs.md`, `docs/tools/prepare_application.md`, `docs/tools/apply_application.md`, `docs/tools/query_database.md`
- `OrchestratorState` TypedDict: `user_id, session_id, raw_query, intent, profile_snapshot, jobs, scored_jobs, threshold, drafts, error`
- Planner DAG schema: `{"steps": [{id, tool, args, depends_on}]}` — `depends_on` references prior `id`s

## 8. Conventions Reference

Project `AGENTS.md` at the repo root applies (`CLAUDE.md` points there): concise code, docstrings with `Args:`/`Returns:`, ask-before-edit/push, LangChain by default and LangGraph for custom flows, check LangChain/LangGraph abstractions first before creating new helpers, keep `README.md` current. Deviation: agent architecture lives at `agent/ARCHITECTURE.md` (not project-root) because this doc scopes to the agent subsystem only; tool contracts live at `docs/tools/<tool>.md` with a mapping in `tools/README.md` and guardrails in `docs/GUARDRAILS.md`.

## 9. Open Questions

No unresolved architecture decisions at write time. Deferred follow-ups are tracked in `BACKLOG.md`: planner re-plan on node failure (R3), preferences storage shape for profile, embedding provider for deterministic skills overlap, checkpoint cutover from `MemorySaver` to Postgres, and real job-search API selection.
