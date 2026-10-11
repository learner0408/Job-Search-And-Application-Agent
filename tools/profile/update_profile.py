import json
import logging
from typing import Optional
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def update_profile(
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    github_url: Optional[str] = None,
    website: Optional[str] = None,
    skills: Optional[list] = None,
    work_history: Optional[list] = None,
    education: Optional[list] = None,
) -> str:
    """Update your saved profile. Scalars are replaced; skills are merged (union);
    work_history and education entries are appended, never removed.

    Call this when the user wants to save or update their personal information. Use get_profile first
    to see current values.

    Args:
        full_name: Full name (e.g. "John Doe"). Replaces the current value.
        email: Email address (e.g. "john@example.com"). Replaces the current value.
        phone: Phone number (e.g. "+1 555-0123"). Replaces the current value.
        linkedin_url: Full URL to LinkedIn profile. Replaces the current value.
        github_url: Full URL to GitHub profile. Replaces the current value.
        website: Personal or portfolio website URL. Replaces the current value.
        skills: List of skill strings (e.g. ["Python", "React", "Machine Learning"]). Merged into existing skills.
        work_history: List of work-history objects. Appended to existing entries.
        education: List of education objects. Appended to existing entries.

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
        merged = list(current.get("skills", []) or [])
        seen = {str(s).lower() for s in merged}
        for s in skills:
            if isinstance(s, str) and s.strip() and s.strip().lower() not in seen:
                merged.append(s.strip())
                seen.add(s.strip().lower())
        current["skills"] = merged
    if work_history is not None:
        current["work_history"] = list(current.get("work_history", []) or []) + [e for e in work_history if isinstance(e, dict)]
    if education is not None:
        current["education"] = list(current.get("education", []) or []) + [e for e in education if isinstance(e, dict)]

    save_profile(current)
    return json.dumps({"status": "ok", "message": "Profile updated successfully."}, indent=2)
