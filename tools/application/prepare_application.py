import json
from typing import Optional
from langchain_core.tools import tool


@tool
def prepare_application(title: str, company: Optional[str] = "", location: Optional[str] = "", link: Optional[str] = "", description: Optional[str] = "", source: Optional[str] = "") -> str:
    """Draft a job application preview without auto-submit or cover letter.

    Persists status=preparing and asks user if required fields are missing.

    Args:
        title: Job title.
        company: Company name.
        location: Job location.
        link: Job listing URL.
        description: Job description.
        source: Source where job was found.

    Returns:
        JSON with app_id, status, and missing_fields.
    """
    from database import get_profile, save_application
    from Agent.orchestrator import get_missing_fields
    profile = get_profile()
    missing = get_missing_fields(profile)

    data = {
        "job_title": title,
        "company": company or "",
        "location": location or "",
        "salary": "",
        "link": link or "",
        "source": source or "",
        "status": "preparing",
        "cover_letter": "",
    }
    app_id = save_application(data)
    return json.dumps({"app_id": app_id, "status": "preparing", "missing_fields": missing}, indent=2)
