import os
import json
import uuid
import re
import logging
from contextvars import ContextVar
from typing import Optional, Any

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)


def set_current_user(user_id: Optional[int]) -> None:
    """Set the effective user id for the current request context.

    Args:
        user_id: The authenticated user's id, or None to clear.
    """
    current_user_id.set(user_id)


def get_current_user_id() -> Optional[int]:
    """Get the user id set for the current request context.

    Returns:
        The current user id, or None if not set.
    """
    return current_user_id.get()


def _resolve_user_id(user_id: Optional[int]) -> int:
    """Resolve an explicit user id or fall back to the request context.

    Args:
        user_id: Explicit user id passed by the caller.

    Returns:
        The user id to scope queries by.

    Raises:
        ValueError: If no user id is available.
    """
    if user_id is not None:
        return user_id
    uid = current_user_id.get()
    if uid is None:
        raise ValueError("No user context available for database operation")
    return uid


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
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                provider TEXT NOT NULL DEFAULT 'email',
                provider_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider
            ON users(provider, provider_id) WHERE provider_id IS NOT NULL
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                preview TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS searches (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                query TEXT NOT NULL,
                location TEXT,
                source_trigger TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_results (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                search_id INTEGER REFERENCES searches(id),
                session_id TEXT REFERENCES sessions(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
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
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resumes (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                filename TEXT NOT NULL DEFAULT '',
                mime_type TEXT NOT NULL DEFAULT 'application/pdf',
                content BYTEA NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_search_results_search_id ON search_results(search_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_searches_user ON searches(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_search_results_user ON search_results(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id)")
        conn.commit()
        logger.info("Database initialised")
    finally:
        conn.close()


def save_search(
    query: str,
    location: str,
    source_trigger: str,
    jobs: list[dict],
    user_id: Optional[int] = None,
) -> int:
    """Insert a search and its job results for a user.

    Args:
        query: The search query text.
        location: Location filter used.
        source_trigger: What triggered the search (e.g. "chat", "search-form").
        jobs: List of job dicts with title, description, location, salary, link, source.
        user_id: Owning user id; defaults to the request context.

    Returns:
        The new search id.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO searches (user_id, query, location, source_trigger) VALUES (%s, %s, %s, %s) RETURNING id",
            (uid, query, location, source_trigger),
        )
        search_id = cur.fetchone()["id"]
        rows = [
            (uid, search_id, j.get("title", ""), j.get("description", ""),
             j.get("location", ""), j.get("salary", ""), j.get("link", ""), j.get("source", ""))
            for j in jobs
        ]
        cur.executemany(
            "INSERT INTO search_results (user_id, search_id, title, description, location, salary, link, source) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()
        return search_id
    finally:
        conn.close()


def get_history(page: int = 1, per_page: int = 10, user_id: Optional[int] = None) -> tuple[list[dict], int]:
    """Fetch paginated search history ordered by recency for a user.

    Args:
        page: 1-based page number.
        per_page: Number of searches per page.
        user_id: Owning user id; defaults to the request context.

    Returns:
        Tuple of (list of search dicts with result_count, total search count).
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        offset = (page - 1) * per_page
        cur.execute("SELECT COUNT(*) AS cnt FROM searches WHERE user_id = %s", (uid,))
        total = cur.fetchone()["cnt"]
        cur.execute(
            """SELECT s.*, COUNT(sr.id) AS result_count
               FROM searches s
               LEFT JOIN search_results sr ON sr.search_id = s.id AND sr.user_id = s.user_id
               WHERE s.user_id = %s
               GROUP BY s.id
               ORDER BY s.created_at DESC
               LIMIT %s OFFSET %s""",
            (uid, per_page, offset),
        )
        return [dict(r) for r in cur.fetchall()], total
    finally:
        conn.close()


def get_search(search_id: int, user_id: Optional[int] = None) -> Optional[dict]:
    """Fetch a single search with its job results for a user.

    Args:
        search_id: The id of the search.
        user_id: Owning user id; defaults to the request context.

    Returns:
        Search dict with a "results" key, or None if not found.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM searches WHERE id = %s AND user_id = %s", (search_id, uid))
        row = cur.fetchone()
        if not row:
            return None
        search = dict(row)
        cur.execute(
            "SELECT title, description, location, salary, link, source FROM search_results WHERE search_id = %s AND user_id = %s",
            (search_id, uid),
        )
        search["results"] = [dict(r) for r in cur.fetchall()]
        return search
    finally:
        conn.close()


def delete_search(search_id: int, user_id: Optional[int] = None) -> bool:
    """Delete a search and unlink its chat messages for a user.

    Args:
        search_id: The id of the search to delete.
        user_id: Owning user id; defaults to the request context.

    Returns:
        True if the search was deleted, False otherwise.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE chat_messages SET search_id = NULL WHERE search_id = %s AND user_id = %s", (search_id, uid))
        cur.execute("DELETE FROM searches WHERE id = %s AND user_id = %s", (search_id, uid))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_history(user_id: Optional[int] = None) -> int:
    """Delete all chat messages, search results, searches, and sessions for a user.

    Args:
        user_id: Owning user id; defaults to the request context.

    Returns:
        Number of searches deleted.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM searches WHERE user_id = %s", (uid,))
        deleted = cur.fetchone()["cnt"]
        cur.execute("DELETE FROM chat_messages WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM search_results WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM searches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (uid,))
        conn.commit()
        return deleted
    finally:
        conn.close()


def create_session(preview: str = "", user_id: Optional[int] = None) -> str:
    """Create a new chat session for a user.

    Args:
        preview: Optional preview text for the session.
        user_id: Owning user id; defaults to the request context.

    Returns:
        The new session id (UUID string).
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO sessions (id, user_id, preview) VALUES (%s, %s, %s)",
            (sid, uid, preview or ""),
        )
        conn.commit()
        return sid
    finally:
        conn.close()


def update_session(session_id: str, user_id: Optional[int] = None) -> None:
    """Refresh a session's preview and updated_at from its messages.

    Args:
        session_id: The id of the session to update.
        user_id: Owning user id; defaults to the request context.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT content FROM chat_messages WHERE session_id = %s AND user_id = %s AND role = 'user' ORDER BY created_at ASC LIMIT 1",
            (session_id, uid),
        )
        first = cur.fetchone()
        preview = first["content"][:80] if first else ""
        cur.execute(
            "UPDATE sessions SET preview = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s",
            (preview, session_id, uid),
        )
        conn.commit()
    finally:
        conn.close()


def list_sessions(limit: int = 5, user_id: Optional[int] = None) -> list[dict]:
    """List recent sessions ordered by last activity for a user.

    Args:
        limit: Maximum number of sessions to return.
        user_id: Owning user id; defaults to the request context.

    Returns:
        List of session dicts with id, preview, created_at, and msg_count.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT s.id, s.preview, s.created_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS msg_count
               FROM sessions s
               WHERE s.user_id = %s
               ORDER BY s.updated_at DESC
               LIMIT %s""",
            (uid, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_session_messages(session_id: str, user_id: Optional[int] = None) -> list[dict]:
    """Fetch all messages for a session in chronological order.

    Args:
        session_id: The id of the session.
        user_id: Owning user id; defaults to the request context.

    Returns:
        List of message dicts with role, content, and created_at.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE session_id = %s AND user_id = %s ORDER BY created_at ASC",
            (session_id, uid),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def delete_session(session_id: str, user_id: Optional[int] = None) -> bool:
    """Delete a session and all its chat messages for a user.

    Args:
        session_id: The id of the session to delete.
        user_id: Owning user id; defaults to the request context.

    Returns:
        True on success.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s AND user_id = %s", (session_id, uid))
        cur.execute("DELETE FROM sessions WHERE id = %s AND user_id = %s", (session_id, uid))
        conn.commit()
        return True
    finally:
        conn.close()


def save_chat_message(
    role: str,
    content: str,
    search_id: Optional[int] = None,
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    """Insert a chat message for a user.

    Args:
        role: "user" or "agent".
        content: The message text.
        search_id: Optional linked search id.
        session_id: Optional linked session id.
        user_id: Owning user id; defaults to the request context.

    Returns:
        The new message id.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_messages (user_id, role, content, search_id, session_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (uid, role, content, search_id, session_id),
        )
        conn.commit()
        return cur.fetchone()["id"]
    finally:
        conn.close()


def get_recent_chat_messages(limit: int = 6, session_id: Optional[str] = None, user_id: Optional[int] = None) -> list[dict]:
    """Fetch the most recent chat messages, optionally filtered by session.

    Args:
        limit: Maximum number of messages to return.
        session_id: If set, only fetch messages for this session.
        user_id: Owning user id; defaults to the request context.

    Returns:
        List of message dicts in chronological order.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if session_id:
            cur.execute(
                "SELECT role, content, created_at FROM chat_messages WHERE session_id = %s AND user_id = %s ORDER BY created_at DESC LIMIT %s",
                (session_id, uid, limit),
            )
        else:
            cur.execute(
                "SELECT role, content, created_at FROM chat_messages WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (uid, limit),
            )
        rows = list(reversed(cur.fetchall()))
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_profile(data: dict, user_id: Optional[int] = None) -> None:
    """Upsert the user's profile row.

    Args:
        data: Profile dict with keys matching the user_profile table columns.
        user_id: Owning user id; defaults to the request context.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO user_profile (user_id, full_name, email, phone, linkedin_url, github_url, website, skills, work_history, education, resume_name, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (user_id) DO UPDATE SET
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
                uid,
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


def get_profile(user_id: Optional[int] = None) -> Optional[dict]:
    """Fetch the user's profile.

    Args:
        user_id: Owning user id; defaults to the request context.

    Returns:
        Profile dict with JSON fields parsed, or None if no profile exists.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_profile WHERE user_id = %s", (uid,))
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


def save_application(data: dict, user_id: Optional[int] = None) -> int:
    """Insert a new job application for a user.

    Args:
        data: Application dict with job_title, company, location, salary, link,
              source, status, cover_letter.
        user_id: Owning user id; defaults to the request context.

    Returns:
        The new application id.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO applications (user_id, job_title, company, location, salary, link, source, status, cover_letter)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                uid,
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


def get_application(app_id: int, user_id: Optional[int] = None) -> Optional[dict]:
    """Fetch a single application by id for a user.

    Args:
        app_id: The application id.
        user_id: Owning user id; defaults to the request context.

    Returns:
        Application dict, or None if not found.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM applications WHERE id = %s AND user_id = %s", (app_id, uid))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_applications(status: Optional[str] = None, user_id: Optional[int] = None) -> list[dict]:
    """List applications for a user, optionally filtered by status, newest first.

    Args:
        status: If set, only return applications with this status.
        user_id: Owning user id; defaults to the request context.

    Returns:
        List of application dicts.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM applications WHERE user_id = %s AND status = %s ORDER BY created_at DESC",
                (uid, status),
            )
        else:
            cur.execute(
                "SELECT * FROM applications WHERE user_id = %s ORDER BY created_at DESC",
                (uid,),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_application_status(app_id: int, status: str, applied_at: Optional[str] = None, user_id: Optional[int] = None) -> bool:
    """Update an application's status for a user, optionally recording the applied timestamp.

    Args:
        app_id: The application id.
        status: New status value.
        applied_at: ISO timestamp to store when marking as applied.
        user_id: Owning user id; defaults to the request context.

    Returns:
        True if the application was updated, False if not found.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if applied_at:
            cur.execute(
                "UPDATE applications SET status = %s, applied_at = %s WHERE id = %s AND user_id = %s",
                (status, applied_at, app_id, uid),
            )
        else:
            cur.execute(
                "UPDATE applications SET status = %s WHERE id = %s AND user_id = %s",
                (status, app_id, uid),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


_COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "sessions": {
        "id": "UUID identifying the conversation",
        "user_id": "Owning user's id (FK to users)",
        "preview": "First-message preview shown in sidebar",
        "created_at": "When the conversation started",
        "updated_at": "When the conversation was last active",
    },
    "chat_messages": {
        "id": "Auto-increment message ID",
        "user_id": "Owning user's id (FK to users)",
        "role": "'user' or 'assistant'",
        "content": "Full message text",
        "search_id": "FK to searches if linked to a search",
        "session_id": "FK to sessions; NULL for legacy messages",
        "created_at": "When the message was sent",
    },
    "searches": {
        "id": "Auto-increment search ID",
        "user_id": "Owning user's id (FK to users)",
        "query": "Search query text",
        "location": "Location filter used",
        "source_trigger": "Which source triggered the search",
        "created_at": "When the search was performed",
    },
    "search_results": {
        "id": "Auto-increment result ID",
        "user_id": "Owning user's id (FK to users)",
        "search_id": "FK to searches",
        "title": "Job listing title",
        "description": "Short job description",
        "location": "Job location",
        "salary": "Salary text if available",
        "link": "URL to the job listing",
        "source": "Which site it came from (Indeed, LinkedIn, YC)",
    },
    "user_profile": {
        "user_id": "Owning user's id (PK, FK to users)",
        "id": "Legacy single-row id (always 1; deprecated)",
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
        "user_id": "Owning user's id (FK to users)",
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
    "users": {
        "id": "Auto-increment user ID",
        "email": "Canonical email address (unique login identity)",
        "password_hash": "Argon2 hash for email/password users; NULL for OAuth-only users",
        "provider": "How the account was created: email, google, or github",
        "provider_id": "Provider-specific identity (e.g. Google/GitHub sub)",
        "status": "active or pending (waitlist)",
        "is_admin": "Whether the user is an admin (owner slot)",
        "created_at": "When the account was created",
        "updated_at": "Last account update timestamp",
    },
    "resumes": {
        "user_id": "Owning user's id (PK, FK to users)",
        "filename": "Original resume filename",
        "mime_type": "MIME type of the stored resume",
        "content": "Raw resume bytes (BYTEA)",
        "updated_at": "Last upload timestamp",
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


_USER_SCOPED_TABLES = {
    "sessions", "searches", "search_results", "chat_messages",
    "user_profile", "applications",
}

_BLOCKED_TABLES = {"users", "resumes"}


def _validate_query_scoping(sql: str) -> None:
    """Verify a query respects per-user isolation rules.

    Args:
        sql: The generated SQL to validate.

    Raises:
        ValueError: If the query references a blocked table or touches a
            user-scoped table without scoping by current_setting.
    """
    lowered = sql.lower()
    for table in _BLOCKED_TABLES:
        if re.search(rf"\b{table}\b", lowered):
            raise ValueError(
                f"Queries against the '{table}' table are not allowed. "
                "Use user_profile/other scoped tables instead."
            )
    touches_scoped = any(re.search(rf"\b{t}\b", lowered) for t in _USER_SCOPED_TABLES)
    if touches_scoped and "current_setting('app.current_user_id')" not in lowered:
        raise ValueError(
            "Query must scope user data via user_id = current_setting('app.current_user_id')"
        )


def execute_sql_query(sql: str) -> list[dict]:
    """Execute a read-only SELECT query scoped to the current user.

    Enforces per-user isolation: requires a user context, rejects access to
    the users/resumes tables, requires any query touching user-scoped tables
    to reference current_setting('app.current_user_id'), and verifies every
    returned row belongs to the current user.

    Args:
        sql: A SELECT query string.

    Returns:
        List of result row dicts.

    Raises:
        ValueError: If the query is not a SELECT, is not user-scoped, or
            would expose another user's data.
    """
    stripped = sql.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")

    uid = _resolve_user_id(None)
    _validate_query_scoping(stripped)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.current_user_id', %s, true)", (str(uid),))
        cur.execute(stripped)
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            if row.get("user_id") is not None and row["user_id"] != uid:
                raise ValueError("Query result included another user's data")
        return rows
    finally:
        conn.close()


# ── Users & Resume persistence ────────────────────────────────

def create_user(
    email: str,
    password_hash: Optional[str] = None,
    provider: str = "email",
    provider_id: Optional[str] = None,
    is_admin: bool = False,
    status: str = "active",
) -> dict:
    """Create a new user account.

    Args:
        email: Canonical email address (unique).
        password_hash: Argon2 hash for email/password users, else None.
        provider: "email", "google", or "github".
        provider_id: Provider-specific identity, if any.
        is_admin: Whether the account is an admin (owner slot).
        status: "active" or "pending" (waitlist).

    Returns:
        The created user row.

    Raises:
        ValueError: If the email already exists.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO users (email, password_hash, provider, provider_id, is_admin, status)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (email, password_hash, provider, provider_id, is_admin, status),
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise ValueError("An account with this email already exists")
        user = dict(cur.fetchone())
        conn.commit()
        return user
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    """Fetch a user by their canonical email.

    Args:
        email: The user's email address.

    Returns:
        User dict, or None if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_provider(provider: str, provider_id: str) -> Optional[dict]:
    """Fetch a user by their OAuth provider identity.

    Args:
        provider: "google" or "github".
        provider_id: Provider-specific identity.

    Returns:
        User dict, or None if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM users WHERE provider = %s AND provider_id = %s",
            (provider, provider_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Fetch a user by their id.

    Args:
        user_id: The user's id.

    Returns:
        User dict, or None if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_active_users() -> int:
    """Count users whose status is 'active'.

    Returns:
        Number of active accounts.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE status = 'active'")
        return cur.fetchone()["cnt"]
    finally:
        conn.close()


def admit_user(user_id: int) -> Optional[dict]:
    """Promote a pending (waitlisted) user to active.

    Args:
        user_id: The user's id.

    Returns:
        The updated user dict, or None if not found.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING *",
            (user_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    finally:
        conn.close()


def link_oauth_identity(user_id: int, provider: str, provider_id: str) -> dict:
    """Attach an OAuth identity to an existing account.

    Args:
        user_id: The existing user's id.
        provider: "google" or "github".
        provider_id: Provider-specific identity.

    Returns:
        The updated user dict.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET provider = %s, provider_id = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING *",
            (provider, provider_id, user_id),
        )
        user = dict(cur.fetchone())
        conn.commit()
        return user
    finally:
        conn.close()


def save_resume(content: bytes, filename: str, mime_type: str, user_id: Optional[int] = None) -> None:
    """Upsert a user's resume bytes.

    Args:
        content: Raw resume file bytes.
        filename: Original filename.
        mime_type: MIME type of the file.
        user_id: Owning user id; defaults to the request context.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO resumes (user_id, filename, mime_type, content, updated_at)
               VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (user_id) DO UPDATE SET
                 filename = EXCLUDED.filename,
                 mime_type = EXCLUDED.mime_type,
                 content = EXCLUDED.content,
                 updated_at = CURRENT_TIMESTAMP""",
            (uid, filename, mime_type, psycopg2.Binary(content)),
        )
        conn.commit()
    finally:
        conn.close()


def get_resume(user_id: Optional[int] = None) -> Optional[dict]:
    """Fetch a user's stored resume.

    Args:
        user_id: Owning user id; defaults to the request context.

    Returns:
        Dict with filename, mime_type, and content bytes, or None if none stored.
    """
    uid = _resolve_user_id(user_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT filename, mime_type, content FROM resumes WHERE user_id = %s",
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "filename": row["filename"],
            "mime_type": row["mime_type"],
            "content": bytes(row["content"]),
        }
    finally:
        conn.close()
