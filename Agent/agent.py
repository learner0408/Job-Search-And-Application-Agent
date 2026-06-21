import os
import json
import logging
from typing import Any
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.prebuilt import create_react_agent

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful job search and career assistant. "
    "You have access to a job search tool that searches Indeed, LinkedIn, "
    "and Y Combinator startups. "
    "When the user asks you to find jobs, use the search_jobs tool with "
    "the relevant parameters (title, location, etc.). "
    "If the user asks a general career question, just answer helpfully. "
    "Be concise but informative."
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


def run_agent(message: str) -> dict[str, Any]:
    logger.info("Agent message: '%s'", message[:120])

    model_name = os.getenv("CHAT_LLM_MODEL", "llama-3.3-70b-versatile")
    provider = os.getenv("CHAT_LLM_PROVIDER", "groq")
    llm = init_chat_model(model_name, model_provider=provider, temperature=0)

    from tools import search_jobs

    agent = create_react_agent(llm, [search_jobs], prompt=_SYSTEM_PROMPT)

    input_messages: list = [("human", message)]
    try:
        from database import get_recent_chat_messages
        recent = get_recent_chat_messages(4)
        if recent:
            history: list = []
            for m in recent:
                role = "human" if m["role"] == "user" else "ai"
                history.append((role, m["content"]))
            input_messages = history + input_messages
    except Exception:
        pass

    try:
        result = agent.invoke({"messages": input_messages})
    except Exception as e:
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
