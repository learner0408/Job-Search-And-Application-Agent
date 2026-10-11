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
    raw = f"v2|{title}|{location}|{source}|{employment_type}|{remote}|{max_results}"
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
        from tools.search.sources.adzuna import AdzunaSource

        SOURCES = {
            "adzuna": AdzunaSource(),
            "indeed": IndeedSource(),
            "linkedin": LinkedInSource(),
            "yc": YCSource(),
        }
    except Exception:
        SOURCES = {}
    return SOURCES


PRIMARY_SOURCES = ("adzuna", "yc")
FALLBACK_SOURCES = ("linkedin", "indeed")


def _legacy_source_fn(key: str):
    """Return the legacy DDG search function for a source key.

    Args:
        key: Source key (indeed, linkedin, yc).

    Returns:
        Search function or None.
    """
    return {"indeed": search_indeed, "linkedin": search_linkedin, "yc": search_yc}.get(key)


@tool
def search_jobs(
    title: str,
    location: Optional[str] = "",
    source: Optional[str] = "all",
    employment_type: Optional[str] = "",
    remote: Optional[bool] = False,
    max_results: Optional[int] = 10,
) -> str:
    """Search for jobs across Adzuna, Y Combinator startups, Indeed, and LinkedIn.

    Call this when the user asks to find or search for jobs. Returns a JSON string
    keyed by source with job listings containing title, company, description, location, salary, and link.

    Primaries (adzuna, yc) are always queried; fallbacks (linkedin, indeed) only
    run when primaries return fewer than max_results in total.

    Args:
        title: Job title or keywords to search for (e.g. "AI Engineer", "Software Developer").
        location: City or region to search in (e.g. "Bangalore", "San Francisco", "Remote").
        source: Which source to search. "all" (default) runs the primary-first cascade,
            or choose "adzuna", "indeed", "linkedin", "yc".
        employment_type: Filter by employment type (e.g. "fulltime", "contract", "internship", "parttime").
        remote: If True, add "remote" to the search query to find remote jobs.
        max_results: Maximum number of results per source (default 10, max 20).

    Returns:
        JSON string with job results grouped by source. Top-level keys are "adzuna",
        "indeed", "linkedin", "yc_startups".
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

    def _run(key: str) -> list:
        sources = _get_sources()
        if key in sources:
            for attempt in range(2):
                jobs = sources[key].search(title, location, employment_type, remote, max_results)
                if jobs:
                    return jobs
                if attempt == 0:
                    time.sleep(random.uniform(0.5, 1.5))
            return jobs
        func = _legacy_source_fn(key)
        if func is None:
            return []
        for attempt in range(2):
            jobs = func(title, location, employment_type, remote, max_results)
            if jobs:
                return jobs
            if attempt == 0:
                time.sleep(random.uniform(0.5, 1.5))
        return jobs

    if source == "all":
        adzuna_jobs = _run("adzuna")
        yc_jobs = _run("yc")
        primary_total = len(adzuna_jobs) + len(yc_jobs)
        if primary_total < (max_results or 10):
            linkedin_jobs = _run("linkedin")
            indeed_jobs = _run("indeed")
        else:
            logger.info("search primaries satisfied=%d, skipping fallbacks", primary_total)
            linkedin_jobs, indeed_jobs = [], []
        result = {
            "adzuna": adzuna_jobs,
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
    key_map = {"adzuna": "adzuna", "indeed": "indeed", "linkedin": "linkedin", "yc": "yc"}
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
        return json.dumps({"error": f"Unknown source '{source}'. Use 'all', 'adzuna', 'indeed', 'linkedin', or 'yc'."})
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
