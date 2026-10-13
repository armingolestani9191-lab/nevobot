import logging
import sqlite3
import unicodedata

from database import get_connection

logger = logging.getLogger("NevoBot.Moderators")


def normalize_text(value):
    """یکسان‌سازی متن دستورهای فارسی و انگلیسی."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))
    value = value.replace("\u200c", " ")
    value = value.replace("\u200f", "").replace("\u200e", "")
    return " ".join(value.lower().split()).strip()


ADD_ALIASES = {
    "ناظر شو",
    "ناظر کن",
    "ناظر کنه",
    "ناظر",
    "/promote_mod",
    "/addmod",
}

REMOVE_ALIASES = {
    "عزل ناظر",
    "ناظر بردار",
    "ناظر نباش",
    "حذف ناظر",
    "/demote_mod",
    "/delmod",
}

LIST_ALIASES = {
    "ناظرها",
    "ناظر ها",
    "لیست ناظر",
    "لیست ناظرها",
    "لیست ناظر ها",
    "/mods",
    "/moderators",
}


class ModeratorsFeature:
    """
    مدیریت ناظرهای NevoBot.

    هر ناظر فقط برای همان chat_id ثبت می‌شود.
    اضافه‌کردن و عزل ناظر فقط با تأیید ادمین واقعی گروه مجاز است.
    """

    def __init__(self, is_group_admin):
        self.is_group_admin = is_group_admin
        self._init_table()

    @staticmethod
    def _init_table():
        """ساخت جدول ناظرها در صورت نبودن."""
        with get_connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS group_moderators (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    added_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_group_moderators_user_id
                ON group_moderators(user_id)
            """)

    @staticmethod
    def _is_group(message):
        chat = message.get("chat") or {}
        return chat.get("type") in {"group", "supergroup"}

    @staticmethod
    def _get_target(message):
        """شناسه فردی که به پیامش ریپلای شده‌ایم."""
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    @staticmethod
    def _get_sender_id(message):
        sender = message.get("from") or {}
        return sender.get("id")

    @staticmethod
    def _get_chat_id(message):
        chat = message.get("chat") or {}
        return chat.get("id")

    @staticmethod
    def _parse_command(text):
        normalized = normalize_text(text)

        if normalized in {normalize_text(x) for x in ADD_ALIASES}:
            return "add"

        if normalized in {normalize_text(x) for x in REMOVE_ALIASES}:
            return "remove"

        if normalized in {normalize_text(x) for x in LIST_ALIASES}:
            return "list"

        # پشتیبانی از دستورهایی مثل /mods@NevoBot
        first = normalized.split(maxsplit=1)[0] if normalized else ""
        if first.startswith("/"):
            first = first.split("@", 1)[0]

            if first in {"/promote_mod", "/addmod"}:
                return "add"

            if first in {"/demote_mod", "/delmod"}:
                return "remove"

            if first in {"/mods", "/moderators"}:
                return "list"

        return None

    def is_moderator(self, chat_id, user_id):
        """بررسی ناظر بودن یک کاربر در یک گروه مشخص."""
        if chat_id is None or user_id is None:
            return False

        try:
            with get_connection() as connection:
                row = connection.execute("""
                    SELECT 1
                    FROM group_moderators
                    WHERE chat_id = ? AND user_id = ?
                    LIMIT 1
                """, (str(chat_id), str(user_id))).fetchone()

            return row is not None

        except sqlite3.Error:
            logger.exception("Could not check moderator status")
            return False

    def add_moderator(self, chat_id, user_id, added_by):
        """ثبت ناظر؛ اگر از قبل ناظر باشد دوباره ثبت نمی‌شود."""
        if chat_id is None or user_id is None or added_by is None:
            return False, "شناسه گروه یا کاربر معتبر نیست."

        try:
            with get_connection() as connection:
                cursor = connection.execute("""
                    INSERT OR IGNORE INTO group_moderators (
                        chat_id,
                        user_id,
                        added_by
                    )
                    VALUES (?, ?, ?)
                """, (
                    str(chat_id),
                    str(user_id),
                    str(added_by),
                ))

                added = cursor.rowcount > 0

            if not added:
                return False, "این کاربر از قبل ناظر این گروه است."

            return True, "کاربر با موفقیت ناظر شد."

        except sqlite3.Error:
            logger.exception("Could not add moderator")
            return False, "ذخیره ناظر در دیتابیس ناموفق بود."

    def remove_moderator(self, chat_id, user_id):
        """حذف ناظر از یک گروه؛ بدون تغییر دسترسی او در گروه‌های دیگر."""
        if chat_id is None or user_id is None:
            return False, "شناسه گروه یا کاربر معتبر نیست."

        try:
            with get_connection() as connection:
                cursor = connection.execute("""
                    DELETE FROM group_moderators
                    WHERE chat_id = ? AND user_id = ?
                """, (str(chat_id), str(user_id)))

                removed = cursor.rowcount > 0

            if not removed:
                return False, "این کاربر ناظر این گروه نیست."

            return True, "دسترسی ناظر این کاربر برداشته شد."

        except sqlite3.Error:
            logger.exception("Could not remove moderator")
            return False, "حذف ناظر از دیتابیس ناموفق بود."

    def list_moderators(self, chat_id):
        """دریافت فهرست ناظرهای همان گروه."""
        try:
            with get_connection() as connection:
                rows = connection.execute("""
                    SELECT
                        gm.user_id,
                        u.first_name,
                        u.username
                    FROM group_moderators AS gm
                    LEFT JOIN users AS u
                        ON u.user_id = gm.user_id
                    WHERE gm.chat_id = ?
                    ORDER BY gm.created_at ASC, gm.user_id ASC
                """, (str(chat_id),)).fetchall()

            return [dict(row) for row in rows]

        except sqlite3.Error:
            logger.exception("Could not list moderators")
            return None

    def handle_message(self, message):
        """
        اگر پیام دستور ناظر باشد، نتیجه را برمی‌گرداند.
        اگر مربوط به این قابلیت نباشد، None برمی‌گرداند.
        """
        if not isinstance(message, dict) or not self._is_group(message):
            return None

        text = message.get("text", "")
        if not isinstance(text, str):
            return None

        action = self._parse_command(text)
        if action is None:
            return None

        chat_id = self._get_chat_id(message)
        sender_id = self._get_sender_id(message)

        if chat_id is None or sender_id is None:
            return {
                "ok": False,
                "text": "شناسه گروه یا فرستنده پیام موجود نیست.",
            }

        # مدیریت ناظرها فقط دست ادمین واقعی گروه است.
        try:
            if not self.is_group_admin(message):
                return {
                    "ok": False,
                    "text": "فقط ادمین واقعی گروه می‌تواند ناظرها را مدیریت کند.",
                }
        except Exception:
            logger.exception("Moderator management authorization failed")
            return {
                "ok": False,
                "text": "بررسی ادمین گروه ناموفق بود؛ دستور اجرا نشد.",
            }

        if action == "list":
            moderators = self.list_moderators(chat_id)

            if moderators is None:
                return {
                    "ok": False,
                    "text": "دریافت فهرست ناظرها ناموفق بود.",
                }

            if not moderators:
                return {
                    "ok": True,
                    "text": "👮 هنوز ناظری برای این گروه ثبت نشده است.",
                }

            lines = ["👮 ناظرهای این گروه:", ""]

            for index, moderator in enumerate(moderators, start=1):
                user_id = moderator["user_id"]
                name = moderator.get("first_name") or ""
                username = moderator.get("username") or ""

                if username:
                    display = f"@{username}"
                elif name:
                    display = name
                else:
                    display = f"کاربر {user_id}"

                lines.append(f"{index}. {display} — شناسه: {user_id}")

            return {
                "ok": True,
                "text": "\n".join(lines),
            }

        target_id = self._get_target(message)

        if target_id is None:
            return {
                "ok": False,
                "text": (
                    "برای این دستور باید روی پیام کاربر موردنظر ریپلای کنی."
                ),
            }

        if str(target_id) == str(sender_id):
            return {
                "ok": False,
                "text": "نمی‌توانی خودت را ناظر کنی یا دسترسی ناظر خودت را برداری.",
            }

        if action == "add":
            success, result_text = self.add_moderator(
                chat_id,
                target_id,
                sender_id,
            )
            return {"ok": success, "text": result_text}

        if action == "remove":
            success, result_text = self.remove_moderator(
                chat_id,
                target_id,
            )
            return {"ok": success, "text": result_text}

        return None
