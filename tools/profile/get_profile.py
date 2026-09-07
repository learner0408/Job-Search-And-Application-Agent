import json
from langchain_core.tools import tool


@tool
def get_profile() -> str:
    """Get the current user's profile snapshot (structured + resume + preferences).

    Returns:
        JSON string with profile_snapshot and completeness.
    """
    from database import get_profile as db_get_profile, get_resume_text, get_user_preferences

    profile = db_get_profile()
    prefs = get_user_preferences()
    resume_text = get_resume_text()

    if not profile:
        profile = {"full_name": "", "email": "", "phone": "", "linkedin_url": "", "github_url": "", "website": "", "skills": [], "work_history": [], "education": [], "resume_name": ""}

    snapshot = {
        "skills": profile.get("skills", []),
        "work_history": profile.get("work_history", []),
        "education": profile.get("education", []),
        "resume_text": resume_text,
        "preferences": prefs,
        "full_name": profile.get("full_name", ""),
        "email": profile.get("email", ""),
    }
    completeness = "full" if resume_text else "partial"
    return json.dumps({"profile_snapshot": snapshot, "completeness": completeness}, indent=2, default=str)
