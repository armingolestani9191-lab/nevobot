# features/whisper.py

import logging

from database import get_connection

logger = logging.getLogger("NevoBot.Whisper")

MAX_WHISPER_LENGTH = 3000


def init_whisper():
    """ساخت جدول نجوا و افزودن ستون‌های جدید به دیتابیس قبلی."""

    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS whispers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                group_message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL DEFAULT '',
                recipient_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                opened_at TEXT
            )
        """)

        # سازگاری با جدول‌های قدیمی که sender_name ندارند
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()
        }

        if "sender_name" not in columns:
            connection.execute("""
                ALTER TABLE whispers
                ADD COLUMN sender_name TEXT NOT NULL DEFAULT ''
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
    sender_name="",
):
    """ذخیره متن نجوا و اسم فرستنده در لحظه ارسال."""

    text = str(text or "").strip()
    sender_name = str(sender_name or "").strip()

    if not text:
        raise ValueError("متن نجوا خالی است.")

    if len(text) > MAX_WHISPER_LENGTH:
        raise ValueError("متن نجوا بیش از حد طولانی است.")

    if not sender_name:
        sender_name = "کاربر"

    with get_connection() as connection:
        cursor = connection.execute("""
            INSERT INTO whispers (
                chat_id,
                group_message_id,
                sender_id,
                sender_name,
                recipient_id,
                text
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(chat_id),
            str(group_message_id),
            str(sender_id),
            sender_name,
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
    """دریافت نجوا فقط برای مخاطب اصلی."""

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

        return dict(row) if row else None


def mark_whisper_opened(whisper_id, recipient_id):
    """ثبت زمان اولین بازشدن نجوا."""

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
