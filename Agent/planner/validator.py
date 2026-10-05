import json
import re

ALLOWED_TOOLS = {"search_jobs", "get_profile", "score_jobs", "prepare_application", "apply_application", "query_database", "get_database_schema"}


REQUIRED_ARGS = {
    "search_jobs": set(),
    "get_profile": set(),
    "score_jobs": set(),
    "prepare_application": set(),
    "apply_application": {"app_ids"},
    "query_database": set(),
    "get_database_schema": set(),
}


def validate_plan(plan: dict, raw_query: str = "") -> tuple[bool, str]:
    """Validate a planner DAG.

    Args:
        plan: Dict with steps [{id, tool, args, depends_on}].
        raw_query: Original user query for threshold hint.

    Returns:
        Tuple (ok, error message).
    """
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "plan must have non-empty steps"
    ids = set()
    for step in steps:
        tid = step.get("id")
        tool = step.get("tool")
        if not tid or not tool:
            return False, "each step needs id and tool"
        if tid in ids:
            return False, f"duplicate id {tid}"
        ids.add(tid)
        if tool not in ALLOWED_TOOLS:
            return False, f"tool {tool} not allowed"
        deps = step.get("depends_on", [])
        for d in deps:
            if d not in ids:
                return False, f"depends_on {d} not yet defined"
        # Arg schema check
        args = step.get("args", {}) or {}
        req = REQUIRED_ARGS.get(tool, set())
        for r in req:
            if r not in args:
                return False, f"tool {tool} missing required arg {r}"
            if r == "threshold" and "threshold" in args and not isinstance(args["threshold"], int):
                return False, "threshold must be int"
        for k in args:
            if k not in req and k not in {"title", "location", "source", "employment_type", "remote", "max_results", "user_id", "jobs", "profile_snapshot", "threshold", "company", "link", "description", "natural_language_query"}:
                # allow known optional keys, but flag truly unknown
                pass
        # score_jobs $ref check
        if tool == "score_jobs":
            for ref_key in ("jobs", "profile_snapshot"):
                val = args.get(ref_key, "")
                if isinstance(val, str) and val.startswith("$") and val[1:] not in ids:
                    return False, f"{ref_key} $ref {val} not yet defined"
    # Threshold hint check — prefer raw_query when provided, else fallback to plan for backward compat
    hint_source = raw_query.lower() if raw_query else json.dumps(plan).lower()
    if any(k in hint_source for k in ["match", "threshold", "%", "≥", "percent", "pct"]):
        has_score = any(s.get("tool") == "score_jobs" for s in steps)
        if not has_score:
            return False, "query mentions match/threshold but plan lacks score_jobs"
    return True, ""
