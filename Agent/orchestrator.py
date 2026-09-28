import json
import logging
import re
from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


class OrchestratorState(TypedDict):
    """Typed state for the orchestrator graph."""

    user_id: int
    session_id: str
    raw_query: str
    intent: str
    profile_snapshot: dict
    jobs: list
    scored_jobs: list
    threshold: Optional[int]
    drafts: list
    error: Optional[str]
    plan: Optional[dict]


def extract_threshold(raw_query: str) -> Optional[int]:
    """Extract threshold from query.

    Args:
        raw_query: User query text.

    Returns:
        Threshold 0-100 or None.
    """
    m = re.search(r"(?:threshold|≥|>=|match)?\s*(\d+)\s*(?:%|percent|pct)?\b", raw_query, re.I)
    if m:
        v = int(m.group(1))
        if 0 < v <= 100:
            return v
    return None


def get_missing_fields(profile: Optional[dict]) -> list[str]:
    """Get missing required fields from profile.

    Args:
        profile: Profile dict.

    Returns:
        List of missing field names.
    """
    if not profile:
        return ["phone", "email"]
    missing = []
    if not profile.get("phone"):
        missing.append("phone")
    if not profile.get("email"):
        missing.append("email")
    return missing


def _load_classify_prompt() -> str:
    """Load classify prompt template.

    Returns:
        Prompt template string.
    """
    from pathlib import Path
    p = Path(__file__).parent / "prompts" / "classify" / "v1.md"
    if p.exists():
        return p.read_text()
    return "Classify into one of search, search_score_prepare, db_read, chat, profile"


def _rule_based_intent(raw: str) -> str:
    """Fallback rule-based intent.

    Args:
        raw: Raw query.

    Returns:
        Intent string.
    """
    q = raw.lower()
    if "my profile" in q or "my resume" in q or q.strip() == "profile":
        return "profile"
    if any(k in q for k in ["apply", "match", "%", "threshold", "≥"]):
        return "search_score_prepare"
    if "search" in q or "jobs" in q or "position" in q:
        return "search"
    if "appl" in q or "history" in q or ("profile" in q and "get" not in q):
        return "db_read"
    return "chat"


def _classify_intent(state: OrchestratorState) -> dict:
    """Classify query intent via LLM with rule fallback.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with intent and plan.
    """
    raw = state.get("raw_query", "")
    try:
        import os
        from langchain.chat_models import init_chat_model

        llm = init_chat_model(os.getenv("PLANNER_LLM_MODEL", "claude-haiku-4-5"), model_provider="anthropic", temperature=0)
        prompt = _load_classify_prompt() + f"\n\nQuery: {raw}\nIntent:"
        result = llm.invoke(prompt)
        content = result.content if isinstance(result.content, str) else str(result.content)
        m = re.search(r"\b(search_score_prepare|search|db_read|chat|profile)\b", content.lower())
        if m:
            logger.info("intent=%s session=%s", m.group(1), state.get("session_id"))
            return {"intent": m.group(1), "plan": None}
    except Exception as e:
        logger.warning("LLM classify failed, falling back to rule: %s", e)
    intent = _rule_based_intent(raw)
    logger.info("intent=%s session=%s (rule)", intent, state.get("session_id"))
    return {"intent": intent, "plan": None}


def _search_node(state: OrchestratorState) -> dict:
    """Search jobs via JobSource dispatcher.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with jobs.
    """
    from tools.search.job_search import search_jobs
    import json as _json
    title = state.get("raw_query", "")
    # If plan has search_jobs step, use its args
    plan = state.get("plan")
    if plan:
        for s in plan.get("steps", []):
            if s["tool"] == "search_jobs":
                args = s.get("args", {})
                title = args.get("title", title)
                break
    raw = json.loads(search_jobs(title=title, location="", source="all", max_results=10))
    jobs = []
    for key in ("indeed", "linkedin", "yc_startups"):
        jobs.extend(raw.get(key, []))
    logger.info("search jobs=%d session=%s", len(jobs), state.get("session_id"))
    return {"jobs": jobs}


def _profile_node(state: OrchestratorState) -> dict:
    """Fetch profile snapshot.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with profile_snapshot.
    """
    from database import get_profile, get_resume_text, get_user_preferences
    profile = get_profile(user_id=state["user_id"]) or {}
    prefs = get_user_preferences(user_id=state["user_id"])
    resume_text = get_resume_text(user_id=state["user_id"])
    snapshot = {
        "skills": profile.get("skills", []),
        "work_history": profile.get("work_history", []),
        "education": profile.get("education", []),
        "resume_text": resume_text,
        "preferences": prefs,
    }
    logger.info(
        "profile completeness=%s session=%s",
        "full" if resume_text else "partial",
        state.get("session_id"),
    )
    return {"profile_snapshot": snapshot}


