import os
import json
import uuid
import logging
from typing import Optional, Any

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")


def _get_conn() -> psycopg2.extensions.connection:
    """Open a psycopg2 Postgres connection.

    Returns:
        Connection with RealDictCursor factory and autocommit disabled.
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    return conn


def init_db() -> None:
    """Create all tables and indexes if they do not already exist."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                preview TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS searches (
                id SERIAL PRIMARY KEY,
                query TEXT NOT NULL,
                location TEXT,
                source_trigger TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_results (
                id SERIAL PRIMARY KEY,
                search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT,
                location TEXT,
                salary TEXT,
                link TEXT NOT NULL,
                source TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                search_id INTEGER REFERENCES searches(id),
                session_id TEXT REFERENCES sessions(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                full_name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                linkedin_url TEXT NOT NULL DEFAULT '',
                github_url TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                skills TEXT NOT NULL DEFAULT '[]',
                work_history TEXT NOT NULL DEFAULT '[]',
                education TEXT NOT NULL DEFAULT '[]',
                resume_name TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id SERIAL PRIMARY KEY,
                job_title TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                salary TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'preparing',
                cover_letter TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied_at TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_search_results_search_id ON search_results(search_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status)")
        conn.commit()
        logger.info("Database initialised")
    finally:
        conn.close()


def save_search(
    query: str,
    location: str,
    source_trigger: str,
    jobs: list[dict],
) -> int:
    """Insert a search and its job results.

    Args:
        query: The search query text.
        location: Location filter used.
        source_trigger: What triggered the search (e.g. "chat", "search-form").
        jobs: List of job dicts with title, description, location, salary, link, source.

    Returns:
        The new search id.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO searches (query, location, source_trigger) VALUES (%s, %s, %s) RETURNING id",
            (query, location, source_trigger),
        )
        search_id = cur.fetchone()["id"]
        rows = [
            (search_id, j.get("title", ""), j.get("description", ""),
             j.get("location", ""), j.get("salary", ""), j.get("link", ""), j.get("source", ""))
            for j in jobs
        ]
        cur.executemany(
            "INSERT INTO search_results (search_id, title, description, location, salary, link, source) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()
        return search_id
    finally:
        conn.close()


def get_history(page: int = 1, per_page: int = 10) -> tuple[list[dict], int]:
    """Fetch paginated search history ordered by recency.

    Args:
        page: 1-based page number.
        per_page: Number of searches per page.

    Returns:
        Tuple of (list of search dicts with result_count, total search count).
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        offset = (page - 1) * per_page
        cur.execute("SELECT COUNT(*) AS cnt FROM searches")
        total = cur.fetchone()["cnt"]
        cur.execute(
            """SELECT s.*, COUNT(sr.id) AS result_count
               FROM searches s
               LEFT JOIN search_results sr ON sr.search_id = s.id
               GROUP BY s.id
               ORDER BY s.created_at DESC
               LIMIT %s OFFSET %s""",
            (per_page, offset),
        )
        return [dict(r) for r in cur.fetchall()], total
    finally:
        conn.close()


def get_search(search_id: int) -> Optional[dict]:
    """Fetch a single search with its job results.

    Args:
        search_id: The id of the search.

    Returns:
        Search dict with a "results" key, or None if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM searches WHERE id = %s", (search_id,))
        row = cur.fetchone()
        if not row:
            return None
        search = dict(row)
        cur.execute(
            "SELECT title, description, location, salary, link, source FROM search_results WHERE search_id = %s",
            (search_id,),
        )
        search["results"] = [dict(r) for r in cur.fetchall()]
        return search
    finally:
        conn.close()


def delete_search(search_id: int) -> bool:
    """Delete a search and unlink its chat messages.

    Args:
        search_id: The id of the search to delete.

    Returns:
        True if the search was deleted, False otherwise.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE chat_messages SET search_id = NULL WHERE search_id = %s", (search_id,))
        cur.execute("DELETE FROM searches WHERE id = %s", (search_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_history() -> int:
    """Delete all chat messages, search results, searches, and sessions.

    Returns:
        Number of searches deleted.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM searches")
        deleted = cur.fetchone()["cnt"]
        cur.execute("DELETE FROM chat_messages")
        cur.execute("DELETE FROM search_results")
        cur.execute("DELETE FROM searches")
        cur.execute("DELETE FROM sessions")
        conn.commit()
        return deleted
    finally:
        conn.close()


def create_session(preview: str = "") -> str:
    """Create a new chat session.

    Args:
        preview: Optional preview text for the session.

    Returns:
        The new session id (UUID string).
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO sessions (id, preview) VALUES (%s, %s)",
            (sid, preview or ""),
        )
        conn.commit()
        return sid
    finally:
        conn.close()


def update_session(session_id: str) -> None:
    """Refresh a session's preview and updated_at from its messages.

    Args:
        session_id: The id of the session to update.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT content FROM chat_messages WHERE session_id = %s AND role = 'user' ORDER BY created_at ASC LIMIT 1",
            (session_id,),
        )
        first = cur.fetchone()
        preview = first["content"][:80] if first else ""
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM chat_messages WHERE session_id = %s",
            (session_id,),
        )
        count = cur.fetchone()["cnt"]
        cur.execute(
            "UPDATE sessions SET preview = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (preview, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_sessions(limit: int = 5) -> list[dict]:
    """List recent sessions ordered by last activity.

    Args:
        limit: Maximum number of sessions to return.

    Returns:
        List of session dicts with id, preview, created_at, and msg_count.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT s.id, s.preview, s.created_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS msg_count
               FROM sessions s
               ORDER BY s.updated_at DESC
               LIMIT %s""",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_session_messages(session_id: str) -> list[dict]:
    """Fetch all messages for a session in chronological order.

    Args:
        session_id: The id of the session.

    Returns:
        List of message dicts with role, content, and created_at.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE session_id = %s ORDER BY created_at ASC",
            (session_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    """Delete a session and all its chat messages.

    Args:
        session_id: The id of the session to delete.

    Returns:
        True on success.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
        cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def save_chat_message(role: str, content: str, search_id: Optional[int] = None, session_id: Optional[str] = None) -> int:
    """Insert a chat message.

    Args:
        role: "user" or "agent".
        content: The message text.
        search_id: Optional linked search id.
        session_id: Optional linked session id.

    Returns:
        The new message id.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_messages (role, content, search_id, session_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (role, content, search_id, session_id),
        )
        conn.commit()
        return cur.fetchone()["id"]
    finally:
        conn.close()


def get_recent_chat_messages(limit: int = 6, session_id: Optional[str] = None) -> list[dict]:
    """Fetch the most recent chat messages, optionally filtered by session.

    Args:
        limit: Maximum number of messages to return.
        session_id: If set, only fetch messages for this session.

    Returns:
        List of message dicts in chronological order.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if session_id:
            cur.execute(
                "SELECT role, content, created_at FROM chat_messages WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                (session_id, limit),
            )
        else:
            cur.execute(
                "SELECT role, content, created_at FROM chat_messages ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        rows = list(reversed(cur.fetchall()))
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_profile(data: dict) -> None:
    """Upsert the single user profile row.

    Args:
        data: Profile dict with keys matching the user_profile table columns.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO user_profile (id, full_name, email, phone, linkedin_url, github_url, website, skills, work_history, education, resume_name, updated_at)
               VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (id) DO UPDATE SET
                 full_name = EXCLUDED.full_name,
                 email = EXCLUDED.email,
                 phone = EXCLUDED.phone,
                 linkedin_url = EXCLUDED.linkedin_url,
                 github_url = EXCLUDED.github_url,
                 website = EXCLUDED.website,
                 skills = EXCLUDED.skills,
                 work_history = EXCLUDED.work_history,
                 education = EXCLUDED.education,
                 resume_name = EXCLUDED.resume_name,
                 updated_at = CURRENT_TIMESTAMP""",
            (
                data.get("full_name", ""),
                data.get("email", ""),
                data.get("phone", ""),
                data.get("linkedin_url", ""),
                data.get("github_url", ""),
                data.get("website", ""),
                json.dumps(data.get("skills", [])),
                json.dumps(data.get("work_history", [])),
                json.dumps(data.get("education", [])),
                data.get("resume_name", ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile() -> Optional[dict]:
    """Fetch the user profile.

    Returns:
        Profile dict with JSON fields parsed, or None if no profile exists.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_profile WHERE id = 1")
        row = cur.fetchone()
        if not row:
            return None
        p = dict(row)
        for field in ("skills", "work_history", "education"):
            if isinstance(p.get(field), str):
                p[field] = json.loads(p[field])
        return p
    finally:
        conn.close()


def save_application(data: dict) -> int:
    """Insert a new job application.

    Args:
        data: Application dict with job_title, company, location, salary, link,
              source, status, cover_letter.

    Returns:
        The new application id.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO applications (job_title, company, location, salary, link, source, status, cover_letter)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                data.get("job_title", ""),
                data.get("company", ""),
                data.get("location", ""),
                data.get("salary", ""),
                data.get("link", ""),
                data.get("source", ""),
                data.get("status", "preparing"),
                data.get("cover_letter", ""),
            ),
        )
        conn.commit()
        return cur.fetchone()["id"]
    finally:
        conn.close()


def get_application(app_id: int) -> Optional[dict]:
    """Fetch a single application by id.

    Args:
        app_id: The application id.

    Returns:
        Application dict, or None if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM applications WHERE id = %s", (app_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_applications(status: Optional[str] = None) -> list[dict]:
    """List applications, optionally filtered by status, newest first.

    Args:
        status: If set, only return applications with this status.

    Returns:
        List of application dicts.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM applications WHERE status = %s ORDER BY created_at DESC",
                (status,),
            )
        else:
            cur.execute(
                "SELECT * FROM applications ORDER BY created_at DESC"
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_application_status(app_id: int, status: str, applied_at: Optional[str] = None) -> bool:
    """Update an application's status, optionally recording the applied timestamp.

    Args:
        app_id: The application id.
        status: New status value.
        applied_at: ISO timestamp to store when marking as applied.

    Returns:
        True if the application was updated, False if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if applied_at:
            cur.execute(
                "UPDATE applications SET status = %s, applied_at = %s WHERE id = %s",
                (status, applied_at, app_id),
            )
        else:
            cur.execute(
                "UPDATE applications SET status = %s WHERE id = %s",
                (status, app_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


_COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "sessions": {
        "id": "UUID identifying the conversation",
        "preview": "First-message preview shown in sidebar",
        "created_at": "When the conversation started",
        "updated_at": "When the conversation was last active",
    },
    "chat_messages": {
        "id": "Auto-increment message ID",
        "role": "'user' or 'assistant'",
        "content": "Full message text",
        "search_id": "FK to searches if linked to a search",
        "session_id": "FK to sessions; NULL for legacy messages",
        "created_at": "When the message was sent",
    },
    "searches": {
        "id": "Auto-increment search ID",
        "query": "Search query text",
        "location": "Location filter used",
        "source_trigger": "Which source triggered the search",
        "created_at": "When the search was performed",
    },
    "search_results": {
        "id": "Auto-increment result ID",
        "search_id": "FK to searches",
        "title": "Job listing title",
        "description": "Short job description",
        "location": "Job location",
        "salary": "Salary text if available",
        "link": "URL to the job listing",
        "source": "Which site it came from (Indeed, LinkedIn, YC)",
    },
    "user_profile": {
        "id": "Always 1 (single-row profile)",
        "full_name": "User's full name",
        "email": "User's email address",
        "phone": "User's phone number",
        "linkedin_url": "URL to LinkedIn profile",
        "github_url": "URL to GitHub profile",
        "website": "Personal or portfolio website",
        "skills": "JSON array of skill strings",
        "work_history": "JSON array of work-history objects",
        "education": "JSON array of education objects",
        "resume_name": "Filename of uploaded resume",
        "updated_at": "Last profile update timestamp",
    },
    "applications": {
        "id": "Auto-increment application ID",
        "job_title": "Title of the job applied to",
        "company": "Company name",
        "location": "Job location",
        "salary": "Salary text",
        "link": "URL to the original job listing",
        "source": "Where the job was found",
        "status": "preparing/applied/rejected/interview/offer",
        "cover_letter": "Generated cover letter text",
        "created_at": "When the application record was created",
        "applied_at": "When it was marked applied (nullable)",
    },
}


def _get_pk_columns(conn, tname: str) -> set[str]:
    """Return the set of primary-key column names for a table.

    Args:
        conn: An open database connection.
        tname: Table name.

    Returns:
        Set of column names that form the table's primary key.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_name = %s
            AND tc.table_schema = 'public'
    """, (tname,))
    return {row["column_name"] for row in cur.fetchall()}


def get_schema_metadata() -> dict:
    """Describe all tables and columns in the database.

    Returns:
        Dict with a "tables" key listing table names and column metadata.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = []
        for row in cur.fetchall():
            tname = row["table_name"]
            pk_cols = _get_pk_columns(conn, tname)
            cur2 = conn.cursor()
            cur2.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
                ORDER BY ordinal_position
            """, (tname,))
            columns = []
            for col in cur2.fetchall():
                columns.append({
                    "name": col["column_name"],
                    "type": col["data_type"],
                    "notnull": col["is_nullable"] == "NO",
                    "pk": col["column_name"] in pk_cols,
                    "description": _COLUMN_DESCRIPTIONS.get(tname, {}).get(col["column_name"], ""),
                })
            tables.append({"name": tname, "columns": columns})
        return {"tables": tables}
    finally:
        conn.close()


def execute_sql_query(sql: str) -> list[dict]:
    """Execute a read-only SELECT query against the database.

    Args:
        sql: A SELECT query string.

    Returns:
        List of result row dicts.
    """
    stripped = sql.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(stripped)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
