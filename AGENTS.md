# Agent Instructions

## Environment
- Use `uv run python ...` instead of plain `python` for running scripts (packages are managed via uv).
- To add packages: `uv add <package>`.
- The virtual environment is managed by uv — do not use pip directly.

## Project Structure
- `models/` — contains the LLM factory (`get_llm` function) for connecting to OpenAI, Anthropic, Ollama, and Llama.cpp.
- `main.py` — entry point.
- `pyproject.toml` — project config and dependencies.

## Conventions
- Keep code concise. No unnecessary comments.
- Follow existing patterns in the codebase.
- Always ask before editing any files.
- Always ask for permission before pushing anything to git (any branch).
- Use LangChain by default when creating agents unless otherwise specified.
- Do not deploy anything before receiving explicit consent. Only build Docker images, restart pods, or deploy when explicitly asked.
- If not sure about some tool or function, check official documentation first before generating the response. For Example - If unsure about a query related to influxDB, seach official influxDB documentation first etc
- Keep the README.md up-to-date as new edits are performed
