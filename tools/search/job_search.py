"""Shim for category import — real implementation lives in tools/job_search.py."""
from tools.job_search import (  # noqa: F401
    _ddgs_search,
    _extract_company,
    _extract_location,
    _extract_salary,
    _parse_jobs,
    _cache_key,
    search_indeed,
    search_linkedin,
    search_yc,
    search_jobs,
)
try:
    from tools.job_search import SOURCES  # noqa: F401
except Exception:
    pass
