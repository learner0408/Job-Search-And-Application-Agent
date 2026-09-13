from .base import JobSource


class IndeedSource(JobSource):
    """Indeed via DuckDuckGo."""

    @property
    def name(self) -> str:
        return "Indeed"

    def search(self, title: str, location: str = "", employment_type: str = "", remote: bool = False, max_results: int = 10) -> list[dict]:
        """Search Indeed.

        Args:
            title: Job title.
            location: Location filter.
            employment_type: Employment type filter.
            remote: Remote filter.
            max_results: Max results.

        Returns:
            Job listings from Indeed.
        """
        import tools.job_search as js

        return js.search_indeed(title, location, employment_type, remote, max_results)