def _score_node(state: OrchestratorState) -> dict:
    """Hybrid scoring.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with scored_jobs.
    """
    from tools.scoring.score_jobs import _deterministic_score, _llm_experience_score
    threshold = state.get("threshold")
    if threshold is None:
        threshold = extract_threshold(state.get("raw_query", ""))
        # Also check planner plan threshold arg if present
        plan = state.get("plan")
        if plan:
            for s in plan.get("steps", []):
                if s["tool"] == "score_jobs" and "threshold" in s.get("args", {}):
                    try:
                        threshold = int(s["args"]["threshold"])
                    except Exception:
                        pass
    jobs = state.get("jobs", [])
    snapshot = state.get("profile_snapshot", {})
    scored = []
    for job in jobs:
        det, breakdown = _deterministic_score(job, snapshot)
        exp = _llm_experience_score(job, snapshot)
        total = det + exp
        breakdown["experience"] = exp
        scored.append({"job": job, "score": total, "breakdown": breakdown})
    scored.sort(key=lambda x: x["score"], reverse=True)
    n_in = len(scored)
    if threshold is not None:
        scored = [s for s in scored if s["score"] >= threshold]
    logger.info(
        "score threshold=%s in=%d out=%d session=%s",
        threshold, n_in, len(scored), state.get("session_id"),
    )
    return {"scored_jobs": scored, "threshold": threshold}


def _prepare_node(state: OrchestratorState) -> dict:
    """Draft applications for top scored jobs (preview, no cover letter).

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with drafts.
    """
    from database import save_application, get_profile
    profile = get_profile(user_id=state["user_id"])
    missing = get_missing_fields(profile)
    scored = state.get("scored_jobs", [])[:5]
    drafts = []
    for s in scored:
        job = s["job"]
        data = {
            "job_title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "salary": job.get("salary", ""),
            "link": job.get("link", ""),
            "source": job.get("source", ""),
            "status": "preparing",
            "cover_letter": "",
        }
        app_id = save_application(data, user_id=state["user_id"])
        drafts.append({"app_id": app_id, "job": job, "score": s["score"], "missing_fields": missing})
    logger.info("prepare drafts=%d session=%s", len(drafts), state.get("session_id"))
    return {"drafts": drafts}


def _db_read_node(state: OrchestratorState) -> dict:
    """Handle DB-read intents via query_database.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with error as formatted answer.
    """
    from tools.db_query_tool import query_database
    try:
        answer = query_database(state.get("raw_query", ""))
        logger.info("db_read session=%s", state.get("session_id"))
        return {"error": answer}
    except Exception as e:
        return {"error": str(e)}


def _format_node(state: OrchestratorState) -> dict:
    """Format final response.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with formatted reply (stored in error field for simplicity).
    """
    if state.get("intent") == "search_score_prepare":
        drafts = state.get("drafts", [])
        if not drafts:
            return {"error": "No jobs met the threshold. Try lowering it or broadening the search."}
        lines = [f"Found {len(drafts)} jobs meeting the threshold:"]
        for i, d in enumerate(drafts, 1):
            lines.append(f"{i}. {d['job'].get('title')} — {d['score']}% (id {d['app_id']})")
        lines.append("Say 'apply to #N' to proceed.")
        return {"error": "\n".join(lines)}
    if state.get("intent") == "search":
        jobs = state.get("jobs", [])
        return {"error": f"Found {len(jobs)} jobs."}
    if state.get("intent") == "db_read":
        return {"error": state.get("error") or "Done."}
    if state.get("intent") == "profile":
        snap = state.get("profile_snapshot") or {}
        return {"error": f"Profile: {snap.get('skills', [])} | {snap.get('full_name','')}"}
    return {"error": state.get("error") or "Done."}


def _route_after_profile(state: OrchestratorState) -> str:
    """Route after profile based on intent.

    Args:
        state: Current orchestrator state.

    Returns:
        Next node name.
    """
    if state.get("intent") == "profile":
        return "format"
    return "score"


def _route_after_classify(state: OrchestratorState) -> str:
    """Route after classify based on intent.

    Args:
        state: Current orchestrator state.

    Returns:
        Next node name.
    """
    intent = state.get("intent", "chat")
    if intent == "search_score_prepare":
        return "search"
    if intent == "search":
        return "search"
    if intent == "db_read":
        return "db_read"
    if intent == "profile":
        return "profile"
    return "format"


_checkpointer = MemorySaver()
_graph = None


def _build_graph(checkpointer=None):
    """Build the graph structure.

    Args:
        checkpointer: Checkpoint saver.

    Returns:
        StateGraph builder.
    """
    g = StateGraph(OrchestratorState)
    g.add_node("classify", _classify_intent)
    g.add_node("search", _search_node)
    g.add_node("profile", _profile_node)
    g.add_node("score", _score_node)
    g.add_node("prepare", _prepare_node)
    g.add_node("db_read", _db_read_node)
    g.add_node("format", _format_node)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _route_after_classify, {"search": "search", "db_read": "db_read", "profile": "profile", "format": "format"})
    g.add_edge("search", "profile")
    g.add_conditional_edges("profile", _route_after_profile, {"score": "score", "format": "format"})
    g.add_edge("score", "prepare")
    g.add_edge("prepare", "format")
    g.add_edge("db_read", "format")
    g.add_edge("format", END)
    return g


def build_orchestrator():
    """Build the LangGraph orchestrator with shared MemorySaver checkpoint.

    Returns:
        Compiled graph.
    """
    global _graph
    if _graph is not None:
        return _graph
    g = _build_graph(checkpointer=_checkpointer)
    _graph = g.compile(checkpointer=_checkpointer)
    return _graph
