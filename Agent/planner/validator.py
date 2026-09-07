import json
import re

ALLOWED_TOOLS = {"search_jobs", "get_profile", "score_jobs", "prepare_application", "query_database", "get_database_schema"}


def validate_plan(plan: dict) -> tuple[bool, str]:
    """Validate a planner DAG.

    Args:
        plan: Dict with steps [{id, tool, args, depends_on}].

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
    # Threshold hint check
    raw = json.dumps(plan).lower()
    if any(k in raw for k in ["match", "threshold", "%", "≥"]):
        has_score = any(s.get("tool") == "score_jobs" for s in steps)
        if not has_score:
            return False, "query mentions match/threshold but plan lacks score_jobs"
    return True, ""
