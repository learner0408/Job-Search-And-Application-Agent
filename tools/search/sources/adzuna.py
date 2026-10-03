import logging
import os
from .base import JobSource

logger = logging.getLogger(__name__)

API_BASE = "https://api.adzuna.com/v1/api/jobs"

_LOCATION_COUNTRY = [
    (("bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "chennai", "pune", "kolkata", "india"), "in"),
    (("san francisco", "new york", "seattle", "austin", "boston", "chicago", "united states", ", us", " usa"), "us"),
    (("london", "united kingdom", ", uk", "manchester", "berlin", "amsterdam"), "gb"),
]


def _country_for(location: str) -> str:
    """Map a location string to an Adzuna country code.

    Args:
        location: Location filter text.

    Returns:
        Adzuna country code, defaulting to ADZUNA_COUNTRY env (default "in").
    """
    loc = (location or "").lower()
    for keywords, code in _LOCATION_COUNTRY:
        if any(k in loc for k in keywords):
            return code
    return os.getenv("ADZUNA_COUNTRY", "in")


def _format_salary(item: dict) -> str:
    """Format Adzuna salary_min/max into text.

    Args:
        item: Adzuna job dict.

    Returns:
        Salary text or empty string.
    """
    lo, hi = item.get("salary_min"), item.get("salary_max")
    if lo and hi:
        return f"{lo} - {hi}"
    if lo or hi:
        return str(lo or hi)
    return ""


class AdzunaSource(JobSource):
    """Adzuna official API (app_id + app_key)."""

    @property
    def name(self) -> str:
        return "Adzuna"

    def search(self, title: str, location: str = "", employment_type: str = "", remote: bool = False, max_results: int = 10) -> list[dict]:
        """Search Adzuna.

        Args:
            title: Job title.
            location: Location filter.
            employment_type: Employment type filter.
            remote: Remote filter.
            max_results: Max results.

        Returns:
            Job listings from Adzuna, or [] when keys are missing or the call fails.
        """
        app_id = os.getenv("ADZUNA_APP_ID", "")
        app_key = os.getenv("ADZUNA_APP_KEY", "")
        if not app_id or not app_key:
            logger.warning("Adzuna keys missing, skipping")
            return []
        country = _country_for(location)
        what = title + (" remote" if remote else "")
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": max(1, min(int(max_results or 10), 50)),
            "what": what,
            "content-type": "application/json",
        }
        if location:
            params["where"] = location
        if (employment_type or "").lower() == "fulltime":
            params["full_time"] = 1
        try:
            import httpx
            resp = httpx.get(f"{API_BASE}/{country}/search/1", params=params, timeout=20)
            if resp.status_code != 200:
                logger.warning("Adzuna HTTP %s, skipping", resp.status_code)
                return []
            jobs = []
            for item in resp.json().get("results", []):
                company = item.get("company") or {}
                loc = item.get("location") or {}
                jobs.append({
                    "source": "Adzuna",
                    "title": item.get("title", ""),
                    "company": company.get("display_name", ""),
                    "description": item.get("description", ""),
                    "location": loc.get("display_name", "") or location,
                    "salary": _format_salary(item),
                    "link": item.get("redirect_url", ""),
                })
            return jobs
        except Exception as e:
            logger.warning("Adzuna search failed: %s", e)
            return []
