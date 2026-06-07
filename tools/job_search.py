from typing import Optional
from langchain_core.tools import tool
from ddgs import DDGS


def _ddgs_search(query: str, max_results: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def search_indeed(
    title: str,
    location: str = "",
    employment_type: str = "",
    remote: bool = False,
    max_results: int = 10,
) -> list[dict]:
    """Search Indeed for jobs via DuckDuckGo."""
    parts = ["site:indeed.com", title]
    if location:
        parts.append(location)
    if remote:
        parts.append("remote")
    if employment_type:
        parts.append(employment_type)
    query = " ".join(parts)
    results = _ddgs_search(query, max_results)
    jobs = []
    for r in results:
        jobs.append({
            "source": "Indeed",
            "title": r.get("title", ""),
            "snippet": r.get("body", ""),
            "url": r.get("href", ""),
        })
    return jobs


def search_linkedin(
    title: str,
    location: str = "",
    employment_type: str = "",
    remote: bool = False,
    max_results: int = 10,
) -> list[dict]:
    """Search LinkedIn for jobs via DuckDuckGo."""
    parts = ["site:linkedin.com/jobs", title]
    if location:
        parts.append(location)
    if remote:
        parts.append("remote")
    if employment_type:
        parts.append(employment_type)
    query = " ".join(parts)
    results = _ddgs_search(query, max_results)
    jobs = []
    for r in results:
        jobs.append({
            "source": "LinkedIn",
            "title": r.get("title", ""),
            "snippet": r.get("body", ""),
            "url": r.get("href", ""),
        })
    return jobs


@tool
def search_jobs(
    title: str,
    location: Optional[str] = "",
    employment_type: Optional[str] = "",
    remote: Optional[bool] = False,
    max_results: Optional[int] = 10,
) -> str:
    """Search for jobs on Indeed and LinkedIn.

    Args:
        title: Job title or keywords to search for.
        location: City, state, or "remote" to filter by location.
        employment_type: Type of employment — "fulltime", "parttime", "contract", "internship", "temporary".
        remote: Set to True to only show remote jobs.
        max_results: Maximum number of results per source (default 10).
    """
    indeed_jobs = search_indeed(title, location, employment_type, remote, max_results)
    linkedin_jobs = search_linkedin(title, location, employment_type, remote, max_results)

    if not indeed_jobs and not linkedin_jobs:
        return f"No jobs found for '{title}' in '{location}'."

    lines = []
    if indeed_jobs:
        lines.append(f"### Indeed ({len(indeed_jobs)} results)")
        lines.append("")
        for j in indeed_jobs:
            lines.append(f"  **{j['title']}**")
            lines.append(f"  {j['url']}")
            if j["snippet"]:
                lines.append(f"  > {j['snippet'][:200]}")
            lines.append("")

    if linkedin_jobs:
        lines.append(f"### LinkedIn ({len(linkedin_jobs)} results)")
        lines.append("")
        for j in linkedin_jobs:
            lines.append(f"  **{j['title']}**")
            lines.append(f"  {j['url']}")
            if j["snippet"]:
                lines.append(f"  > {j['snippet'][:200]}")
            lines.append("")

    return "\n".join(lines).strip()
