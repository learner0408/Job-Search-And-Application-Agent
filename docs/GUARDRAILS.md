# Guardrails

Hard boundaries the agent architecture enforces.

- Orchestrator never calls the database or scorer directly — only via `get_profile`, `query_database`, and `score_jobs` tools.
- Scoring never triggers `prepare_application`; threshold filtering (`score >= threshold`) happens in code before any draft is created.
- No tool reads `users` or `resumes` directly — use `get_profile` snapshot; every read that touches `sessions`, `searches`, `search_results`, `chat_messages`, `user_profile`, or `applications` filters `WHERE user_id = current_setting('app.current_user_id')::int`. `get_resume_text` parses PDF via `pypdf2` / DOCX via `python-docx` based on `mime_type`; `resumes` stays blocked for `query_database`.
- Planner never invents tools — validator rejects any `tool` not in `tools/README.md` allow-list and validates arg JSON schemas and `user_id` scoping.
- Apply never auto-submits — `prepare_application` only creates `status: preparing`; actual apply requires an explicit second user turn (`"apply to #N"` or `"apply to all ≥70%"`).
- Check existing LangChain and LangGraph abstractions first before introducing new helpers; use `init_chat_model` everywhere.
- Input scope: the entry guard node admits only career-domain queries (jobs, applications, resume/profile, interviews, salary, employment skills, cover letters); off-topic input gets a refusal with no tools or writes. Mixed queries with any job content pass through.
