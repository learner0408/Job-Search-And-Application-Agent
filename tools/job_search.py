import json
import re
from typing import Optional
from langchain_core.tools import tool
from ddgs import DDGS


def _ddgs_search(query: str, max_results: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


_LOCATION_RE = re.compile(
    r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)"
    r"|in\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)"
    r"|Location[:\s]*([^.;\n]+)"
)
_SALARY_RE = re.compile(
    r"\$\d[\d,]*\s*(?:-\s*\$?\d[\d,]*)?\s*(?:a\s+year|an?\s+(?:hour|month)|/yr|/hour|/mo)"
    r"|₹\d[\d,]*\s*(?:-\s*₹?\d[\d,]*)?"
    r"|(?:Salary|Compensation)[:\s]*([^.;\n]+)"
)


def _extract_location(text: str) -> str:
    if not text:
        return ""
    m = _LOCATION_RE.search(text)
    if m:
        return m.group(0).strip()
    return ""


def _extract_salary(text: str) -> str:
    if not text:
        return ""
    m = _SALARY_RE.search(text)
    if m:
        return m.group(0).strip()
    return ""


def _parse_jobs(results: list[dict], source: str) -> list[dict]:
    jobs = []
    for r in results:
        title = r.get("title", "")
        snippet = r.get("body", "")
        jobs.append({
            "source": source,
            "title": title,
            "description": snippet[:300],
            "location": _extract_location(f"{title} {snippet}"),
            "salary": _extract_salary(snippet),
            "link": r.get("href", ""),
        })
    return jobs


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
    return _parse_jobs(_ddgs_search(query, max_results), "Indeed")


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
    return _parse_jobs(_ddgs_search(query, max_results), "LinkedIn")


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
        return json.dumps({"indeed": [], "linkedin": []})

    return json.dumps({
        "indeed": indeed_jobs,
        "linkedin": linkedin_jobs,
    }, indent=2)
