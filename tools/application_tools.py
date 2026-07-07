import json
import logging
from typing import Optional
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def update_application_status(app_id: int, status: str) -> str:
    """Update the status of a job application.

    Use this when the user wants to mark an application as applied, rejected, interview scheduled, or offer received.

    Args:
        app_id: The numeric ID of the application to update.
        status: New status value. Must be one of: "applied", "rejected", "interview", "offer".

    Returns:
        JSON string confirming the update or an error message if the app_id is not found.
    """
    from database import update_application_status as db_update_status

    valid = {"applied", "rejected", "interview", "offer"}
    if status not in valid:
        return json.dumps(
            {"error": f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid))}"},
            indent=2,
        )

    ok = db_update_status(app_id, status)
    if not ok:
        return json.dumps({"error": f"No application found with id {app_id}"}, indent=2)

    return json.dumps({"status": "ok", "message": f"Application {app_id} updated to '{status}'."}, indent=2)
