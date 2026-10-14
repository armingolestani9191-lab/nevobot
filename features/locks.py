import logging
import unicodedata

from database import get_connection

logger = logging.getLogger("NevoBot.Locks")


# ==========================================
#             Lock Definitions
# ==========================================

LOCKS = {
    "photo": {
        "name": "عکس",
        "aliases": {"عکس", "تصویر"},
    },
    "video": {
        "name": "ویدیو",
        "aliases": {"ویدیو", "فیلم"},
    },
    "music": {
        "name": "موسیقی",
        "aliases": {"موسیقی", "آهنگ", "صوت"},
    },
    "voice": {
        "name": "ویس",
        "aliases": {"ویس", "صوتی", "پیام صوتی"},
    },
    "document": {
        "name": "فایل",
        "aliases": {"فایل", "سند", "داکیومنت"},
    },
    "forward": {
        "name": "فوروارد",
        "aliases": {"فوروارد", "فورواردی"},
    },
    "location": {
        "name": "موقعیت مکانی",
        "aliases": {
            "موقعیت مکانی",
            "موقعیت",
            "لوکیشن",
            "مکان",
        },
    },
    "sticker": {
        "name": "استیکر",
        "aliases": {"استیکر"},
    },
    "animation": {
        "name": "گیف",
        "aliases": {"گیف", "انیمیشن"},
    },
    "contact": {
        "name": "مخاطب",
        "aliases": {"مخاطب", "شماره"},
    },
    "video_note": {
        "name": "ویدیو مسیج",
        "aliases": {
            "ویدیو مسیج",
            "ویدیو پیام",
            "ویدیو گرد",
        },
    },
}


# ==========================================
#               Text Helpers
# ==========================================

def normalize_text(value):
    """یکسان‌سازی متن برای تشخیص دستورهای فارسی."""

    value = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )

    value = (
        value
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .replace("\u200f", "")
        .replace("\u200e", "")
    )

    return " ".join(value.lower().split()).strip()


def is_group(message):
    chat = message.get("chat") or {}

    return chat.get("type") in {
        "group",
        "supergroup",
    }


# ==========================================
#                Locks Feature
# ==========================================

