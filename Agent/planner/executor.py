"""Execute validated planner DAGs by dispatching to orchestrator nodes."""

import logging

logger = logging.getLogger(__name__)


def topo_sort(steps: list[dict]) -> list[dict]:
    """Order steps so dependencies run first (stable for independents).

    Args:
        steps: Plan steps with id and depends_on.

    Returns:
        Steps in execution order.
    """
    order: list[dict] = []
    done: set[str] = set()
    remaining = list(steps)
    while remaining:
        progressed = False
        for s in list(remaining):
            if all(d in done for d in s.get("depends_on", [])):
                order.append(s)
                done.add(s["id"])
                remaining.remove(s)
                progressed = True
        if not progressed:
            raise ValueError(f"Circular or missing dependency in plan: {[s['id'] for s in remaining]}")
    return order


def resolve_refs(args: dict, outputs: dict) -> dict:
    """Resolve $id and $id.field references against step outputs.

    Args:
        args: Step args, possibly containing $refs.
        outputs: Mapping of step id to step output value.

    Returns:
        Args with refs replaced.
    """
    resolved = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and v.startswith("$"):
            path = v[1:].split(".")
            target = outputs.get(path[0])
            for part in path[1:]:
                target = target.get(part, "") if isinstance(target, dict) else ""
            resolved[k] = target if target is not None else ""
        else:
            resolved[k] = v
    return resolved


def execute_plan(plan: dict, state: dict) -> dict:
    """Run plan steps in order, dispatching to node functions.

    Args:
        plan: Validated plan dict.
        state: Mutable orchestrator state dict (jobs, profile_snapshot,
            scored_jobs, drafts written in place).

    Returns:
        Dict with outputs per step id.

    Raises:
        ValueError: On first step failure (fail-fast) or unknown tool.
    """
    from Agent import orchestrator as orch

    dispatch = {
        "search_jobs": orch._search_node,
        "get_profile": orch._profile_node,
        "score_jobs": orch._score_node,
        "prepare_application": orch._prepare_node,
        "query_database": orch._db_read_node,
    }
    outputs: dict = {}
    for step in topo_sort(plan.get("steps", [])):
        tool = step["tool"]
        if tool == "apply_application":
            raise ValueError("Step %s (apply_application) needs human confirmation — reply 'apply to #N' instead" % step["id"])
        node = dispatch.get(tool)
        if node is None:
            raise ValueError(f"Step {step['id']} ({tool}) has no executor")
        args = resolve_refs(step.get("args", {}), outputs)
        sub = dict(state)
        sub["plan"] = {"steps": [{**step, "args": args}]}
        try:
            out = node(sub)
        except Exception as e:
            raise ValueError(f"Step {step['id']} ({tool}) failed: {e}")
        outputs[step["id"]] = out
        state.update({k: v for k, v in out.items() if k in state or k in ("jobs", "profile_snapshot", "scored_jobs", "threshold", "drafts", "error")})
        logger.info("plan step=%s tool=%s session=%s", step["id"], tool, state.get("session_id"))
    return outputs
