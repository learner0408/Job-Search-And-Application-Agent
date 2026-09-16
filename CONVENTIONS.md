## Conventions
- Keep code concise. No unnecessary comments.
- Follow existing patterns in the codebase.
- Always ask before editing any files.
- Always ask for permission before pushing anything to git (any branch).
- Use LangChain by default when creating simple agents and Langgraph for custom flows unless otherwise specified.
- Do not deploy anything before receiving explicit consent. Only build Docker images, restart pods, or deploy when explicitly asked.
- If not sure about some tool or function, check official documentation first before generating the response. For Example - If unsure about a query related to influxDB, seach official influxDB documentation first etc
- Keep the README.md up-to-date as new edits are performed
- Every function must have a concise docstring with `Args:`/`Returns:` sections describing parameters and return value. Just enough context to understand what the function does and how to call it — no extensive prose.