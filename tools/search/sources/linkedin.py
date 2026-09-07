from .base import JobSource
from tools.job_search import search_linkedin as _search_fn


class LinkedInSource(JobSource):
    """LinkedIn via DuckDuckGo."""

    @property
    def name(self) -> str:
        return "LinkedIn"

    def search(self, title: str, location: str = "", employment_type: str = "", remote: bool = False, max_results: int = 10) -> list[dict]:
        """Search LinkedIn.

        Args:
            title: Job title.
            location: Location filter.
            employment_type: Employment type filter.
            remote: Remote filter.
            max_results: Max results.

        Returns:
            Job listings from LinkedIn.
        """
        return _search_fn(title, location, employment_type, remote, max_results)
