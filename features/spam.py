# ==========================================
#             NevoBot | Anti-Spam
# ==========================================

import logging
import time
import unicodedata

from collections import defaultdict, deque

from database import get_group, set_group_feature


logger = logging.getLogger("NevoBot.Spam")


# ==========================================
#              Text Normalization
# ==========================================

def normalize_text(value):
    """یکسان‌سازی متن فارسی و اعداد برای تشخیص فرمان‌ها."""

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

    return " ".join(
        value.lower().split()
    ).strip()


# ==========================================
#                Spam Feature
# ==========================================

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

        # کلید هر صف:
        # (شناسه گروه، شناسه کاربر)
        #
        # هر رویداد:
        # (زمان، شناسه پیام، امضای پیام)

        self._recent = defaultdict(deque)

    # ==========================================
    #                 Group Check
    # ==========================================

    @staticmethod
    def _is_group(message):
        chat = message.get("chat") or {}

        return str(
            chat.get("type", "")
        ).lower() in {
            "group",
            "supergroup",
        }

    # ==========================================
    #              Message Signature
    # ==========================================

    @staticmethod
    def _signature(message):
        """تشخیص متن یا رسانه تکراری."""

        text = message.get("text")

        if (
            not isinstance(text, str)
            or not text.strip()
        ):
            text = message.get("caption")

        if (
            isinstance(text, str)
            and text.strip()
        ):
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

                return (
                    f"{key}:{unique_id or key}"
                )

            return f"{key}:{key}"

        return "other"

    # ==========================================
    #              Enable / Disable
    # ==========================================

    def _set_enabled(self, chat_id, enabled):
        """ذخیره وضعیت ضداسپم گروه."""

        try:
            saved = set_group_feature(
                chat_id,
                "anti_spam_enabled",
                enabled,
            )

        except Exception:
            logger.exception(
                "Could not save anti-spam setting"
            )

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

        # پاک‌کردن سابقه قبلی همان گروه.

        prefix = str(chat_id)

        for key in list(self._recent):
            if key[0] == prefix:
                self._recent.pop(
                    key,
                    None,
                )

        if enabled:
            logger.info(
                "Anti-spam enabled | chat=%s",
                chat_id,
            )

            return {
                "text": (
                    "✅ ضداسپم این گروه روشن شد."
                )
            }

        logger.info(
            "Anti-spam disabled | chat=%s",
            chat_id,
        )

        return {
            "text": (
                "🔕 ضداسپم این گروه خاموش شد."
            )
        }

    # ==========================================
    #                 Delete Message
    # ==========================================

    def _delete(self, chat_id, message_id):
        """حذف پیام و بررسی پاسخ API بله."""

        if message_id is None:
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
                "Could not delete spam message | "
                "chat=%s | message=%s",
                chat_id,
                message_id,
            )

            return False

        if (
            not isinstance(result, dict)
            or result.get("ok") is not True
        ):
            logger.warning(
                "Spam deletion failed | "
                "chat=%s | message=%s | response=%r",
                chat_id,
                message_id,
                result,
            )

            return False

        return True

    # ==========================================
    #            Safe Authorization Check
    # ==========================================

    def _is_authorized_safely(self, message):
        """
        اگر بررسی دسترسی ناموفق بود،
        عملیات مدیریتی یا حذف بر اساس ضداسپم انجام نشود.
        """

        try:
            return bool(
                self.is_authorized(message)
            )

        except Exception:
            logger.exception(
                "Could not verify sender role for anti-spam"
            )

            return None

    # ==========================================
    #               Handle Message
    # ==========================================

    def handle_message(self, message):
        """اجرای دستورات ضداسپم و تشخیص موج اسپم."""

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

        if (
            chat_id is None
            or user_id is None
        ):
            return None

        command = (
            normalize_text(text)
            if isinstance(text, str)
            else ""
        )

        # ======================================
        #             Settings Commands
        # ======================================

        all_commands = (
            self.ENABLE_COMMANDS
            | self.DISABLE_COMMANDS
        )

        if command in all_commands:

            authorized = (
                self._is_authorized_safely(
                    message
                )
            )

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

        # ======================================
        #                Ignore Bots
        # ======================================

        if sender.get("is_bot"):
            return None

        # ======================================
        #              Read Group Settings
        # ======================================

        try:
            group = get_group(chat_id)

        except Exception:
            logger.exception(
                "Could not read anti-spam setting | "
                "chat=%s",
                chat_id,
            )

            return None

        if (
            not group
            or not bool(
                group.get("anti_spam_enabled")
            )
        ):
            return None

        # ======================================
        #            Moderator Exemption
        # ======================================

        authorized = (
            self._is_authorized_safely(
                message
            )
        )

        if authorized is None:
            return None

        if authorized:
            self._recent.pop(
                (
                    str(chat_id),
                    str(user_id),
                ),
                None,
            )

            return None

        # ======================================
        #               Record Message
        # ======================================

        now = time.monotonic()

        key = (
            str(chat_id),
            str(user_id),
        )

        signature = self._signature(
            message
        )

        events = self._recent[key]

        # حذف رویدادهای خارج از بازه زمانی.

        while (
            events
            and now - events[0][0]
            > self.DUPLICATE_WINDOW_SECONDS
        ):
            events.popleft()

        events.append(
            (
                now,
                message_id,
                signature,
            )
        )

        # ======================================
        #             Detect Message Flood
        # ======================================

        recent_messages = [
            event
            for event in events
            if (
                now - event[0]
                <= self.MESSAGE_WINDOW_SECONDS
            )
        ]

        flood_detected = (
            len(recent_messages)
            >= self.MESSAGE_LIMIT
        )

        # ======================================
        #            Detect Duplicate Spam
        # ======================================

        duplicate_count = sum(
            1
            for event in events
            if event[2] == signature
        )

        duplicate_detected = (
            signature != "other"
            and duplicate_count
            >= self.DUPLICATE_LIMIT
        )

        # هنوز تخلفی تشخیص داده نشده است.

        if (
            not flood_detected
            and not duplicate_detected
        ):
            return None

        # ======================================
        #               Confirm Violation
        # ======================================

        reason = (
            "duplicate"
            if duplicate_detected
            else "flood"
        )

        # یک‌بار از رویدادهای موج فعلی نسخه می‌گیریم.

        to_delete = list(events)

        deleted_ids = set()
        successfully_deleted = 0

        # ======================================
        #              Delete Spam Wave
        # ======================================

        for (
            _event_time,
            spam_message_id,
            _signature,
        ) in to_delete:

            if spam_message_id is None:
                continue

            # جلوگیری از درخواست تکراری برای یک شناسه.

            if spam_message_id in deleted_ids:
                continue

            deleted_ids.add(
                spam_message_id
            )

            if self._delete(
                chat_id,
                spam_message_id,
            ):
                successfully_deleted += 1

        # ======================================
        #                   Logging
        # ======================================

        logger.info(
            "Spam violation confirmed | "
            "chat=%s | user=%s | reason=%s | "
            "candidates=%s | deleted=%s",
            chat_id,
            user_id,
            reason,
            len(deleted_ids),
            successfully_deleted,
        )

        # پاک‌کردن موج فعلی برای جلوگیری از شمارش
        # دوباره همان پیام‌ها در موج بعدی.

        events.clear()

        # ======================================
        #                 Result
        # ======================================

        # نکته سازگاری:
        # main.py فعلی از deleted=True برای ثبت اخطار
        # استفاده می‌کند. بنابراین این فیلد نشان‌دهنده
        # تشخیص تخلف است، نه تضمین حذف موفق تمام پیام‌ها.
        #
        # deleted_count تعداد پیام‌هایی است که API
        # حذف آن‌ها را با موفقیت تأیید کرده است.

        return {
            "deleted": True,
            "violation": True,
            "reason": reason,
            "deleted_count": successfully_deleted,
            "deletion_succeeded": (
                successfully_deleted > 0
            ),
        }