class LocksFeature:
    """مدیریت قفل رسانه‌ها و پیام‌های گروه."""

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    # --------------------------------------
    # Database
    # --------------------------------------

    @staticmethod
    def init_database():
        """ساخت جدول اختصاصی قفل‌ها."""

        with get_connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS group_locks (
                    chat_id TEXT NOT NULL,
                    lock_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, lock_key)
                )
            """)

            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_group_locks_chat_id
                ON group_locks(chat_id)
            """)

    @staticmethod
    def get_lock_status(chat_id, lock_key):
        """دریافت وضعیت یک قفل در یک گروه."""

        try:
            with get_connection() as connection:
                row = connection.execute("""
                    SELECT enabled
                    FROM group_locks
                    WHERE chat_id = ?
                      AND lock_key = ?
                    LIMIT 1
                """, (
                    str(chat_id),
                    lock_key,
                )).fetchone()

            return bool(row["enabled"]) if row else False

        except Exception:
            logger.exception(
                "Could not read lock | chat=%s | lock=%s",
                chat_id,
                lock_key,
            )

            return None

    @staticmethod
    def get_enabled_locks(chat_id):
        """دریافت تمام قفل‌های روشن یک گروه."""

        try:
            with get_connection() as connection:
                rows = connection.execute("""
                    SELECT lock_key
                    FROM group_locks
                    WHERE chat_id = ?
                      AND enabled = 1
                """, (
                    str(chat_id),
                )).fetchall()

            return {
                row["lock_key"]
                for row in rows
            }

        except Exception:
            logger.exception(
                "Could not read group locks | chat=%s",
                chat_id,
            )

            return None

    @staticmethod
    def set_lock_status(chat_id, lock_key, enabled):
        """ثبت وضعیت قفل در دیتابیس."""

        if lock_key not in LOCKS:
            return False

        try:
            with get_connection() as connection:
                connection.execute("""
                    INSERT INTO group_locks (
                        chat_id,
                        lock_key,
                        enabled,
                        updated_at
                    )
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)

                    ON CONFLICT(chat_id, lock_key)
                    DO UPDATE SET
                        enabled = excluded.enabled,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    str(chat_id),
                    lock_key,
                    1 if enabled else 0,
                ))

            return True

        except Exception:
            logger.exception(
                "Could not save lock | chat=%s | lock=%s",
                chat_id,
                lock_key,
            )

            return False

    # --------------------------------------
    # Command Parsing
    # --------------------------------------

    @staticmethod
    def parse_command(text):
        """
        دستورهای پشتیبانی‌شده:

        قفل عکس روشن
        قفل عکس خاموش
        قفل عکس
        """

        command = normalize_text(text)

        if not command.startswith("قفل "):
            return None

        parts = command.split()

        if len(parts) < 2:
            return None

        action = "status"
        lock_name_parts = parts[1:]

        if parts[-1] in {"روشن", "خاموش"}:
            action = (
                "enable"
                if parts[-1] == "روشن"
                else "disable"
            )

            lock_name_parts = parts[1:-1]

        if not lock_name_parts:
            return None

        lock_name = " ".join(lock_name_parts)

        for lock_key, info in LOCKS.items():
            aliases = {
                normalize_text(alias)
                for alias in info["aliases"]
            }

            if lock_name in aliases:
                return lock_key, action

        return None

    # --------------------------------------
    # Authorization
    # --------------------------------------

    def _is_authorized_safely(self, message):
        """
        دسترسی مدیر یا ناظر ثبت‌شده را بررسی می‌کند.
        اگر بررسی خطا بدهد، تنظیم تغییر نمی‌کند.
        """

        try:
            return bool(self.is_authorized(message))

        except Exception:
            logger.exception(
                "Could not verify lock permissions"
            )

            return None

    # --------------------------------------
    # Lock Commands
    # --------------------------------------

    def _handle_command(self, message, lock_key, action):
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return {
                "text": "❌ شناسه گروه معتبر نیست."
            }

        lock_name = LOCKS[lock_key]["name"]

        # نمایش وضعیت قفل
        if action == "status":
            enabled = self.get_lock_status(
                chat_id,
                lock_key,
            )

            if enabled is None:
                return {
                    "text": "❌ دریافت وضعیت قفل ناموفق بود."
                }

            status_text = (
                "🔒 روشن"
                if enabled
                else "🔓 خاموش"
            )

            return {
                "text": (
                    f"📌 وضعیت قفل {lock_name}\n\n"
                    f"وضعیت: {status_text}"
                )
            }

        # تغییر تنظیمات فقط برای مدیر یا ناظر
        authorized = self._is_authorized_safely(message)

        if authorized is None:
            return {
                "text": (
                    "❌ بررسی دسترسی ناموفق بود؛ "
                    "تنظیم تغییر نکرد."
                )
            }

        if not authorized:
            return {
                "text": (
                    "⛔ فقط ادمین گروه یا ناظر ثبت‌شده "
                    "می‌تواند قفل‌ها را مدیریت کند."
                )
            }

        enabled = action == "enable"

        saved = self.set_lock_status(
            chat_id,
            lock_key,
            enabled,
        )

        if not saved:
            return {
                "text": (
                    f"❌ ذخیره وضعیت قفل {lock_name} "
                    "ناموفق بود."
                )
            }

        if enabled:
            return {
                "text": (
                    f"🔒 قفل {lock_name} روشن شد."
                )
            }

        return {
            "text": (
                f"🔓 قفل {lock_name} خاموش شد."
            )
        }

    # --------------------------------------
    # Message Detection
    # --------------------------------------

    @staticmethod
    def detect_message_type(message):
        """
        تشخیص نوع رسانه پیام با استفاده از فیلدهای
        موجود در پیام‌های دریافتی بله.
        """

        # فوروارد
        if any((
            message.get("forward_from"),
            message.get("forward_from_chat"),
            message.get("forward_from_message_id"),
            message.get("forward_date"),
            message.get("forward_origin"),
            message.get("is_automatic_forward"),
        )):
            return "forward"

        # عکس
        if message.get("photo"):
            return "photo"

        # ویدیو
        if message.get("video"):
            return "video"

        # موسیقی یا فایل صوتی
        if message.get("audio"):
            return "music"

        # پیام صوتی
        if message.get("voice"):
            return "voice"

        # ویدیو مسیج
        if message.get("video_note"):
            return "video_note"

        # فایل
        if message.get("document"):
            return "document"

        # استیکر
        if message.get("sticker"):
            return "sticker"

        # گیف یا انیمیشن
        if message.get("animation"):
            return "animation"

        # موقعیت مکانی
        if message.get("location") or message.get("venue"):
            return "location"

        # مخاطب
        if message.get("contact"):
            return "contact"

        return None

    # --------------------------------------
    # Message Deletion
    # --------------------------------------

    def _delete_message(self, chat_id, message_id):
        """حذف پیام ممنوع و بررسی نتیجه API."""

        if chat_id is None or message_id is None:
            return False

        try:
            result = self.api(
                "deleteMessage",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
            )

        except Exception:
            logger.exception(
                "Could not delete locked message"
            )

            return False

        if not isinstance(result, dict):
            logger.warning(
                "Invalid deleteMessage response"
            )

            return False

        if result.get("ok") is not True:
            logger.warning(
                "Could not delete locked message | "
                "chat=%s | message=%s | response=%r",
                chat_id,
                message_id,
                result,
            )

            return False

        return True

    # --------------------------------------
    # Main Handler
    # --------------------------------------

    def handle_message(self, message):
        """پردازش دستورهای قفل و جلوگیری از رسانه ممنوع."""

        if not isinstance(message, dict):
            return None

        if not is_group(message):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")
        message_id = message.get("message_id")

        if chat_id is None or user_id is None:
            return None

        text = message.get("text", "")

        # ابتدا بررسی دستورهای قفل
        if isinstance(text, str):
            parsed = self.parse_command(text)

            if parsed is not None:
                lock_key, action = parsed

                return self._handle_command(
                    message,
                    lock_key,
                    action,
                )

        # پیام خود ربات بررسی نشود
        if sender.get("is_bot"):
            return None

        # قفل‌های فعال گروه
        enabled_locks = self.get_enabled_locks(chat_id)

        if enabled_locks is None:
            # اگر دیتابیس در دسترس نیست، پیام را حذف نکن.
            return None

        if not enabled_locks:
            return None

        message_type = self.detect_message_type(message)

        if message_type is None:
            return None

        # آیا قفل مربوط به این نوع پیام روشن است؟
        if message_type not in enabled_locks:
            return None

        # مدیران و ناظرها از قفل‌ها مستثنا هستند.
        authorized = self._is_authorized_safely(message)

        if authorized is None:
            # در صورت خطای بررسی دسترسی، پیام را حذف نکن.
            return None

        if authorized:
            return None

        deleted = self._delete_message(
            chat_id,
            message_id,
        )

        if deleted:
            logger.info(
                "Locked message deleted | chat=%s | "
                "user=%s | type=%s",
                chat_id,
                user_id,
                message_type,
            )

            return {
                "deleted": True,
            }

        return None
