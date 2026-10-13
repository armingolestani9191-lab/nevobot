# features/whisper.py

import logging

from database import get_connection

logger = logging.getLogger("NevoBot.Whisper")

MAX_WHISPER_LENGTH = 3000


def init_whisper():
    """ساخت جدول نجوا در دیتابیس اصلی ربات."""

    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS whispers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                group_message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                opened_at TEXT
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_whispers_recipient
            ON whispers(recipient_id)
        """)

        connection.commit()


def save_whisper(
    chat_id,
    group_message_id,
    sender_id,
    recipient_id,
    text,
):
    """ذخیره نجوا در دیتابیس اصلی."""

    text = str(text or "").strip()

    if not text:
        raise ValueError("متن نجوا خالی است.")

    if len(text) > MAX_WHISPER_LENGTH:
        raise ValueError("متن نجوا بیش از حد طولانی است.")

    with get_connection() as connection:
        cursor = connection.execute("""
            INSERT INTO whispers (
                chat_id,
                group_message_id,
                sender_id,
                recipient_id,
                text
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            str(chat_id),
            str(group_message_id),
            str(sender_id),
            str(recipient_id),
            text,
        ))

        connection.commit()
        return cursor.lastrowid


def get_whisper(whisper_id):
    """دریافت اطلاعات یک نجوا."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM whispers
            WHERE id = ?
        """, (int(whisper_id),)).fetchone()

        return dict(row) if row else None


def open_whisper(whisper_id, recipient_id):
    """نمایش متن فقط برای دریافت‌کنندهٔ اصلی نجوا."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM whispers
            WHERE id = ?
              AND recipient_id = ?
        """, (
            int(whisper_id),
            str(recipient_id),
        )).fetchone()

        if not row:
            return None

        return dict(row)


def mark_whisper_opened(whisper_id, recipient_id):
    """ثبت زمان بازشدن نجوا."""

    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE whispers
            SET opened_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND recipient_id = ?
              AND opened_at IS NULL
        """, (
            int(whisper_id),
            str(recipient_id),
        ))

        connection.commit()
        return cursor.rowcount > 0
