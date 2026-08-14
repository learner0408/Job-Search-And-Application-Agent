import json
import re
import logging
from typing import Any, TypedDict, Optional, Literal
from langgraph.graph import StateGraph, END
from langchain.chat_models import init_chat_model


def _safe_text(content: Any) -> str:
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

logger = logging.getLogger(__name__)


class DBQueryState(TypedDict):
    user_query: str
    schema: str
    can_answer: Optional[bool]
    sql_query: Optional[str]
    query_result: Optional[str]
    error: Optional[str]
    retry_count: int
    final_response: str


def _get_llm():
    import os
    model_name = os.getenv("CHAT_LLM_MODEL", "claude-sonnet-5")
    provider = os.getenv("CHAT_LLM_PROVIDER", "anthropic")
    return init_chat_model(model_name, model_provider=provider)


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No JSON found in response: {text[:200]}")


def _extract_sql(text: str) -> str:
    match = re.search(r"```sql\s*\n(.*?)```", text.strip(), re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*\n(.*?)```", text.strip(), re.DOTALL)
    if match:
        return match.group(1).strip()
    lines = text.strip().splitlines()
    sql_lines = [l for l in lines if l.strip().upper().startswith("SELECT")]
    if sql_lines:
        return " ".join(l.strip() for l in sql_lines)
    return text.strip()


_ROW_SCOPING_HINT = (
    "IMPORTANT DATA ISOLATION RULE: This database is multi-tenant. "
    "The current user's id is available as current_setting('app.current_user_id')::int. "
    "Every query that reads sessions, searches, search_results, chat_messages, "
    "user_profile, or applications MUST filter rows to this user, e.g. "
    "WHERE user_id = current_setting('app.current_user_id')::int. "
    "Never query the 'users' table or the 'resumes' table directly. "
)


def classify_query(state: DBQueryState) -> dict:
    llm = _get_llm()
    prompt = (
        f"You have access to a PostgreSQL database with these tables:\n\n"
        f"{state['schema']}\n\n"
        f"User question: {state['user_query']}\n\n"
        f"Can this question be answered by querying this database?\n"
        f'Return ONLY valid JSON: {{"can_answer": true/false, "reason": "..."}}'
    )
    try:
        result = llm.invoke(prompt)
        parsed = _extract_json(_safe_text(result.content))
        return {"can_answer": parsed.get("can_answer", False)}
    except Exception as e:
        logger.warning("classify_query failed: %s", e)
        return {"can_answer": False}


def generate_sql(state: DBQueryState) -> dict:
    llm = _get_llm()
    prev_sql = state.get("sql_query")
    prev_error = state.get("error")

    if prev_error and state.get("retry_count", 0) > 0:
        prompt = (
            f"{_ROW_SCOPING_HINT}"
            f"You have access to a PostgreSQL database with these tables:\n\n"
            f"{state['schema']}\n\n"
            f"User question: {state['user_query']}\n\n"
            f"Previous SQL attempt:\n{prev_sql}\n\n"
            f"Error:\n{prev_error}\n\n"
            f"Fix the SQL query. Return ONLY the SQL inside ```sql ... ```."
        )
    else:
        prompt = (
            f"{_ROW_SCOPING_HINT}"
            f"You have access to a PostgreSQL database with these tables:\n\n"
            f"{state['schema']}\n\n"
            f"User question: {state['user_query']}\n\n"
            f"Write a PostgreSQL SELECT query to answer this question.\n"
            f"Return ONLY the SQL inside ```sql ... ```. "
            f"Use ILIKE for case-insensitive partial text matches. "
            f"Use DATE_TRUNC('day', column) or column::date for date comparisons, "
            f"and CURRENT_DATE for the current date."
        )

    try:
        result = llm.invoke(prompt)
        sql = _extract_sql(_safe_text(result.content))
        return {"sql_query": sql}
    except Exception as e:
        logger.warning("generate_sql failed: %s", e)
        return {"sql_query": "", "error": f"SQL generation failed: {e}"}


def execute_sql(state: DBQueryState) -> dict:
    from database import execute_sql_query

    sql = state.get("sql_query", "")
    if not sql:
        return {"error": "No SQL query to execute", "retry_count": state.get("retry_count", 0) + 1}

    try:
        rows = execute_sql_query(sql)
        if not rows:
            return {"query_result": "No results found.", "error": None}
        return {"query_result": json.dumps(rows, indent=2, default=str), "error": None}
    except Exception as e:
        logger.warning("execute_sql failed: %s", e)
        return {"error": str(e), "retry_count": state.get("retry_count", 0) + 1}


def format_result(state: DBQueryState) -> dict:
    from database import get_schema_metadata

    if state.get("can_answer") is False:
        resp = (
            "I couldn't find information related to that in the database. "
            f"The database contains these tables: {', '.join(t['name'] for t in get_schema_metadata()['tables'])}. "
            "Try asking about your profile, applications, search history, or chat messages."
        )
        return {"final_response": resp}

    if state.get("error") and state.get("retry_count", 0) >= 2:
        resp = (
            f"I tried to query the database but ran into an issue after 2 attempts: "
            f"{state['error']}"
        )
        return {"final_response": resp}

    if state.get("error"):
        resp = f"I encountered an error: {state['error']}"
        return {"final_response": resp}

    query_result = state.get("query_result", "")
    resp = (
        f"Here is what I found:\n\n{query_result}\n\n"
        f"(SQL: `{state.get('sql_query', '')}`)"
    )
    return {"final_response": resp}


def route_after_classify(state: DBQueryState) -> Literal["generate_sql", "format_result"]:
    if state.get("can_answer"):
        return "generate_sql"
    return "format_result"


def route_after_execute(state: DBQueryState) -> Literal["generate_sql", "format_result"]:
    if state.get("error") and state.get("retry_count", 0) < 2:
        return "generate_sql"
    return "format_result"


def build_db_query_agent():
    workflow = StateGraph(DBQueryState)

    workflow.add_node("classify_query", classify_query)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("format_result", format_result)

    workflow.set_entry_point("classify_query")

    workflow.add_conditional_edges("classify_query", route_after_classify)
    workflow.add_edge("generate_sql", "execute_sql")
    workflow.add_conditional_edges("execute_sql", route_after_execute)
    workflow.add_edge("format_result", END)

    return workflow.compile()


_db_agent = None


def run_db_query(user_query: str) -> str:
    global _db_agent
    from database import get_schema_metadata

    if _db_agent is None:
        _db_agent = build_db_query_agent()

    schema = json.dumps(get_schema_metadata(), indent=2)

    initial = DBQueryState(
        user_query=user_query,
        schema=schema,
        can_answer=None,
        sql_query=None,
        query_result=None,
        error=None,
        retry_count=0,
        final_response="",
    )

    result = _db_agent.invoke(initial)
    return result.get("final_response") or "No response from database query agent."
