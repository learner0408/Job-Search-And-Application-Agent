import json
from langchain_core.tools import tool


def build_snapshot(profile: dict | None, resume_text: str, prefs: dict) -> dict:
    """Assemble a profile snapshot from its parts (single source of truth).

    Args:
        profile: Profile row dict or None.
        resume_text: Extracted resume text (may be empty).
        prefs: Preferences dict.

    Returns:
        Snapshot dict with skills, work_history, education, resume_text,
        preferences, full_name, email.
    """
    profile = profile or {}
    return {
        "skills": profile.get("skills", []),
        "work_history": profile.get("work_history", []),
        "education": profile.get("education", []),
        "resume_text": resume_text,
        "preferences": prefs,
        "full_name": profile.get("full_name", ""),
        "email": profile.get("email", ""),
    }


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

    snapshot = build_snapshot(profile, resume_text, prefs)
    completeness = "full" if resume_text else "partial"
    return json.dumps({"profile_snapshot": snapshot, "completeness": completeness}, indent=2, default=str)
