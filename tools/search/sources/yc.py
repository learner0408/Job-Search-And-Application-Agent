from .base import JobSource


class YCSource(JobSource):
    """YC Work at a Startup via DuckDuckGo."""

    @property
    def name(self) -> str:
        return "YC Startup"

    def search(self, title: str, location: str = "", employment_type: str = "", remote: bool = False, max_results: int = 10) -> list[dict]:
        """Search YC startups.

        Args:
            title: Job title.
            location: Location filter.
            employment_type: Employment type filter.
            remote: Remote filter.
            max_results: Max results.

        Returns:
            Job listings from YC.
        """
        import tools.search.job_search as js

        return js.search_yc(title, location, employment_type, remote, max_results)
