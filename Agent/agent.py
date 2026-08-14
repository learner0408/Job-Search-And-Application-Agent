import os
import json
import logging
import time
from typing import Any, Optional
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage
from langgraph.prebuilt import create_react_agent

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful job search and career assistant with direct access to tools. "
    "When the user asks to search jobs, update profile, query data, etc., "
    "call the appropriate tool immediately without asking for confirmation. "
    "Tools: search_jobs, query_database, get_database_schema, update_profile, update_application_status. "
    "Use query_database for any questions about saved data (profile, applications, search history). "
    "Be concise."
)


def _safe_text(content: Any) -> str:
    """Extract plain text from an LLM content value.

    Args:
        content: A string, a list of content blocks, or another value.

    Returns:
        The text representation of the content.
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _parse_search_result(tool_content: str) -> dict[str, Any]:
    """Parse a search_jobs tool result into normalized job dicts.

    Args:
        tool_content: JSON string produced by the search_jobs tool.

    Returns:
        Dict with a "jobs" key listing normalized job listings.
    """
    data = json.loads(tool_content)
    jobs = []
    for source_key, source_label in [
        ("indeed", "Indeed"),
        ("linkedin", "LinkedIn"),
        ("yc_startups", "YC Startup"),
    ]:
        for item in data.get(source_key, []):
            jobs.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "location": item.get("location") or "N/A",
                "salary": item.get("salary") or "N/A",
                "link": item.get("link", ""),
                "source": source_label,
            })
    return {"jobs": jobs}


def run_agent(message: str, session_id: Optional[str] = None, user_id: Optional[int] = None, email: Optional[str] = None) -> dict[str, Any]:
    """Run the job-search assistant agent on a user message.

    Args:
        message: The user's chat message.
        session_id: Optional session id used to load recent conversation history.
        user_id: The authenticated user's id, used to scope data access.
        email: The authenticated user's email, used to scope data access.

    Returns:
        Dict with a "reply" string and an optional "jobs" list of job listings.
    """
    logger.info("Agent message (user %s): '%s'", user_id, message[:120])

    model_name = os.getenv("CHAT_LLM_MODEL", "claude-sonnet-5")
    provider = os.getenv("CHAT_LLM_PROVIDER", "anthropic")
    llm = init_chat_model(model_name, model_provider=provider)

    from tools import (
        search_jobs, update_profile, update_application_status,
        query_database, get_database_schema,
    )

    system_prompt = _SYSTEM_PROMPT
    if user_id is not None:
        system_prompt += (
            f"\nThe current user is user_id={user_id} (email: {email}). "
            "All database queries MUST be scoped to this user via a user_id filter. "
            "Never show or reference other users' data."
        )

    agent = create_react_agent(
        llm,
        [
            search_jobs, update_profile, update_application_status,
            query_database, get_database_schema,
        ],
        prompt=system_prompt,
    )

    input_messages: list = [("human", message)]
    try:
        from database import get_recent_chat_messages
        recent = get_recent_chat_messages(6, session_id=session_id, user_id=user_id)
        if recent:
            recent = [m for m in recent if not (m["role"] == "user" and m["content"] == message)]
            if recent:
                history: list = []
                for m in recent:
                    if m["role"] == "agent" and m["content"].startswith(("Sorry", "It seems")):
                        continue
                    role = "human" if m["role"] == "user" else "ai"
                    history.insert(0, (role, m["content"]))
                input_messages = history + input_messages
    except Exception:
        pass

    try:
        result = agent.invoke({"messages": input_messages})
    except Exception as e:
        err_str = str(e)
        if "rate_limit_exceeded" in err_str or "413" in err_str:
            logger.warning("Rate limited, waiting 70s before retry...")
            time.sleep(70)
            try:
                result = agent.invoke({"messages": input_messages})
            except Exception as e2:
                logger.error("Agent retry also failed: %s", e2)
                return {"reply": f"Sorry, something went wrong: {e2}", "jobs": None}
        else:
            logger.error("Agent invocation failed: %s", e)
            return {"reply": f"Sorry, something went wrong: {e}", "jobs": None}

    messages = result.get("messages", [])
    search_result = None

    last = messages[-1] if messages else None
    reply = ""
    if isinstance(last, AIMessage) and not last.tool_calls and last.content:
        reply = _safe_text(last.content)
    if not reply:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
                reply = _safe_text(msg.content)
                break
        if not reply:
            reply = _safe_text(last.content if last else None) or "No response."

    for msg in messages:
        if msg.type == "tool" and msg.name == "search_jobs":
            if isinstance(msg.content, list):
                text = "".join(
                    b.get("text", "") for b in msg.content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                if text:
                    search_result = _parse_search_result(text)
            elif msg.content:
                search_result = _parse_search_result(msg.content)

    logger.info("Agent reply: '%s'", reply[:120])

    out: dict[str, Any] = {"reply": reply}
    if search_result:
        out["jobs"] = search_result["jobs"]
    else:
        out["jobs"] = None
    return out
