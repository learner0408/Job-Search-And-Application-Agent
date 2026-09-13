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


def _classify_intent(state: OrchestratorState) -> dict:
    """Classify query intent.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with intent and plan.
    """
    raw = state.get("raw_query", "")
    q = raw.lower()
    if any(k in q for k in ["apply", "match", "%", "threshold"]):
        return {"intent": "search_score_prepare", "plan": None}
    if "search" in q or "jobs" in q or "position" in q:
        return {"intent": "search", "plan": None}
    if "appl" in q or "profile" in q or "history" in q:
        return {"intent": "db_read", "plan": None}
    return {"intent": "chat", "plan": None}


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
    if threshold is not None:
        scored = [s for s in scored if s["score"] >= threshold]
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
    return {"error": state.get("error") or "Done."}


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
    g.add_conditional_edges("classify", _route_after_classify, {"search": "search", "db_read": "db_read", "format": "format"})
    g.add_edge("search", "profile")
    g.add_edge("profile", "score")
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
