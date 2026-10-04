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
    if any(k in q for k in ["update my", "change my", "set my", "save my", "mark application", "mark as"]) or ("update" in q and "profile" in q):
        return "update"
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
        m = re.search(r"\b(search_score_prepare|search|db_read|chat|profile|update)\b", content.lower())
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
    title = state.get("raw_query", "")
    # If plan has search_jobs step, use its args
    plan = state.get("plan") or {}
    plan_args = {}
    for s in plan.get("steps", []):
        if s["tool"] == "search_jobs":
            plan_args = s.get("args", {}) or {}
            break
    title = plan_args.get("title", title)
    location = plan_args.get("location", "")
    employment_type = plan_args.get("employment_type", "")
    remote = bool(plan_args.get("remote", False))
    try:
        max_results = int(plan_args.get("max_results", 10))
    except (TypeError, ValueError):
        max_results = 10
    raw = json.loads(search_jobs(title=title, location=location, source="all", employment_type=employment_type, remote=remote, max_results=max_results))
    jobs = []
    for key in ("adzuna", "indeed", "linkedin", "yc_startups"):
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
    from database import get_profile, get_resume, get_resume_text, get_user_preferences
    from tools.profile.get_profile import build_snapshot
    profile = get_profile(user_id=state["user_id"]) or {}
    prefs = get_user_preferences(user_id=state["user_id"])
    resume = get_resume(user_id=state["user_id"])
    resume_bytes = len(resume["content"]) if resume else 0
    resume_updated = str(resume.get("updated_at")) if resume and resume.get("updated_at") else "none"
    resume_text = get_resume_text(user_id=state["user_id"])
    snapshot = build_snapshot(profile, resume_text, prefs)
    snapshot["resume_updated"] = resume_updated
    logger.info(
        "profile completeness=%s resume_bytes=%d resume_text_len=%d session=%s",
        "full" if resume_text else "partial",
        resume_bytes,
        len(resume_text),
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
    from tools.db.query_database import query_database
    try:
        answer = query_database(state.get("raw_query", ""))
        logger.info("db_read session=%s", state.get("session_id"))
        return {"error": answer}
    except Exception as e:
        return {"error": str(e)}


def _load_update_prompt() -> str:
    """Load update extraction prompt template.

    Returns:
        Prompt template string.
    """
    from pathlib import Path
    p = Path(__file__).parent / "prompts" / "update" / "v1.md"
    if p.exists():
        return p.read_text()
    return 'Return ONLY JSON {"action": "update_profile"|"update_application_status", "args": {}}'


_UPDATE_PROFILE_FIELDS = {"full_name", "email", "phone", "linkedin_url", "github_url", "website", "skills"}
_UPDATE_STATUSES = {"applied", "rejected", "interview", "offer"}


def _update_node(state: OrchestratorState) -> dict:
    """Apply profile/application updates via LLM arg extraction.

    Args:
        state: Current orchestrator state.

    Returns:
        Dict with confirmation or clarification in error field.
    """
    import os
    from langchain.chat_models import init_chat_model

    raw = state.get("raw_query", "")
    uid = state.get("user_id")
    try:
        llm = init_chat_model(os.getenv("PLANNER_LLM_MODEL", "claude-haiku-4-5"), model_provider="anthropic", temperature=0)
        result = llm.invoke(_load_update_prompt() + f"\n\nQuery: {raw}\nJSON:")
        content = result.content if isinstance(result.content, str) else str(result.content)
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {"error": "I couldn't tell what to update. Tell me the field and value, e.g. 'update my phone to X'."}
        extracted = json.loads(m.group())
    except Exception as e:
        logger.warning("update extraction failed: %s", e)
        return {"error": "I couldn't process that update. Tell me the field and value, e.g. 'update my phone to X'."}

    action = extracted.get("action")
    args = extracted.get("args", {}) or {}
    if action == "update_profile":
        fields = {k: v for k, v in args.items() if k in _UPDATE_PROFILE_FIELDS and v is not None}
        if not fields:
            return {"error": "I couldn't tell which profile field to update. Tell me the field and value."}
        if "skills" in fields and isinstance(fields["skills"], list):
            fields["skills"] = [s.strip() for s in fields["skills"] if isinstance(s, str)]
        from database import get_profile, save_profile
        profile = get_profile(user_id=uid) or {}
        profile.update(fields)
        save_profile(profile, user_id=uid)
        logger.info("update action=update_profile fields=%s session=%s", sorted(fields), state.get("session_id"))
        return {"error": f"Profile updated: {', '.join(sorted(fields))}."}
    if action == "update_application_status":
        try:
            app_id = int(args.get("app_id"))
        except (TypeError, ValueError):
            return {"error": "Which application id should I update? e.g. 'mark application 5 as applied'."}
        status = str(args.get("status", "")).lower()
        if status not in _UPDATE_STATUSES:
            return {"error": f"Invalid status '{args.get('status')}'. Must be one of: {', '.join(sorted(_UPDATE_STATUSES))}."}
        from database import update_application_status as db_update_status
        ok = db_update_status(app_id, status, user_id=uid)
        logger.info("update action=update_application_status app=%s status=%s ok=%s session=%s", app_id, status, ok, state.get("session_id"))
        if not ok:
            return {"error": f"No application found with id {app_id}."}
        return {"error": f"Application {app_id} updated to '{status}'."}
    return {"error": "I couldn't tell what to update. Tell me the field and value, e.g. 'update my phone to X'."}


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
    if state.get("intent") == "update":
        return {"error": state.get("error") or "Done."}
    if state.get("intent") == "profile":
        snap = state.get("profile_snapshot") or {}
        resume_text = snap.get("resume_text", "")
        if resume_text:
            summary = _get_cached_resume_summary(
                state.get("user_id"), snap.get("resume_updated", "none"), resume_text,
                state.get("session_id"),
            )
            if summary:
                return {"error": summary}
            return {"error": "Your resume on file:\n\n" + resume_text[:1500]}
        return {"error": f"Profile: {snap.get('skills', [])} | {snap.get('full_name','')}"}
    return {"error": state.get("error") or "Done."}


def _load_summarize_prompt() -> str:
    """Load resume summarize prompt template.

    Returns:
        Prompt template string.
    """
    from pathlib import Path
    p = Path(__file__).parent / "prompts" / "summarize" / "v1.md"
    if p.exists():
        return p.read_text()
    return "Summarize this resume in 5 lines: Name | Headline | Key skills | Experience | Education."


def _get_cached_resume_summary(user_id: int, resume_updated: str, resume_text: str, session_id: str = "") -> str:
    """Get cached resume summary or generate via LLM.

    Args:
        user_id: Owning user id.
        resume_updated: Resume updated_at marker for cache rotation.
        resume_text: Full resume text.
        session_id: Session id for log correlation.

    Returns:
        Summary string, or empty string on LLM failure.
    """
    from database import get_search_cache, set_search_cache

    key = f"resume_summary:{user_id}:{resume_updated}"
    try:
        cached = get_search_cache(key)
        if cached and isinstance(cached, dict) and cached.get("summary"):
            logger.info("profile summary cache=hit session=%s", session_id)
            return cached["summary"]
    except Exception:
        pass
    try:
        import os
        from langchain.chat_models import init_chat_model

        llm = init_chat_model(os.getenv("PLANNER_LLM_MODEL", "claude-haiku-4-5"), model_provider="anthropic", temperature=0)
        result = llm.invoke(_load_summarize_prompt() + "\n\nResume:\n" + resume_text)
        summary = result.content if isinstance(result.content, str) else str(result.content)
        try:
            set_search_cache(key, {"summary": summary})
        except Exception:
            pass
        logger.info("profile summary cache=miss chars=%d session=%s", len(summary), session_id)
        return summary
    except Exception as e:
        logger.warning("profile summary failed, falling back to excerpt: %s", e)
        return ""


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
    if intent == "update":
        return "update"
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
    g.add_node("update", _update_node)
    g.add_node("format", _format_node)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _route_after_classify, {"search": "search", "db_read": "db_read", "profile": "profile", "update": "update", "format": "format"})
    g.add_edge("search", "profile")
    g.add_conditional_edges("profile", _route_after_profile, {"score": "score", "format": "format"})
    g.add_edge("score", "prepare")
    g.add_edge("prepare", "format")
    g.add_edge("db_read", "format")
    g.add_edge("update", "format")
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
