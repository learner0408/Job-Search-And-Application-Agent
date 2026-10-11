import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def query_database(natural_language_query: str) -> str:
    """Answer questions by querying the database using natural language.

    Use this when the user asks about their saved data — profile, applications,
    search history, or chat messages. Do NOT use this for job searches
    (use search_jobs instead) or for general knowledge questions.

    The database contains: sessions, chat_messages, searches, search_results,
    user_profile, and applications.

    Args:
        natural_language_query: The user's question phrased naturally, e.g.
            "show me my applications" or "what jobs did I search for yesterday".

    Returns:
        A human-readable answer with the query results, or a message explaining
        that the database doesn't have the requested information.
    """
    from agent.db_query_agent import run_db_query

    return run_db_query(natural_language_query)
