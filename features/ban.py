
import logging
import re
import time
import unicodedata

logger = logging.getLogger("NevoBot.Ban")


# شکل‌های جایگزین دستورها
BAN_ALIASES = {
    "ban", "بن", "بان",
}

UNBAN_ALIASES = {
    "unban", "un ban", "انبن", "آن بن",
    "آن‌بن", "آنبان", "رفع بن", "رفع‌بن",
}

KICK_ALIASES = {
    "kick", "کیک", "اخراج",
}


PERSIAN_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_text(value):
    """یکسان‌سازی حروف و فاصله‌های دستور."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.translate(PERSIAN_DIGITS)
    value = value.replace("\u200c", " ")
    value = value.replace("\u200f", "")
    value = value.replace("\u200e", "")
    value = " ".join(value.lower().split())
    return value.strip()


def parse_duration(text):
    """
    تبدیل زمان فارسی یا انگلیسی به ثانیه.

    نمونه:
    1h، 30m، 1d
    ۱ ساعت، ۳۰ دقیقه، ۱ روز

    None یعنی زمان مشخص نشده است.
    """

    value = normalize_text(text)

    if not value:
        return None

    pattern = (
        r"^(\d+)\s*"
        r"(s|sec|second|ثانیه|m|min|minute|دقیقه|"
        r"h|hr|hour|ساعت|d|day|روز|"
        r"w|week|هفته)$"
    )

    match = re.fullmatch(pattern, value)

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "ثانیه": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "دقیقه": 60,
        "h": 3600,
        "hr": 3600,
        "hour": 3600,
        "ساعت": 3600,
        "d": 86400,
        "day": 86400,
        "روز": 86400,
        "w": 604800,
        "week": 604800,
        "هفته": 604800,
    }

    seconds = amount * multipliers[unit]

    # محدودیت ایمنی: بین ۳۰ ثانیه تا ۳۶۶ روز
    if not 30 <= seconds <= 366 * 86400:
        return None

    return seconds


class BanFeature:
    """
    عملیات بن، انبن و کیک.

    api:
        همان تابع api(method, payload) در main.py

    is_authorized:
        تابعی که مجازبودن دستوردهنده را بررسی می‌کند.
        باید True را فقط برای ادمین/مالک مجاز یا ویژه
        با دسترسی تأییدشده برگرداند.

    این کلاس تا زمانی که تابع مجوزدهی به آن داده نشده
    باشد، هیچ دستور مدیریتی را اجرا نمی‌کند.
    """

    def __init__(self, api, is_authorized=None):
        self.api = api
        self.is_authorized = is_authorized

    def _request(self, method, payload):
        try:
            result = self.api(method, payload)

            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "error": "پاسخ API معتبر نیست.",
                }

            if not result.get("ok"):
                logger.warning(
                    "Moderation API rejected method=%s",
                    method,
                )
                return {
                    "ok": False,
                    "error": result.get(
                        "description",
                        "عملیات ناموفق بود.",
                    ),
                }

            return result

        except Exception:
            logger.exception(
                "Moderation API request failed: %s",
                method,
            )
            return {
                "ok": False,
                "error": "خطای ارتباط با API.",
            }

    @staticmethod
    def _validate_ids(chat_id, user_id):
        if chat_id is None or user_id is None:
            raise ValueError("شناسه گروه و کاربر الزامی است.")

        if str(chat_id).strip() == "":
            raise ValueError("شناسه گروه نامعتبر است.")

        if str(user_id).strip() == "":
            raise ValueError("شناسه کاربر نامعتبر است.")

        if str(chat_id) == str(user_id):
            raise ValueError("شناسه گروه و کاربر یکسان است.")

    def ban(self, chat_id, user_id, duration_seconds=None):
        """
        بن دائمی یا زمان‌دار.

        مدت زمان به صورت until_date به API فرستاده می‌شود.
        پشتیبانی دقیق این پارامتر باید در بله تست شود.
        """
        self._validate_ids(chat_id, user_id)

        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
        }

        if duration_seconds is not None:
            try:
                duration_seconds = int(duration_seconds)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "مدت بن معتبر نیست.",
                }

            if not 30 <= duration_seconds <= 366 * 86400:
                return {
                    "ok": False,
                    "error": "مدت بن باید بین ۳۰ ثانیه و ۳۶۶ روز باشد.",
                }

            payload["until_date"] = int(time.time()) + duration_seconds

        return self._request("kickChatMember", payload)

    def unban(self, chat_id, user_id):
        """رفع مسدودیت عضو."""
        self._validate_ids(chat_id, user_id)

        return self._request(
            "unbanChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )

    def kick(self, chat_id, user_id):
        """
        اخراج بدون قصد مسدودسازی دائمی.

        پس از اخراج، تلاش می‌کنیم مسدودیت برداشته شود.
        نتیجه نهایی باید با رفتار واقعی API بله بررسی شود.
        """
        self._validate_ids(chat_id, user_id)

        kicked = self._request(
            "kickChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )

        if not kicked.get("ok"):
            return kicked

        unbanned = self.unban(chat_id, user_id)

        if not unbanned.get("ok"):
            return {
                "ok": False,
                "error": (
                    "اخراج انجام شد؛ اما رفع مسدودیت تأیید نشد."
                ),
            }

        return {
            "ok": True,
            "message": "عضو اخراج شد.",
        }

    @staticmethod
    def _get_target(message):
        """هدف دستور را از پیام ریپلای‌شده می‌گیرد."""
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}

        # ساختار پیام در API ممکن است متفاوت باشد.
        if user.get("id") is None:
            user = reply.get("from_user") or {}

        return user.get("id")

    @staticmethod
    def _parse_command(text):
        """
        خروجی:
        (action, duration_seconds)

        دستورها باید در قالب پیام مستقل باشند.
        """
        normalized = normalize_text(text)

        if not normalized:
            return None, None

        parts = normalized.split()
        first = parts[0].lstrip("/")

        # دستورهای انگلیسی
        if first in {"ban", "kick", "unban"}:
            action = {
                "ban": "ban",
                "kick": "kick",
                "unban": "unban",
            }[first]

            rest = " ".join(parts[1:])

            # برای دستورهای انگلیسی، نام کاربری ربات
            # بعد از @ در نسخه فعلی پشتیبانی نمی‌شود.
            if action == "ban" and rest:
                duration = parse_duration(rest)
                if duration is not None:
                    return action, duration

            if not rest:
                return action, None

            return None, None

        # دستورهای فارسی؛ عبارت‌های چندکلمه‌ای
        for alias in sorted(
            UNBAN_ALIASES,
            key=len,
            reverse=True,
        ):
            if normalized == alias:
                return "unban", None

        for alias in sorted(
            KICK_ALIASES,
            key=len,
            reverse=True,
        ):
            if normalized == alias:
                return "kick", None

        for alias in sorted(
            BAN_ALIASES,
            key=len,
            reverse=True,
        ):
            if normalized == alias:
                return "ban", None

            if normalized.startswith(alias + " "):
                duration_text = normalized[len(alias):].strip()
                duration = parse_duration(duration_text)

                if duration is not None:
                    return "ban", duration

        return None, None

    def handle_message(self, message):
        """
        پردازش دستور با ریپلای به پیام هدف.

        این متد را بعد از دریافت پیام در main.py فراخوانی کن.
        اگر دستور شناخته نشود، None برمی‌گرداند.
        """
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

        # بررسی مجوز باید fail-closed باشد.
        if self.is_authorized is None:
            return {
                "ok": False,
                "error": "بررسی دسترسی هنوز متصل نشده است.",
            }

        try:
            allowed = self.is_authorized(message)
        except Exception:
            logger.exception("Authorization check failed")
            allowed = False

        if not allowed:
            return {
                "ok": False,
                "error": "اجازه اجرای این دستور را نداری.",
            }

        chat_id = chat.get("id")
        user_id = self._get_target(message)

        if user_id is None:
            return {
                "ok": False,
                "error": "برای اجرای دستور، روی پیام عضو ریپلای کن.",
            }

        if str(user_id) == str((message.get("from") or {}).get("id")):
            return {
                "ok": False,
                "error": "نمی‌توانی این دستور را روی خودت اجرا کنی.",
            }

        if action == "ban":
            return self.ban(chat_id, user_id, duration)

        if action == "unban":
            return self.unban(chat_id, user_id)

        if action == "kick":
            return self.kick(chat_id, user_id)

        return None
