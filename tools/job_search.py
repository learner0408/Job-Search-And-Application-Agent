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
            "description": snippet if snippet else "",
            "location": _extract_location(f"{title} {snippet}"),
            "salary": _extract_salary(snippet),
            "link": r.get("href", ""),
        })
    return jobs


def _cache_key(title: str, location: str, source: str, employment_type: str, remote: bool, max_results: int) -> str:
    """Build a cache key for a search.

    Args:
        title: Job title query.
        location: Location filter.
        source: Source filter.
        employment_type: Employment type filter.
        remote: Remote flag.
        max_results: Max results.

    Returns:
        Cache key string.
    """
    import hashlib
    raw = f"{title}|{location}|{source}|{employment_type}|{remote}|{max_results}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def search_indeed(
    title: str,
    location: str = "",
    employment_type: str = "",
    remote: bool = False,
    max_results: int = 10,
) -> list[dict]:
    """Search Indeed for jobs via DuckDuckGo.

    Args:
        title: Job title to search for (e.g. "AI Engineer", "Software Developer").
        location: City or region to search in (e.g. "Bangalore", "San Francisco").
        employment_type: Filter by employment type (e.g. "fulltime", "contract", "internship").
        remote: If True, restrict results to remote jobs.
        max_results: Maximum number of job listings to return (max 20).

    Returns:
        List of job result dicts with keys: source, title, description, location, salary, link.
    """
    parts = ["site:indeed.com", title, "jobs"]
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
    """Search LinkedIn for jobs via DuckDuckGo.

    Args:
        title: Job title to search for (e.g. "AI Engineer", "Software Developer").
        location: City or region to search in.
        employment_type: Filter by employment type (e.g. "fulltime", "contract").
        remote: If True, restrict results to remote jobs.
        max_results: Maximum number of job listings to return (max 20).

    Returns:
        List of job result dicts with keys: source, title, description, location, salary, link.
    """
    parts = ["site:linkedin.com/jobs", title]
    if location:
        parts.append(location)
    if remote:
        parts.append("remote")
    if employment_type:
        parts.append(employment_type)
    query = " ".join(parts)
    return _parse_jobs(_ddgs_search(query, max_results), "LinkedIn")


def search_yc(
    title: str,
    location: str = "",
    employment_type: str = "",
    remote: bool = False,
    max_results: int = 10,
) -> list[dict]:
    """Search Y Combinator startup jobs via DuckDuckGo.

    Args:
        title: Job title to search for (e.g. "AI Engineer", "Software Developer").
        location: City or region to search in.
        employment_type: Filter by employment type (e.g. "fulltime", "internship").
        remote: If True, restrict results to remote jobs.
        max_results: Maximum number of job listings to return (max 20).

    Returns:
        List of job result dicts with keys: source, title, description, location, salary, link.
    """
    parts = ["site:workatastartup.com/jobs", title]
    if location:
        parts.append(location)
    if employment_type:
        parts.append(employment_type)
    if remote:
        parts.append("remote")
    query = " ".join(parts)
    return _parse_jobs(_ddgs_search(query, max_results), "YC Startup")


@tool
def search_jobs(
    title: str,
    location: Optional[str] = "",
    source: Optional[str] = "all",
    employment_type: Optional[str] = "",
    remote: Optional[bool] = False,
    max_results: Optional[int] = 10,
) -> str:
    """Search for jobs across Indeed, LinkedIn, and Y Combinator startups.

    Call this when the user asks to find or search for jobs. Returns a JSON string
    keyed by source with job listings containing title, description, location, salary, and link.

    Args:
        title: Job title or keywords to search for (e.g. "AI Engineer", "Software Developer").
        location: City or region to search in (e.g. "Bangalore", "San Francisco", "Remote").
        source: Which source to search. "all" (default) searches all three, or choose "indeed", "linkedin", "yc".
        employment_type: Filter by employment type (e.g. "fulltime", "contract", "internship", "parttime").
        remote: If True, add "remote" to the search query to find remote jobs.
        max_results: Maximum number of results per source (default 10, max 20).

    Returns:
        JSON string with job results grouped by source. Top-level keys are "indeed", "linkedin", "yc_startups".
    """
    # Check cache for 24h entries
    cache_key = _cache_key(title, location or "", source or "all", employment_type or "", bool(remote), max_results or 10)
    try:
        from database import get_search_cache, set_search_cache
        cached = get_search_cache(cache_key)
        if cached is not None:
            return json.dumps(cached, indent=2)
    except Exception:
        cached = None

    source_map = {
        "indeed": ("indeed", search_indeed),
        "linkedin": ("linkedin", search_linkedin),
        "yc": ("yc", search_yc),
    }

    if source == "all":
        # Retrieve with 2x retry on empty results
        for attempt in range(2):
            indeed_jobs = search_indeed(title, location, employment_type, remote, max_results)
            linkedin_jobs = search_linkedin(title, location, employment_type, remote, max_results)
            yc_jobs = search_yc(title, location, employment_type, remote, max_results)
            if indeed_jobs or linkedin_jobs or yc_jobs:
                break
        result = {
            "indeed": indeed_jobs,
            "linkedin": linkedin_jobs,
            "yc_startups": yc_jobs,
        }
        try:
            from database import set_search_cache
            set_search_cache(cache_key, result, ttl_hours=24)
        except Exception:
            pass
        return json.dumps(result, indent=2)

    key, func = source_map.get(source.lower(), (None, None))
    if func is None:
        return json.dumps({"error": f"Unknown source '{source}'. Use 'all', 'indeed', 'linkedin', or 'yc'."})
    for attempt in range(2):
        jobs = func(title, location, employment_type, remote, max_results)
        if jobs:
            break
    result = {key: jobs}
    try:
        from database import set_search_cache
        set_search_cache(cache_key, result, ttl_hours=24)
    except Exception:
        pass
    return json.dumps(result, indent=2)
