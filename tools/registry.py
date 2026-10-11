"""Single source of truth for planner-visible tools.

Validator checks, planner prompt rendering, and the executor all derive
from TOOL_REGISTRY. Adding a tool = one entry here.
"""

TOOL_REGISTRY = {
    "search_jobs": {
        "description": "Search jobs across sources",
        "args": {
            "title": {"type": "str", "required": False},
            "location": {"type": "str", "required": False, "default": ""},
            "source": {"type": "str", "required": False, "default": "all",
                       "enum": ["all", "adzuna", "indeed", "linkedin", "yc"]},
            "employment_type": {"type": "str", "required": False, "default": ""},
            "remote": {"type": "bool", "required": False, "default": False},
            "max_results": {"type": "int", "required": False, "default": 10},
        },
        "executor": "search",
    },
    "get_profile": {
        "description": "Assemble the current user's profile snapshot",
        "args": {
            "user_id": {"type": "int", "required": False},
        },
        "executor": "profile",
    },
    "score_jobs": {
        "description": "Score jobs against the profile snapshot, filter by threshold",
        "args": {
            "jobs": {"type": "ref", "required": False},
            "profile_snapshot": {"type": "ref", "required": False},
            "threshold": {"type": "int", "required": False},
        },
        "executor": "score",
    },
    "prepare_application": {
        "description": "Draft preview applications (no cover letter, no submit)",
        "args": {
            "title": {"type": "str", "required": False},
            "company": {"type": "str", "required": False, "default": ""},
            "location": {"type": "str", "required": False, "default": ""},
            "link": {"type": "str", "required": False, "default": ""},
            "description": {"type": "str", "required": False, "default": ""},
            "source": {"type": "str", "required": False, "default": ""},
        },
        "executor": "prepare",
    },
    "apply_application": {
        "description": "Apply to prepared applications after explicit human confirmation",
        "args": {
            "app_ids": {"type": "list[int]", "required": True},
        },
        "executor": "apply",
    },
    "query_database": {
        "description": "Answer questions by querying saved data with natural language",
        "args": {
            "natural_language_query": {"type": "str", "required": False},
        },
        "executor": "db_read",
    },
}


def allowed_tools() -> set[str]:
    """Return planner-visible tool names.

    Returns:
        Set of tool names.
    """
    return set(TOOL_REGISTRY)


def required_args() -> dict[str, set[str]]:
    """Return required arg names per tool.

    Returns:
        Mapping of tool name to required arg names.
    """
    return {
        name: {arg for arg, spec in spec["args"].items() if spec.get("required")}
        for name, spec in TOOL_REGISTRY.items()
    }


def render_tools_block() -> str:
    """Render the allow-list block for the planner prompt.

    Returns:
        Markdown bullet list of tools with arg schemas.
    """
    lines = []
    for name, spec in TOOL_REGISTRY.items():
        parts = []
        for arg, aspec in spec["args"].items():
            if aspec["type"] == "ref":
                parts.append(f'"{arg}": "$<id>"')
            elif aspec["type"] == "list[int]":
                parts.append(f'"{arg}": [int]')
            elif aspec["type"] == "int":
                parts.append(f'"{arg}": int')
            elif aspec["type"] == "bool":
                parts.append(f'"{arg}": bool')
            elif "enum" in aspec:
                parts.append(f'"{arg}": "{aspec.get("default", "")}" (' + "|".join(aspec["enum"]) + ")")
            else:
                parts.append(f'"{arg}": str')
        lines.append(f"- {name}: {{{', '.join(parts)}}} ({spec['description']})")
    return "\n".join(lines)
