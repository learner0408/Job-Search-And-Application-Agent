import sqlite3
import json
import os
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
        """)
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
        conn.executescript("DELETE FROM chat_messages; DELETE FROM search_results; DELETE FROM searches;")
        conn.commit()
        return deleted
    finally:
        conn.close()


def save_chat_message(role: str, content: str, search_id: Optional[int] = None) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO chat_messages (role, content, search_id) VALUES (?, ?, ?)",
            (role, content, search_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recent_chat_messages(limit: int = 6) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, created_at FROM chat_messages ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        rows.reverse()
        return [dict(r) for r in rows]
    finally:
        conn.close()
