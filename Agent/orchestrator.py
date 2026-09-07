import json
import logging
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


def _classify_intent(state: OrchestratorState) -> dict:
    """Classify query intent.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with intent label.
    """
    q = state.get("raw_query", "").lower()
    if any(k in q for k in ["apply", "match", "%", "threshold"]):
        return {"intent": "search_score_prepare"}
    if "search" in q or "jobs" in q or "position" in q:
        return {"intent": "search"}
    if "appl" in q or "profile" in q or "history" in q:
        return {"intent": "db_read"}
    return {"intent": "chat"}


def _search_node(state: OrchestratorState) -> dict:
    """Search jobs via JobSource dispatcher.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with jobs.
    """
    from tools.job_search import search_jobs
    import json as _json
    # Simple heuristic: extract title/location from raw_query (fallback to raw_query as title)
    title = state.get("raw_query", "")
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
    # Extract threshold from query if present
    if threshold is None:
        import re
        m = re.search(r"(\d+)\s*%", state.get("raw_query", ""))
        if m:
            threshold = int(m.group(1))
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
    missing = []
    if not profile or not profile.get("phone"):
        missing.append("phone")
    scored = state.get("scored_jobs", [])[:5]
    drafts = []
    for s in scored:
        job = s["job"]
        data = {
            "job_title": job.get("title", ""),
            "company": job.get("title", "")[:50],
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
    return {"error": "Done."}


def build_orchestrator():
    """Build the LangGraph orchestrator with MemorySaver checkpoint.

    Returns:
        Compiled graph.
    """
    g = StateGraph(OrchestratorState)
    g.add_node("classify", _classify_intent)
    g.add_node("search", _search_node)
    g.add_node("profile", _profile_node)
    g.add_node("score", _score_node)
    g.add_node("prepare", _prepare_node)
    g.add_node("format", _format_node)

    g.set_entry_point("classify")
    # For now linear; planner DAG will replace edges in future
    g.add_edge("classify", "search")
    g.add_edge("search", "profile")
    g.add_edge("profile", "score")
    g.add_edge("score", "prepare")
    g.add_edge("prepare", "format")
    g.add_edge("format", END)

    return g.compile(checkpointer=MemorySaver())
