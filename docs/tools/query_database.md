# query_database

**Purpose:** NL → SQL via LangGraph (`classify → generate → execute`, 2 retries). Handles `what have I applied to` intents.

**Input**
```json
{"natural_language_query": "show me my applications"}
```

**Output:** Human-readable answer string with results or `"No results found."`.

**Scoping:** Every SELECT that reads `sessions`, `searches`, `search_results`, `chat_messages`, `user_profile`, `applications` MUST filter `WHERE user_id = current_setting('app.current_user_id')::int`. Never query `users`/`resumes` directly.

**Errors:** Returns `"I tried to query ... after 2 attempts: <error>"` on persistent failure.

**Version:** `v1` (inherits `Agent/db_query_agent.py` graph).
