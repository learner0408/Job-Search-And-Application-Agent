"""
Migrate all data from SQLite (data/history.db) to a PostgreSQL database
specified by the DATABASE_URL environment variable.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db uv run python scripts/migrate_to_postgres.py
"""

import os
import sys
import json
import sqlite3

import psycopg2
from psycopg2.extras import RealDictCursor

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "history.db")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is not set.")
    sys.exit(1)


def fetch_sqlite(table: str) -> list[dict]:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def migrate_sessions(cur) -> None:
    rows = fetch_sqlite("sessions")
    for r in rows:
        cur.execute(
            "INSERT INTO sessions (id, preview, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (r["id"], r.get("preview", ""), r.get("created_at"), r.get("updated_at")),
        )
    print(f"  sessions: {len(rows)} rows")


def migrate_searches(cur) -> None:
    rows = fetch_sqlite("searches")
    for r in rows:
        cur.execute(
            "INSERT INTO searches (id, query, location, source_trigger, created_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (r["id"], r["query"], r.get("location"), r["source_trigger"], r.get("created_at")),
        )
    # Update sequence to max id
    cur.execute("SELECT setval('searches_id_seq', COALESCE((SELECT MAX(id) FROM searches), 0))")
    print(f"  searches: {len(rows)} rows")


def migrate_search_results(cur) -> None:
    rows = fetch_sqlite("search_results")
    for r in rows:
        cur.execute(
            "INSERT INTO search_results (id, search_id, title, description, location, salary, link, source) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (r["id"], r["search_id"], r["title"], r.get("description"), r.get("location"),
             r.get("salary"), r["link"], r["source"]),
        )
    cur.execute("SELECT setval('search_results_id_seq', COALESCE((SELECT MAX(id) FROM search_results), 0))")
    print(f"  search_results: {len(rows)} rows")


def migrate_chat_messages(cur) -> None:
    rows = fetch_sqlite("chat_messages")
    for r in rows:
        cur.execute(
            "INSERT INTO chat_messages (id, role, content, search_id, session_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (r["id"], r["role"], r["content"], r.get("search_id"), r.get("session_id"), r.get("created_at")),
        )
    cur.execute("SELECT setval('chat_messages_id_seq', COALESCE((SELECT MAX(id) FROM chat_messages), 0))")
    print(f"  chat_messages: {len(rows)} rows")


def migrate_user_profile(cur) -> None:
    rows = fetch_sqlite("user_profile")
    for r in rows:
        cur.execute(
            "INSERT INTO user_profile (id, full_name, email, phone, linkedin_url, github_url, website, "
            "skills, work_history, education, resume_name, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (
                r["id"], r.get("full_name", ""), r.get("email", ""), r.get("phone", ""),
                r.get("linkedin_url", ""), r.get("github_url", ""), r.get("website", ""),
                r.get("skills", "[]"), r.get("work_history", "[]"), r.get("education", "[]"),
                r.get("resume_name", ""), r.get("updated_at"),
            ),
        )
    print(f"  user_profile: {len(rows)} rows")


def migrate_applications(cur) -> None:
    rows = fetch_sqlite("applications")
    for r in rows:
        cur.execute(
            "INSERT INTO applications (id, job_title, company, location, salary, link, source, status, cover_letter, created_at, applied_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (
                r["id"], r["job_title"], r.get("company", ""), r.get("location", ""),
                r.get("salary", ""), r.get("link", ""), r.get("source", ""),
                r.get("status", "preparing"), r.get("cover_letter", ""),
                r.get("created_at"), r.get("applied_at"),
            ),
        )
    cur.execute("SELECT setval('applications_id_seq', COALESCE((SELECT MAX(id) FROM applications), 0))")
    print(f"  applications: {len(rows)} rows")


def main() -> None:
    print(f"Source: {SQLITE_PATH}")
    print(f"Target: {DATABASE_URL[:50]}...")
    print()

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # Import database to init tables
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from database import init_db
        init_db()
        print("  Tables created.")

        migrate_sessions(cur)
        migrate_searches(cur)
        migrate_search_results(cur)
        migrate_chat_messages(cur)
        migrate_user_profile(cur)
        migrate_applications(cur)

        conn.commit()
        print()
        print("Migration complete.")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
