import logging
import re
import time
import unicodedata

logger = logging.getLogger("NevoBot.Mute")


def normalize_text(value):
    """یکسان‌سازی اعداد، فاصله‌ها و حروف فارسی و عربی."""
    value = unicodedata.normalize("NFKC", str(value or ""))

    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )

    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ")
    value = value.replace("\u200f", "")
    value = value.replace("\u200e", "")

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
        ("mute", None)       سکوت دائمی
        ("mute", 10)         سکوت ۱۰ دقیقه‌ای
        ("unmute", None)     رفع سکوت
        (None, None)         دستور نامرتبط
    """
    value = normalize_text(text)

    if value in {normalize_text(x) for x in UNMUTE_ALIASES}:
        return "unmute", None

    if value in {normalize_text(x) for x in MUTE_ALIASES}:
        return "mute", None

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
    """مدیریت سکوت و رفع سکوت با API بله."""

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    @staticmethod
    def _target_id(message):
        """استخراج شناسه کاربری که پیامش ریپلای شده است."""
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    def _restrict(self, chat_id, user_id, can_send, until_date=None):
        """
        اعمال یا برداشتن محدودیت ارسال.

        can_send=False:
            مسدود کردن ارسال پیام و رسانه.

        can_send=True:
            فعال کردن مجدد مجوزهای ارسال.
        """

        permissions = {
            "can_send_messages": can_send,
            "can_send_media_messages": can_send,
            "can_send_other_messages": can_send,
            "can_add_web_page_previews": can_send,
        }

        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
            "permissions": permissions,
            "use_independent_chat_permissions": True,
        }

        if until_date is not None:
            payload["until_date"] = until_date

        try:
            result = self.api("restrictChatMember", payload)

        except Exception:
            logger.exception("Mute API request failed")
            return False, "خطا هنگام اجرای درخواست محدودیت."

        if not isinstance(result, dict) or result.get("ok") is not True:
            if isinstance(result, dict):
                error = result.get(
                    "description",
                    "خطای نامشخص API",
                )
            else:
                error = "پاسخ معتبر از API دریافت نشد."

            logger.error(
                "restrictChatMember failed: %s | chat=%s | user=%s",
                error,
                chat_id,
                user_id,
            )

            return False, error

        if result.get("result") is False:
            return False, "API محدودیت را تأیید نکرد."

        logger.info(
            "Permissions updated | chat=%s | user=%s | can_send=%s",
            chat_id,
            user_id,
            can_send,
        )

        return True, None

    def handle_message(self, message):
        """پردازش دستور سکوت و رفع سکوت."""

        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}

        # سکوت فقط در گروه و سوپرگروه اجرا می‌شود.
        if chat.get("type") not in {"group", "supergroup"}:
            return None

        text = message.get("text", "")

        if not isinstance(text, str):
            return None

        action, duration_minutes = parse_mute_command(text)

        if action is None:
            return None

        chat_id = chat.get("id")

        sender = message.get("from") or {}
        sender_id = sender.get("id")

        target_id = self._target_id(message)

        if chat_id is None:
            return {
                "ok": False,
                "text": "❌ شناسه گروه پیدا نشد.",
            }

        # دستور باید روی پیام کاربر ریپلای شود.
        if target_id is None:
            return {
                "ok": False,
                "text": (
                    "👀 داداش، اول روی پیام کاربر موردنظر "
                    "ریپلای کن و بعد دستور سکوت رو بفرست."
                ),
            }

        # جلوگیری از اجرای دستور روی خود فرد.
        if (
            sender_id is not None
            and str(target_id) == str(sender_id)
        ):
            return {
                "ok": False,
                "text": "❌ نمی‌تونی دستور سکوت رو روی خودت اجرا کنی.",
            }

        # فقط ادمین واقعی یا ناظر همان گروه.
        try:
            authorized = self.is_authorized(message)

        except Exception:
            logger.exception("Mute authorization check failed")

            return {
                "ok": False,
                "text": "❌ بررسی مجوز ناموفق بود؛ دستور اجرا نشد.",
            }

        if not authorized:
            return {
                "ok": False,
                "text": (
                    "⛔ فقط ادمین گروه یا ناظر ثبت‌شده همین گروه "
                    "می‌تونه سکوت رو مدیریت کنه."
                ),
            }

        # =========================
        # سکوت
        # =========================
        if action == "mute":

            # سکوت زمان‌دار: ۱ تا ۶۰ دقیقه.
            if duration_minutes is not None:

                if duration_minutes < 1:
                    return {
                        "ok": False,
                        "text": "⚠️ مدت سکوت باید حداقل ۱ دقیقه باشه.",
                    }

                if duration_minutes > 60:
                    return {
                        "ok": False,
                        "text": (
                            "⚠️ حداکثر مدت سکوت ۶۰ دقیقه‌ست.\n"
                            "برای سکوت بدون محدودیت زمانی، "
                            "فقط دستور «سکوت» رو بفرست."
                        ),
                    }

                until_date = (
                    int(time.time()) + duration_minutes * 60
                )

                success, error = self._restrict(
                    chat_id=chat_id,
                    user_id=target_id,
                    can_send=False,
                    until_date=until_date,
                )

                if not success:
                    return {
                        "ok": False,
                        "text": (
                            "❌ سکوت انجام نشد!\n\n"
                            "بررسی کن ربات ادمین گروه باشه و "
                            "مجوز محدودکردن اعضا رو داشته باشه.\n\n"
                            f"خطای API: {error}"
                        ),
                    }

                return {
                    "ok": True,
                    "text": (
                        f"🔇 کاربر به مدت {duration_minutes} دقیقه "
                        "ساکت شد.\n"
                        "بعد از پایان زمان، محدودیت زمانی "
                        "خودکار منقضی می‌شه."
                    ),
                }

            # سکوت دائمی تا زمان اجرای دستور رفع سکوت.
            success, error = self._restrict(
                chat_id=chat_id,
                user_id=target_id,
                can_send=False,
            )

            if not success:
                return {
                    "ok": False,
                    "text": (
                        "❌ سکوت انجام نشد!\n\n"
                        "بررسی کن ربات ادمین گروه باشه و "
                        "مجوز محدودکردن اعضا رو داشته باشه.\n\n"
                        f"خطای API: {error}"
                    ),
                }

            return {
                "ok": True,
                "text": (
                    "🔇 کاربر ساکت شد!\n"
                    "برای فعال کردن دوباره امکان ارسال پیام، "
                    "روی پیامش ریپلای کن و «حذف سکوت» رو بفرست."
                ),
            }

        # =========================
        # رفع سکوت
        # =========================
        success, error = self._restrict(
            chat_id=chat_id,
            user_id=target_id,
            can_send=True,
        )

        if not success:
            return {
                "ok": False,
                "text": (
                    "❌ رفع سکوت انجام نشد!\n\n"
                    "مجوزهای ربات رو بررسی کن.\n"
                    f"خطای API: {error}"
                ),
            }

        return {
            "ok": True,
            "text": (
                "🔊 سکوت کاربر برداشته شد!\n"
                "مجوزهای ارسال پیام و رسانه دوباره فعال شدند."
            ),
                }
