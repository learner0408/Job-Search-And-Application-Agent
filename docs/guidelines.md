# Guidelines

## Validator (B3)

`agent/planner/validator.py` checks before executor runs:
- `tool` in allowed set derived from `tools/registry.py` (`search_jobs`, `get_profile`, `score_jobs`, `prepare_application`, `apply_application`, `query_database`); the planner prompt allow-list block (`{{TOOLS}}`) renders from the same registry — adding a tool is one registry entry
- Arg JSON schema per `docs/tools/<tool>.md`
- `user_id` scoping present when tool reads `user_profile`/`applications`/etc. (`current_setting('app.current_user_id')`)
- If query contains `match|≥70%|%`, plan must include `score_jobs` with `threshold`

Invalid plan → validator returns error, planner re-asks once.

## Prompt versioning (L2+L3)

- Prompts live at `agent/prompts/<node>/v1.md` (`planner`, `scoring`, `chat`)
- Env `PROMPT_VERSION` per node (default `v1`); bump file, not code, for rubric/weight changes
- Models: `PLANNER_LLM_MODEL=claude-haiku-4-5` (planner + `score_jobs` rubric), `CHAT_LLM_MODEL=claude-sonnet-5` (chat)

## Cache (J2)

`search_results` 24h cache keyed by `(title, location, source, employment_type, remote, max_results)`. Cache hit skips DDG. Invalidate on `source` impl change (minor version bump per `docs/tools/search_jobs.md`).

## Retries (R2)

- LLM: exponential backoff 1s → 7s → 70s; final fallback deterministic-only score
- DDG: 2× retry with jitter → cache fallback

## Eval (E3)

Via `/test-case-writer`: `tests/scoring/test_deterministic.py`, `tests/scoring/golden_jobs.json` (±5 bands), `tests/planner/golden_plans.json`. CI runs deterministic always, LLM-gated.

## Tool versioning

Bump `docs/tools/<tool>.md` minor version on weight/threshold change; major on schema change. Update `tools/README.md` table.

## R3 (backlog)

Planner re-plan on node failure (e.g., DDG `all` → retry `linkedin` only) is deferred; see `BACKLOG.md`.
