
import logging
import re
import time
import unicodedata

logger = logging.getLogger("NevoBot.Ban")


def normalize_text(value):
    """یکسان‌سازی اعداد، فاصله‌ها و حروف دستور."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))
    value = value.replace("\u200c", " ")
    value = value.replace("\u200f", "").replace("\u200e", "")
    return " ".join(value.lower().split()).strip()


UNBAN_ALIASES = {
    "unban",
    "un ban",
    "انبن",
    "آنبن",
    "آن بن",
    "ان بن",
    "آنبان",
    "رفع بن",
    "رفع بان",
    "رفع مسدودی",
}

BAN_ALIASES = {"ban", "بن", "بان"}
KICK_ALIASES = {"kick", "کیک", "اخراج"}


def parse_duration(text):
    """تبدیل مدت‌های شناخته‌شده به ثانیه؛ بدون اجرای بن."""
    value = normalize_text(text)

    match = re.fullmatch(
        r"(\d+)\s*(s|sec|second|ثانیه|m|min|minute|دقیقه|"
        r"h|hr|hour|ساعت|d|day|روز|w|week|هفته)",
        value,
    )
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    factors = {
        "s": 1, "sec": 1, "second": 1, "ثانیه": 1,
        "m": 60, "min": 60, "minute": 60, "دقیقه": 60,
        "h": 3600, "hr": 3600, "hour": 3600, "ساعت": 3600,
        "d": 86400, "day": 86400, "روز": 86400,
        "w": 604800, "week": 604800, "هفته": 604800,
    }

    seconds = amount * factors[unit]
    if not 30 <= seconds <= 366 * 86400:
        return None

    return seconds


class BanFeature:
    """
    قابلیت مدیریت اعضا:
    - بن
    - انبن
    - کیک
    - تشخیص شکل‌های مختلف دستور

    api: تابع api(method, payload) از main.py
    is_authorized: تابع بررسی مجوز پیام‌دهنده
    """

    def __init__(self, api, is_authorized=None):
        self.api = api
        self.is_authorized = is_authorized

    @staticmethod
    def _validate_ids(chat_id, user_id):
        if chat_id is None or user_id is None:
            raise ValueError("شناسه گروه و کاربر الزامی است.")

        if not str(chat_id).strip() or not str(user_id).strip():
            raise ValueError("شناسه گروه یا کاربر نامعتبر است.")

    def _request(self, method, payload):
        """درخواست API و بازگرداندن نتیجه واقعی آن."""
        try:
            result = self.api(method, payload)

            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "error": "پاسخ معتبر از API دریافت نشد.",
                }

            if result.get("ok") is not True:
                logger.warning(
                    "API rejected moderation method: %s",
                    method,
                )
                return {
                    "ok": False,
                    "error": result.get(
                        "description",
                        "API عملیات را نپذیرفت.",
                    ),
                }

            return result

        except Exception:
            logger.exception("Moderation request failed: %s", method)
            return {
                "ok": False,
                "error": "خطا هنگام ارتباط با API بله.",
            }

    def ban(self, chat_id, user_id, duration_seconds=None):
        """بن دائمی؛ بن زمان‌دار تا تأیید API فعال نیست."""
        self._validate_ids(chat_id, user_id)

        if duration_seconds is not None:
            return {
                "ok": False,
                "error": (
                    "بن زمان‌دار هنوز فعال نیست؛ "
                    "پشتیبانی پارامتر زمان پایان باید تأیید شود."
                ),
            }

        return self._request(
            "banChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )

    def unban(self, chat_id, user_id):
        """رفع مسدودیت؛ فقط اگر عضو بن شده باشد."""
        self._validate_ids(chat_id, user_id)

        return self._request(
            "unbanChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "only_if_banned": True,
            },
        )

    def kick(self, chat_id, user_id):
        """
        اخراج قابل بازگشت:
        بن و سپس رفع بن. اگر مرحله دوم شکست بخورد،
        صریحاً اعلام می‌شود که وضعیت نهایی نامشخص است.
        """
        self._validate_ids(chat_id, user_id)

        banned = self.ban(chat_id, user_id)
        if not banned.get("ok"):
            return banned

        unbanned = self.unban(chat_id, user_id)
        if not unbanned.get("ok"):
            return {
                "ok": False,
                "error": (
                    "عضو مسدود شد، اما رفع مسدودیت تأیید نشد؛ "
                    "وضعیت عضو را در گروه بررسی کن."
                ),
            }

        return {
            "ok": True,
            "message": "درخواست اخراج و رفع مسدودیت موفق بود.",
        }

    @staticmethod
    def _get_target(message):
        """هدف دستور فقط از پیام ریپلای‌شده گرفته می‌شود."""
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    @staticmethod
    def _parse_command(text):
        """
        نتیجه:
        (action, duration_seconds)

        duration_seconds فقط تشخیص داده می‌شود؛
        پشتیبانی واقعی بن زمان‌دار در ban() بررسی می‌شود.
        """
        normalized = normalize_text(text)
        if not normalized:
            return None, None

        # ابتدا عبارت‌های چندکلمه‌ای انبن
        for alias in sorted(UNBAN_ALIASES, key=len, reverse=True):
            if normalized == normalize_text(alias):
                return "unban", None

        parts = normalized.split()
        first = parts[0]

        # پشتیبانی از /ban و /ban@nevobot
        if first.startswith("/"):
            first = first[1:].split("@", 1)[0]

        rest = " ".join(parts[1:])

        if first in {"unban"} and not rest:
            return "unban", None

        if first in {"kick"} and not rest:
            return "kick", None

        if first in {"ban"}:
            if not rest:
                return "ban", None

            duration = parse_duration(rest)
            if duration is not None:
                return "ban", duration

            return None, None

        # دستورهای فارسی
        if normalized in {normalize_text(x) for x in KICK_ALIASES}:
            return "kick", None

        if normalized in {normalize_text(x) for x in BAN_ALIASES}:
            return "ban", None

        for alias in BAN_ALIASES:
            normalized_alias = normalize_text(alias)
            prefix = normalized_alias + " "

            if normalized.startswith(prefix):
                duration = parse_duration(
                    normalized[len(prefix):].strip()
                )
                if duration is not None:
                    return "ban", duration

        return None, None

    def handle_message(self, message):
        """پردازش دستور گروه؛ با بررسی مجوز قبل از اجرا."""
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        if chat.get("type") not in {"group", "supergroup"}:
            return None

        text = message.get("text", "")
        if not isinstance(text, str):
            return None

        action, duration = self._parse_command(text)
        if action is None:
            return None

        chat_id = chat.get("id")
        target_id = self._get_target(message)
        sender_id = (message.get("from") or {}).get("id")

        # قبل از تماس API، ورودی‌های ضروری بررسی می‌شوند.
        if chat_id is None:
            return {"ok": False, "error": "شناسه گروه موجود نیست."}

        if target_id is None:
            return {
                "ok": False,
                "error": "برای اجرای دستور روی پیام عضو ریپلای کن.",
            }

        if sender_id is not None and str(target_id) == str(sender_id):
            return {
                "ok": False,
                "error": "نمی‌توانی این دستور را روی خودت اجرا کنی.",
            }

        # نبودن تابع بررسی دسترسی به معنی رد دستور است.
        if self.is_authorized is None:
            return {
                "ok": False,
                "error": "بررسی دسترسی هنوز تنظیم نشده است.",
            }

        try:
            if not self.is_authorized(message):
                return {
                    "ok": False,
                    "error": "اجازه اجرای این دستور را نداری.",
                }
        except Exception:
            logger.exception("Authorization check failed")
            return {
                "ok": False,
                "error": "بررسی مجوز ناموفق بود؛ دستور اجرا نشد.",
            }

        try:
            if action == "ban":
                return self.ban(chat_id, target_id, duration)

            if action == "unban":
                return self.unban(chat_id, target_id)

            if action == "kick":
                return self.kick(chat_id, target_id)

        except ValueError as error:
            return {"ok": False, "error": str(error)}

        return None
