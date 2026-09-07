from abc import ABC, abstractmethod


class JobSource(ABC):
    """Abstract job source.

    Implementations query a specific job board and return normalized listings.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name (e.g. Indeed)."""

    @abstractmethod
    def search(
        self,
        title: str,
        location: str = "",
        employment_type: str = "",
        remote: bool = False,
        max_results: int = 10,
    ) -> list[dict]:
        """Search for jobs.

        Args:
            title: Job title or keywords.
            location: Location filter.
            employment_type: Employment type filter.
            remote: Whether to restrict to remote.
            max_results: Max results to return.

        Returns:
            List of dicts with title, description, location, salary, link, source.
        """
