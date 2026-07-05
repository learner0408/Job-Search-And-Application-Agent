import json
import logging
from typing import Optional
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def get_my_profile() -> str:
    """View your saved profile information.

    Returns the user's full profile including name, contact info, URLs, skills, work history, and education.
    Use this before update_profile to see current values.

    Returns:
        JSON string with profile fields: full_name, email, phone, linkedin_url, github_url, website, skills,
        work_history, education, resume_name.
    """
    from database import get_profile

    profile = get_profile()
    if not profile:
        return json.dumps({"error": "No profile found. Use update_profile to set one up."}, indent=2)

    for field in ("skills", "work_history", "education"):
        if isinstance(profile.get(field), str):
            try:
                profile[field] = json.loads(profile[field])
            except (json.JSONDecodeError, TypeError):
                profile[field] = []

    return json.dumps(profile, indent=2)


@tool
def update_profile(
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    github_url: Optional[str] = None,
    website: Optional[str] = None,
    skills: Optional[list] = None,
) -> str:
    """Update your saved profile. Only provided fields are changed; omitted fields keep their current values.

    Call this when the user wants to save or update their personal information. Use get_my_profile first
    to see current values.

    Args:
        full_name: Full name (e.g. "John Doe").
        email: Email address (e.g. "john@example.com").
        phone: Phone number (e.g. "+1 555-0123").
        linkedin_url: Full URL to LinkedIn profile.
        github_url: Full URL to GitHub profile.
        website: Personal or portfolio website URL.
        skills: List of skill strings (e.g. ["Python", "React", "Machine Learning"]).

    Returns:
        JSON string with status confirmation.
    """
    from database import get_profile, save_profile

    current = get_profile() or {}

    if full_name is not None:
        current["full_name"] = full_name
    if email is not None:
        current["email"] = email
    if phone is not None:
        current["phone"] = phone
    if linkedin_url is not None:
        current["linkedin_url"] = linkedin_url
    if github_url is not None:
        current["github_url"] = github_url
    if website is not None:
        current["website"] = website
    if skills is not None:
        current["skills"] = [s.strip() for s in skills if isinstance(s, str)]

    save_profile(current)
    return json.dumps({"status": "ok", "message": "Profile updated successfully."}, indent=2)
