
import sqlite3
from pathlib import Path

from config import DATABASE_PATH


# ==========================================
#          NevoBot | Database
# ==========================================

def get_connection():
    """ساخت اتصال به دیتابیس اصلی ربات."""

    db_path = Path(DATABASE_PATH)

    # ساخت پوشه دیتابیس در صورت نیاز
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(db_path),
        timeout=30
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def init_database():
    """ساخت جدول‌های موردنیاز ربات."""

    with get_connection() as connection:

        # گروه‌ها
        connection.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                welcome_enabled INTEGER NOT NULL DEFAULT 0,
                anti_spam_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # کاربران
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                first_name TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # أعضاء المجموعات وإحصائيات الرسائل
        connection.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                last_message_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id),
                FOREIGN KEY (chat_id)
                    REFERENCES groups(chat_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            )
        """)

        # إعدادات عامة ومخصصة
        connection.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
        """)

        # فهرس لتحسين استعلام‌های آماری
        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_group_members_user_id
            ON group_members(user_id)
        """)

        connection.commit()


def save_user(user_id, username="", first_name=""):
    """ثبت کاربر جدید یا به‌روزرسانی اطلاعات کاربر."""

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO users (
                user_id,
                username,
                first_name
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


def save_group(chat_id, title=""):
    """ثبت گروه جدید یا به‌روزرسانی نام گروه."""

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO groups (
                chat_id,
                title
            )
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title
        """, (
            str(chat_id),
            title or ""
        ))


def record_group_message(chat_id, user_id):
    """ثبت یک پیام برای عضو گروه."""

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO group_members (
                chat_id,
                user_id,
                message_count
            )
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                message_count = message_count + 1,
                last_message_at = CURRENT_TIMESTAMP
        """, (
            str(chat_id),
            str(user_id)
        ))


def get_group(chat_id):
    """دریافت اطلاعات یک گروه."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM groups
            WHERE chat_id = ?
        """, (str(chat_id),)).fetchone()

        return dict(row) if row else None


def get_user(user_id):
    """دریافت اطلاعات یک کاربر."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM users
            WHERE user_id = ?
        """, (str(user_id),)).fetchone()

        return dict(row) if row else None


def get_group_stats(chat_id):
    """آمار کلی اعضا و پیام‌های یک گروه."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT
                COUNT(*) AS member_count,
                COALESCE(SUM(message_count), 0) AS total_messages
            FROM group_members
            WHERE chat_id = ?
        """, (str(chat_id),)).fetchone()

        return dict(row)


def get_setting(key, default=None):
    """دریافت یک تنظیم عمومی."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT value
            FROM settings
            WHERE key = ?
        """, (str(key),)).fetchone()

        return row["value"] if row else default


def set_setting(key, value):
    """ثبت یا به‌روزرسانی یک تنظیم عمومی."""

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value
        """, (
            str(key),
            str(value)
        ))


def set_group_feature(chat_id, feature, enabled):
    """فعال یا غیرفعال کردن قابلیت مجاز یک گروه."""

    allowed_features = {
        "welcome_enabled",
        "anti_spam_enabled",
    }

    if feature not in allowed_features:
        raise ValueError("نام قابلیت معتبر نیست.")

    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE groups
            SET {feature} = ?
            WHERE chat_id = ?
            """,
            (1 if enabled else 0, str(chat_id))
        )

        return cursor.rowcount > 0


def get_top_group_members(chat_id, limit=10):
    """دریافت اعضای فعال‌تر یک گروه."""

    limit = max(1, min(int(limit), 100))

    with get_connection() as connection:
        rows = connection.execute("""
            SELECT
                users.user_id,
                users.username,
                users.first_name,
                group_members.message_count
            FROM group_members
            JOIN users
                ON users.user_id = group_members.user_id
            WHERE group_members.chat_id = ?
            ORDER BY group_members.message_count DESC
            LIMIT ?
        """, (str(chat_id), limit)).fetchall()

        return [dict(row) for row in rows]
