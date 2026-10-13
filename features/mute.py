import logging
import re
import time
import unicodedata

logger = logging.getLogger("NevoBot.Mute")


def normalize_text(value):
    """یکسان‌سازی اعداد، فاصله‌ها و حروف فارسی و عربی."""
    value = unicodedata.normalize("NFKC", str(value or ""))

    value = value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))

    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ")
    value = value.replace("\u200f", "").replace("\u200e", "")

    return " ".join(value.lower().split()).strip()


MUTE_ALIASES = {
    "سکوت",
    "ساکت",
    "سکوت کن",
    "ساکت کن",
    "mute",
}

UNMUTE_ALIASES = {
    "حذف سکوت",
    "سکوت حذف",
    "رفع سکوت",
    "لغو سکوت",
    "برداشتن سکوت",
    "سکوت بردار",
    "ساکت نباش",
    "از سکوت دربیار",
    "آنسکوت",
    "unmute",
    "un mute",
}


def parse_mute_command(text):
    """
    خروجی:
        ("mute", None)       سکوت بدون مدت
        ("mute", 10)         سکوت ۱۰ دقیقه‌ای
        ("unmute", None)     رفع سکوت
        (None, None)         دستور نامرتبط
    """
    value = normalize_text(text)

    if value in {normalize_text(x) for x in UNMUTE_ALIASES}:
        return "unmute", None

    if value in {normalize_text(x) for x in MUTE_ALIASES}:
        return "mute", None

    # چند شکل رایج و غلط تایپی «دقیقه» هم پذیرفته می‌شود.
    minute_word = (
        r"(?:دقیقه|دیقه|دییقه|دقيقه|min|mins|minute|minutes)"
    )

    command_pattern = (
        rf"(?:سکوت|ساکت|سکوت کن|ساکت کن|mute)"
        rf"\s+(\d+)\s*{minute_word}"
    )

    match = re.fullmatch(command_pattern, value)

    if match:
        return "mute", int(match.group(1))

    return None, None


class MuteFeature:
    """سکوت و رفع سکوت با API بله."""

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    @staticmethod
    def _target_id(message):
        """شناسه کاربری که پیامش را ریپلای کرده‌ایم."""
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    def _restrict(self, chat_id, user_id, can_send, until_date=None):
        """
        محدود یا آزاد کردن مجوز ارسال پیام.

        اگر until_date ارسال نشود، محدودیت زمانی ندارد.
        """
        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
            "can_send_messages": can_send,
            "can_send_media_messages": can_send,
            "can_send_other_messages": can_send,
            "can_add_web_page_previews": can_send,
        }

        if until_date is not None:
            payload["until_date"] = until_date

        try:
            result = self.api("restrictChatMember", payload)
        except Exception:
            logger.exception("Mute API request failed")
            result = None

        if not isinstance(result, dict) or result.get("ok") is not True:
            if isinstance(result, dict):
                error = result.get(
                    "description",
                    "API عملیات را نپذیرفت.",
                )
            else:
                error = "پاسخ معتبر از API بله دریافت نشد."

            logger.warning("restrictChatMember failed: %s", error)
            return False, error

        return True, None

    def handle_message(self, message):
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}

        # این قابلیت فقط در گروه و سوپرگروه اجرا می‌شود.
        if chat.get("type") not in {"group", "supergroup"}:
            return None

        text = message.get("text", "")

        if not isinstance(text, str):
            return None

        action, duration_minutes = parse_mute_command(text)

        if action is None:
            return None

        chat_id = chat.get("id")
        sender_id = (message.get("from") or {}).get("id")
        target_id = self._target_id(message)

        if chat_id is None:
            return {
                "ok": False,
                "text": "شناسه گروه پیدا نشد.",
            }

        if target_id is None:
            return {
                "ok": False,
                "text": (
                    "روی پیام کاربر موردنظر ریپلای کن "
                    "و بعد دستور را بفرست."
                ),
            }

        if (
            sender_id is not None
            and str(target_id) == str(sender_id)
        ):
            return {
                "ok": False,
                "text": "نمی‌توانی این دستور را روی خودت اجرا کنی.",
            }

        # ادمین واقعی یا ناظر ثبت‌شده همین گروه.
        try:
            if not self.is_authorized(message):
                return {
                    "ok": False,
                    "text": (
                        "فقط ادمین گروه یا ناظر همین گروه "
                        "می‌تواند سکوت را مدیریت کند."
                    ),
                }
        except Exception:
            logger.exception("Mute authorization check failed")
            return {
                "ok": False,
                "text": "بررسی مجوز ناموفق بود؛ دستور اجرا نشد.",
            }

        # سکوت
        if action == "mute":

            # سکوت مدت‌دار: حداقل ۱ و حداکثر ۶۰ دقیقه.
            if duration_minutes is not None:
                if duration_minutes < 1:
                    return {
                        "ok": False,
                        "text": "مدت سکوت باید حداقل ۱ دقیقه باشد.",
                    }

                if duration_minutes > 60:
                    return {
                        "ok": False,
                        "text": (
                            "بیشتر از ۶۰ دقیقه نمی‌شود؛ "
                            "می‌توانی از دستور «سکوت» "
                            "بدون مدت استفاده کنی."
                        ),
                    }

                until_date = (
                    int(time.time()) + duration_minutes * 60
                )

                success, error = self._restrict(
                    chat_id,
                    target_id,
                    False,
                    until_date,
                )

                if not success:
                    return {
                        "ok": False,
                        "text": (
                            "سکوت انجام نشد. بررسی کن ربات ادمین "
                            "گروه و دارای مجوز محدودکردن اعضا باشد. "
                            f"جزئیات: {error}"
                        ),
                    }

                return {
                    "ok": True,
                    "text": (
                        f"🔇 کاربر برای {duration_minutes} دقیقه "
                        "ساکت شد. بعد از پایان مدت، محدودیت "
                        "خودکار برداشته می‌شود."
                    ),
                }

            # سکوت دائمی تا زمانی که ادمین یا ناظر رفع کند.
            success, error = self._restrict(
                chat_id,
                target_id,
                False,
            )

            if not success:
                return {
                    "ok": False,
                    "text": (
                        "سکوت انجام نشد. بررسی کن ربات ادمین "
                        "گروه و دارای مجوز محدودکردن اعضا باشد. "
                        f"جزئیات: {error}"
                    ),
                }

            return {
                "ok": True,
                "text": (
                    "🔇 کاربر ساکت شد؛ تا وقتی ادمین یا ناظر "
                    "رفع سکوت نکند، امکان ارسال پیام ندارد."
                ),
            }

        # رفع سکوت: مجوزهای ارسال دوباره فعال می‌شوند.
        success, error = self._restrict(
            chat_id,
            target_id,
            True,
        )

        if not success:
            return {
                "ok": False,
                "text": (
                    "رفع سکوت انجام نشد. مجوزهای ربات را "
                    f"بررسی کن. جزئیات: {error}"
                ),
            }

        return {
            "ok": True,
            "text": (
                "🔊 سکوت کاربر برداشته شد؛ "
                "حالا می‌تواند دوباره پیام بفرستد."
            ),
      }
