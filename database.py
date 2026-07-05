import sqlite3
import json
import os
import uuid
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "history.db"))


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                location TEXT,
                source_trigger TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS search_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT,
                location TEXT,
                salary TEXT,
                link TEXT NOT NULL,
                source TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                search_id INTEGER REFERENCES searches(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_search_results_search_id ON search_results(search_id);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at);

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                preview TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
        """)
        conn.commit()

        # Migration: add session_id to chat_messages if not present
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
        if "session_id" not in cols:
            conn.executescript("""
                ALTER TABLE chat_messages ADD COLUMN session_id TEXT REFERENCES sessions(id);
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
            """)
            orphan_count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id IS NULL").fetchone()[0]
            if orphan_count > 0:
                legacy_id = str(uuid.uuid4())
                conn.execute("INSERT OR IGNORE INTO sessions (id, preview) VALUES (?, 'Legacy conversation')", (legacy_id,))
                conn.execute("UPDATE chat_messages SET session_id = ? WHERE session_id IS NULL", (legacy_id,))
            conn.commit()
            logger.info("Migrated chat_messages: added session_id column")
        conn.commit()
        logger.info("Database initialised at %s", DB_PATH)
    finally:
        conn.close()


def save_search(
    query: str,
    location: str,
    source_trigger: str,
    jobs: list[dict],
) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO searches (query, location, source_trigger) VALUES (?, ?, ?)",
            (query, location, source_trigger),
        )
        search_id = cur.lastrowid
        rows = [
            (search_id, j.get("title", ""), j.get("description", ""),
             j.get("location", ""), j.get("salary", ""), j.get("link", ""), j.get("source", ""))
            for j in jobs
        ]
        conn.executemany(
            "INSERT INTO search_results (search_id, title, description, location, salary, link, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return search_id
    finally:
        conn.close()


def get_history(page: int = 1, per_page: int = 10) -> tuple[list[dict], int]:
    conn = _get_conn()
    try:
        offset = (page - 1) * per_page
        total = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
        rows = conn.execute(
            """SELECT s.*, COUNT(sr.id) AS result_count
               FROM searches s
               LEFT JOIN search_results sr ON sr.search_id = s.id
               GROUP BY s.id
               ORDER BY s.created_at DESC
               LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def get_search(search_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM searches WHERE id = ?", (search_id,)
        ).fetchone()
        if not row:
            return None
        search = dict(row)
        results = conn.execute(
            "SELECT title, description, location, salary, link, source FROM search_results WHERE search_id = ?",
            (search_id,),
        ).fetchall()
        search["results"] = [dict(r) for r in results]
        return search
    finally:
        conn.close()


def delete_search(search_id: int) -> bool:
    conn = _get_conn()
    try:
        conn.execute("UPDATE chat_messages SET search_id = NULL WHERE search_id = ?", (search_id,))
        cur = conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_history() -> int:
    conn = _get_conn()
    try:
        deleted = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
        conn.executescript("DELETE FROM chat_messages; DELETE FROM search_results; DELETE FROM searches; DELETE FROM sessions;")
        conn.commit()
        return deleted
    finally:
        conn.close()


def create_session(preview: str = "") -> str:
    conn = _get_conn()
    try:
        sid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (id, preview) VALUES (?, ?)",
            (sid, preview or ""),
        )
        conn.commit()
        return sid
    finally:
        conn.close()


def update_session(session_id: str) -> None:
    conn = _get_conn()
    try:
        first = conn.execute(
            "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user' ORDER BY created_at ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        preview = (first["content"][:80] if first else "") if first else ""
        count = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE sessions SET preview = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (preview, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_sessions(limit: int = 5) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT s.id, s.preview, s.created_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS msg_count
               FROM sessions s
               ORDER BY s.updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session_messages(session_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0 or True
    finally:
        conn.close()


def save_chat_message(role: str, content: str, search_id: Optional[int] = None, session_id: Optional[str] = None) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO chat_messages (role, content, search_id, session_id) VALUES (?, ?, ?, ?)",
            (role, content, search_id, session_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recent_chat_messages(limit: int = 6, session_id: Optional[str] = None) -> list[dict]:
    conn = _get_conn()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content, created_at FROM chat_messages ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        rows.reverse()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_profile(data: dict) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO user_profile
               (id, full_name, email, phone, linkedin_url, github_url, website, skills, work_history, education, resume_name, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
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
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
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
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO applications (job_title, company, location, salary, link, source, status, cover_letter)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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
        return cur.lastrowid
    finally:
        conn.close()


def get_application(app_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_applications(status: Optional[str] = None) -> list[dict]:
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM applications WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM applications ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_application_status(app_id: int, status: str, applied_at: Optional[str] = None) -> bool:
    conn = _get_conn()
    try:
        if applied_at:
            cur = conn.execute(
                "UPDATE applications SET status = ?, applied_at = ? WHERE id = ?",
                (status, applied_at, app_id),
            )
        else:
            cur = conn.execute(
                "UPDATE applications SET status = ? WHERE id = ?",
                (status, app_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
