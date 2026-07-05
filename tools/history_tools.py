import json
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def list_search_history(
    query: str = "",
    page: int = 1,
    per_page: int = 10,
) -> str:
    """List past job searches with optional keyword filter and pagination.

    Use this when the user asks about past searches or wants to see search history.

    Args:
        query: Optional keyword to filter searches by. Only searches whose query contains this string are returned.
        page: Page number for paginated results (starting from 1). Defaults to 1.
        per_page: Number of searches per page (max 50). Defaults to 10.

    Returns:
        JSON string with keys: "searches" (list of search summaries), "total" (int), "page" (int).
    """
    from database import get_history

    per_page = min(per_page, 50)
    searches, total = get_history(page, per_page)

    if query:
        q = query.lower()
        searches = [s for s in searches if q in s["query"].lower()]

    result = []
    for s in searches:
        result.append({
            "id": s["id"],
            "query": s["query"],
            "location": s.get("location", ""),
            "source_trigger": s["source_trigger"],
            "result_count": s["result_count"],
            "created_at": s["created_at"],
        })

    return json.dumps({"searches": result, "total": total, "page": page}, indent=2)


@tool
def get_search_results(search_id: int) -> str:
    """Get full job results for a specific past search by its ID.

    Use this to retrieve the complete results of a search previously listed via list_search_history.

    Args:
        search_id: The numeric ID of the search to retrieve results for.

    Returns:
        JSON string with keys: "query", "location", "created_at", and "results" (list of job dicts).
    """
    from database import get_search

    search = get_search(search_id)
    if not search:
        return json.dumps({"error": f"No search found with id {search_id}"})

    return json.dumps({
        "query": search["query"],
        "location": search.get("location", ""),
        "created_at": search["created_at"],
        "results": search["results"],
    }, indent=2)
