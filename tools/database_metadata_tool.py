import json
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def get_database_schema() -> str:
    """Get metadata about all database tables and their columns.

    Returns the table names, column names, data types, primary keys, nullable flags,
    and a short description for each column. Useful when you need to understand
    what data is stored and how tables relate to each other.

    Returns:
        JSON string with a list of tables, each containing a name and a list of columns.
        Each column has: name, type, notnull, pk, description.
    """
    from database import get_schema_metadata

    data = get_schema_metadata()
    return json.dumps(data, indent=2)
