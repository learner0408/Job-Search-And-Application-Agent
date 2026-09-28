import json
import logging
import re
import time
import random
from typing import Optional
from langchain_core.tools import tool
from ddgs import DDGS

logger = logging.getLogger(__name__)


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


def _extract_company(title: str, snippet: str = "") -> str:
    """Extract company from title/snippet.

    Args:
        title: Job title string.
        snippet: Snippet text.

    Returns:
        Company name or empty string.
    """
    text = f"{title} {snippet}"
    for pat in [r"\bat\s+([A-Z][A-Za-z0-9 &.'-]+)", r"\b([A-Z][A-Za-z0-9 &.'-]+)\s+is hiring", r"-\s*([A-Z][A-Za-z0-9 &.'-]+)\s*$", r"\|\s*([A-Z][A-Za-z0-9 &.'-]+)\s*$"]:
        m = re.search(pat, text)
        if m:
            # take first non-None group
            for g in m.groups():
                if g:
                    return g.strip()[:80]
    return ""


def _parse_jobs(results: list[dict], source: str) -> list[dict]:
    jobs = []
    for r in results:
        title = r.get("title", "")
        snippet = r.get("body", "")
        jobs.append({
            "source": source,
            "title": title,
            "company": _extract_company(title, snippet),
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
        List of job result dicts with keys: source, title, company, description, location, salary, link.
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
        List of job result dicts with keys: source, title, company, description, location, salary, link.
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
        List of job result dicts with keys: source, title, company, description, location, salary, link.
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


# Registry for J2 dispatch — lazy to avoid circular imports
SOURCES = None


def _get_sources():
    """Lazily load JobSource registry.

    Returns:
        Dict of source name to JobSource instance.
    """
    global SOURCES
    if SOURCES is not None:
        return SOURCES
    try:
        from tools.search.sources.indeed import IndeedSource
        from tools.search.sources.linkedin import LinkedInSource
        from tools.search.sources.yc import YCSource

        SOURCES = {
            "indeed": IndeedSource(),
            "linkedin": LinkedInSource(),
            "yc": YCSource(),
        }
    except Exception:
        SOURCES = {}
    return SOURCES


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
    keyed by source with job listings containing title, company, description, location, salary, and link.

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
    cache_key = _cache_key(title, location or "", source or "all", employment_type or "", bool(remote), max_results or 10)
    try:
        from database import get_search_cache
        cached = get_search_cache(cache_key)
        if cached is not None:
            logger.info("search cache=hit source=%s title=%s", source, title[:60])
            return json.dumps(cached, indent=2)
        logger.info("search cache=miss source=%s title=%s", source, title[:60])
    except Exception:
        cached = None

    def _has_results(res: dict) -> bool:
        return any(v for v in res.values() if v)

    if source == "all":
        sources = _get_sources()
        for attempt in range(2):
            if sources:
                indeed_jobs = sources["indeed"].search(title, location, employment_type, remote, max_results) if "indeed" in sources else search_indeed(title, location, employment_type, remote, max_results)
                linkedin_jobs = sources["linkedin"].search(title, location, employment_type, remote, max_results) if "linkedin" in sources else search_linkedin(title, location, employment_type, remote, max_results)
                yc_jobs = sources["yc"].search(title, location, employment_type, remote, max_results) if "yc" in sources else search_yc(title, location, employment_type, remote, max_results)
            else:
                indeed_jobs = search_indeed(title, location, employment_type, remote, max_results)
                linkedin_jobs = search_linkedin(title, location, employment_type, remote, max_results)
                yc_jobs = search_yc(title, location, employment_type, remote, max_results)
            if indeed_jobs or linkedin_jobs or yc_jobs:
                break
            if attempt == 0:
                time.sleep(random.uniform(0.5, 1.5))
        result = {
            "indeed": indeed_jobs,
            "linkedin": linkedin_jobs,
            "yc_startups": yc_jobs,
        }
        if _has_results(result):
            try:
                from database import set_search_cache
                set_search_cache(cache_key, result, ttl_hours=24)
            except Exception:
                pass
        return json.dumps(result, indent=2)

    # Single source via registry
    key_map = {"indeed": "indeed", "linkedin": "linkedin", "yc": "yc"}
    sources = _get_sources()
    if source.lower() in sources:
        src = sources[source.lower()]
        for attempt in range(2):
            jobs = src.search(title, location, employment_type, remote, max_results)
            if jobs:
                break
            if attempt == 0:
                time.sleep(random.uniform(0.5, 1.5))
        result = {key_map[source.lower()]: jobs}
        if jobs:
            try:
                from database import set_search_cache
                set_search_cache(cache_key, result, ttl_hours=24)
            except Exception:
                pass
        return json.dumps(result, indent=2)

    # Fallback to legacy map
    source_map = {
        "indeed": ("indeed", search_indeed),
        "linkedin": ("linkedin", search_linkedin),
        "yc": ("yc", search_yc),
    }
    key, func = source_map.get(source.lower(), (None, None))
    if func is None:
        return json.dumps({"error": f"Unknown source '{source}'. Use 'all', 'indeed', 'linkedin', or 'yc'."})
    for attempt in range(2):
        jobs = func(title, location, employment_type, remote, max_results)
        if jobs:
            break
        if attempt == 0:
            time.sleep(random.uniform(0.5, 1.5))
    result = {key: jobs}
    if jobs:
        try:
            from database import set_search_cache
            set_search_cache(cache_key, result, ttl_hours=24)
        except Exception:
            pass
    return json.dumps(result, indent=2)
