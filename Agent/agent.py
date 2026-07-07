import os
import json
import logging
import time
from typing import Any, Optional
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.prebuilt import create_react_agent

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful job search and career assistant with direct access to tools. "
    "When the user asks to search jobs, update profile, query data, etc., "
    "call the appropriate tool immediately without asking for confirmation. "
    "Tools: search_jobs, query_database, update_profile, update_application_status. "
    "Use query_database for any questions about saved data (profile, applications, search history). "
    "Be concise."
)


def _parse_search_result(tool_content: str) -> dict[str, Any]:
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


def run_agent(message: str, session_id: Optional[str] = None) -> dict[str, Any]:
    logger.info("Agent message: '%s'", message[:120])

    model_name = os.getenv("CHAT_LLM_MODEL", "llama-3.3-70b-versatile")
    provider = os.getenv("CHAT_LLM_PROVIDER", "groq")
    llm = init_chat_model(model_name, model_provider=provider, temperature=0)

    from tools import (
        search_jobs, update_profile, update_application_status,
        query_database,
    )

    agent = create_react_agent(
        llm,
        [
            search_jobs, update_profile, update_application_status,
            query_database,
        ],
        prompt=_SYSTEM_PROMPT,
    )

    input_messages: list = [("human", message)]
    try:
        from database import get_recent_chat_messages
        recent = get_recent_chat_messages(6, session_id=session_id)
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
    reply = ""
    search_result = None

    for msg in messages:
        if hasattr(msg, "content") and msg.content:
            if msg.type == "ai" and not reply:
                reply = msg.content
            elif msg.type == "tool" and msg.name == "search_jobs":
                search_result = _parse_search_result(msg.content)

    if not reply:
        reply = messages[-1].content if messages else "No response."

    logger.info("Agent reply: '%s'", reply[:120])

    out: dict[str, Any] = {"reply": reply}
    if search_result:
        out["jobs"] = search_result["jobs"]
    else:
        out["jobs"] = None
    return out
