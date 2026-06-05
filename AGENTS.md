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
- Always ask for approval before pushing code to the main branch.
- Do not deploy anything before receiving explicit consent.
