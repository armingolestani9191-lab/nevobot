import logging
import time
import unicodedata
from collections import defaultdict, deque

from database import get_group, set_group_feature

logger = logging.getLogger("NevoBot.Spam")


def normalize_text(value):
    """یکسان‌سازی متن فارسی و اعداد برای تشخیص فرمان‌ها."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))
    value = (
        value.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .replace("\u200f", "")
        .replace("\u200e", "")
    )
    return " ".join(value.lower().split()).strip()


class SpamFeature:
    """مدیریت ضداسپم گروه‌های NevoBot."""

    MESSAGE_WINDOW_SECONDS = 5
    MESSAGE_LIMIT = 5
    DUPLICATE_WINDOW_SECONDS = 10
    DUPLICATE_LIMIT = 3

    ENABLE_COMMANDS = {
        "اسپم روشن",
        "روشن اسپم",
    }

    DISABLE_COMMANDS = {
        "خاموش اسپم",
        "اسپم خاموش",
    }

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

        # کلید هر صف: (شناسه گروه، شناسه کاربر)
        # هر رویداد: (زمان، شناسه پیام، امضای پیام)
        self._recent = defaultdict(deque)

    @staticmethod
    def _is_group(message):
        chat = message.get("chat") or {}
        return str(chat.get("type", "")).lower() in {
            "group",
            "supergroup",
        }

    @staticmethod
    def _signature(message):
        """تشخیص متن یا رسانه تکراری."""
        text = message.get("text")

        if not isinstance(text, str) or not text.strip():
            text = message.get("caption")

        if isinstance(text, str) and text.strip():
            return "text:" + normalize_text(text)

        media_keys = (
            "photo",
            "video",
            "animation",
            "document",
            "sticker",
            "voice",
            "audio",
            "video_note",
            "contact",
            "location",
        )

        for key in media_keys:
            item = message.get(key)

            if not item:
                continue

            if isinstance(item, list) and item:
                item = item[-1]

            if isinstance(item, dict):
                unique_id = (
                    item.get("file_unique_id")
                    or item.get("file_id")
                )
                return f"{key}:{unique_id or key}"

            return f"{key}:{key}"

        return "other"

    def _set_enabled(self, chat_id, enabled):
        """ذخیره وضعیت ضداسپم گروه."""
        try:
            saved = set_group_feature(
                chat_id,
                "anti_spam_enabled",
                enabled,
            )
        except Exception:
            logger.exception("Could not save anti-spam setting")
            return {
                "text": (
                    "❌ ذخیره تنظیم ضداسپم ناموفق بود؛ "
                    "دوباره تلاش کن."
                )
            }

        if not saved:
            return {
                "text": (
                    "❌ گروه در دیتابیس پیدا نشد؛ "
                    "یک پیام دیگر بفرست و دوباره تلاش کن."
                )
            }

        # پاک کردن سابقه قبلی همان گروه.
        prefix = str(chat_id)

        for key in list(self._recent):
            if key[0] == prefix:
                self._recent.pop(key, None)

        if enabled:
            return {
                "text": "✅ ضداسپم این گروه روشن شد."
            }

        return {
            "text": "🔕 ضداسپم این گروه خاموش شد."
        }

    def _delete(self, chat_id, message_id):
        """ارسال درخواست حذف پیام و بررسی پاسخ API."""
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
                "Could not delete spam message | chat=%s | message=%s",
                chat_id,
                message_id,
            )
            return False

        if not isinstance(result, dict) or result.get("ok") is not True:
            logger.warning(
                "Spam deletion failed | chat=%s | message=%s | response=%r",
                chat_id,
                message_id,
                result,
            )
            return False

        return True

    def _is_authorized_safely(self, message):
        """اگر بررسی دسترسی ناموفق بود، پیام را حذف نکن."""
        try:
            return bool(self.is_authorized(message))
        except Exception:
            logger.exception(
                "Could not verify sender role for anti-spam"
            )
            return None

    def handle_message(self, message):
        """اجرای تنظیمات ضداسپم یا تشخیص موج اسپم."""
        if not isinstance(message, dict):
            return None

        if not self._is_group(message):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")
        message_id = message.get("message_id")
        text = message.get("text", "")

        if chat_id is None or user_id is None:
            return None

        command = (
            normalize_text(text)
            if isinstance(text, str)
            else ""
        )

        # روشن و خاموش کردن ضداسپم
        all_commands = self.ENABLE_COMMANDS | self.DISABLE_COMMANDS

        if command in all_commands:
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
                        "می‌تواند ضداسپم را تنظیم کند."
                    )
                }

            return self._set_enabled(
                chat_id,
                command in self.ENABLE_COMMANDS,
            )

        # پیام خود ربات بررسی نشود.
        if sender.get("is_bot"):
            return None

        # بررسی وضعیت ضداسپم گروه
        try:
            group = get_group(chat_id)
        except Exception:
            logger.exception(
                "Could not read anti-spam setting"
            )
            return None

        if not group or not bool(group.get("anti_spam_enabled")):
            return None

        # ادمین و ناظر از تشخیص اسپم معاف هستند.
        authorized = self._is_authorized_safely(message)

        if authorized is None:
            return None

        if authorized:
            self._recent.pop(
                (str(chat_id), str(user_id)),
                None,
            )
            return None

        now = time.monotonic()
        key = (str(chat_id), str(user_id))
        signature = self._signature(message)
        events = self._recent[key]

        # فقط پیام‌های ۱۰ ثانیه اخیر نگهداری شوند.
        while (
            events
            and now - events[0][0]
            > self.DUPLICATE_WINDOW_SECONDS
        ):
            events.popleft()

        events.append((now, message_id, signature))

        recent_messages = [
            event
            for event in events
            if now - event[0] <= self.MESSAGE_WINDOW_SECONDS
        ]

        duplicate_count = sum(
            1
            for event in events
            if event[2] == signature
        )

        flood_detected = (
            len(recent_messages) >= self.MESSAGE_LIMIT
        )

        duplicate_detected = (
            signature != "other"
            and duplicate_count >= self.DUPLICATE_LIMIT
        )

        if not flood_detected and not duplicate_detected:
            return None

        # در صورت تشخیص اسپم، پیام‌های ثبت‌شده در موج فعلی
        # این کاربر در همین گروه برای حذف فرستاده می‌شوند.
        to_delete = list(events)
        deleted_ids = set()

        for _, spam_message_id, _ in to_delete:
            if spam_message_id is None:
                continue

            if spam_message_id in deleted_ids:
                continue

            deleted_ids.add(spam_message_id)
            self._delete(chat_id, spam_message_id)

        logger.info(
            "Spam detected | chat=%s | user=%s | reason=%s | candidates=%s",
            chat_id,
            user_id,
            "duplicate" if duplicate_detected else "flood",
            len(deleted_ids),
        )

        events.clear()

        # پیام حذف‌شده نباید برای پردازش عادی ادامه پیدا کند.
        return {"deleted": True}
