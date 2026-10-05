import json
from langchain_core.tools import tool

_DUMMY_MESSAGE = "The setup for applying is currently in progress. Please come back later."


@tool
def apply_application(app_ids: list[int]) -> str:
    """Apply to prepared job applications after human confirmation.

    Takes a list of application ids (single-item lists welcome). Currently a
    placeholder: validates each id belongs to the user but writes nothing.

    Args:
        app_ids: List of application ids to apply to.

    Returns:
        JSON string with per-id results.
    """
    from database import get_application, get_current_user_id

    uid = get_current_user_id()
    if uid is None:
        return json.dumps({"results": [], "message": "No user context available."}, indent=2)
    if not app_ids:
        return json.dumps({"results": [], "message": "No application ids provided. Reply 'apply to #N' to confirm."}, indent=2)
    results = []
    for app_id in app_ids:
        try:
            aid = int(app_id)
        except (TypeError, ValueError):
            results.append({"app_id": app_id, "message": f"Invalid application id '{app_id}'."})
            continue
        app = get_application(aid, user_id=uid)
        if not app:
            results.append({"app_id": aid, "message": f"No application found with id {aid}."})
            continue
        results.append({"app_id": aid, "message": _DUMMY_MESSAGE})
    return json.dumps({"results": results}, indent=2)
