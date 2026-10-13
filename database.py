import sqlite3
from pathlib import Path

from config import DATABASE_PATH


def get_connection():
    path = Path(DATABASE_PATH)

    if DATABASE_PATH != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")

    return connection


def init_database():
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                welcome_enabled INTEGER NOT NULL DEFAULT 0,
                anti_spam_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                first_name TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                last_message_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
        """)

        connection.commit()


def save_user(user_id, username="", first_name=""):
    with get_connection() as connection:
        connection.execute("""
            INSERT INTO users (
                user_id, username, first_name
            )
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = CURRENT_TIMESTAMP
        """, (
            str(user_id),
            username or "",
            first_name or ""
        ))

        connection.commit()


def save_group(chat_id, title=""):
    with get_connection() as connection:
        connection.execute("""
            INSERT INTO groups (chat_id, title)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title
        """, (str(chat_id), title or ""))

        connection.commit()


def record_group_message(chat_id, user_id):
    with get_connection() as connection:
        connection.execute("""
            INSERT INTO group_members (
                chat_id, user_id, message_count
            )
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                message_count = message_count + 1,
                last_message_at = CURRENT_TIMESTAMP
        """, (str(chat_id), str(user_id)))

        connection.commit()
