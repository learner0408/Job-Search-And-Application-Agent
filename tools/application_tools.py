import json
import logging
from typing import Optional
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def list_applications(
    query: str = "",
    status: str = "",
) -> str:
    """List your job applications with optional filtering by title keyword or status.

    Use this when the user asks "what have I applied to" or wants to see their applications.

    Args:
        query: Optional keyword to filter by job title.
        status: Optional status filter. One of: "preparing", "applied", "rejected", "interview", "offer".

    Returns:
        JSON string with keys: "applications" (list of application summaries) and "count" (int).
    """
    from database import get_applications

    apps = get_applications(status=status if status else None)

    if query:
        q = query.lower()
        apps = [a for a in apps if q in a.get("job_title", "").lower()]

    result = []
    for a in apps:
        result.append({
            "id": a["id"],
            "job_title": a["job_title"],
            "company": a.get("company", ""),
            "location": a.get("location", ""),
            "source": a.get("source", ""),
            "status": a["status"],
            "created_at": a.get("created_at", ""),
            "applied_at": a.get("applied_at", ""),
        })

    return json.dumps({"applications": result, "count": len(result)}, indent=2)


@tool
def get_application(app_id: int) -> str:
    """View full details of a specific job application by its ID.

    Use this to retrieve the complete record of an application including cover letter and timestamps.

    Args:
        app_id: The numeric ID of the application to retrieve.

    Returns:
        JSON string with all application fields including job_title, company, location, source, status,
        cover_letter, created_at, applied_at, and more.
    """
    from database import get_application

    app = get_application(app_id)
    if not app:
        return json.dumps({"error": f"No application found with id {app_id}"}, indent=2)

    return json.dumps(app, indent=2)


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
