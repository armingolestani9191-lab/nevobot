import logging
import re
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
    """تبدیل مدت به ثانیه؛ به‌تنهایی هیچ عملیاتی اجرا نمی‌کند."""
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
    مدیریت بن، آنبن و اخراج.

    api:
        تابع api(method, payload) در main.py

    is_authorized:
        تابعی که ادمین واقعی یا ناظر مجاز را تأیید می‌کند.
    """

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    @staticmethod
    def _validate_ids(chat_id, user_id):
        if chat_id is None or user_id is None:
            raise ValueError("شناسه گروه و کاربر الزامی است.")

        if not str(chat_id).strip() or not str(user_id).strip():
            raise ValueError("شناسه گروه یا کاربر معتبر نیست.")

    def _request(self, method, payload):
        """ارسال درخواست و بررسی پاسخ واقعی API."""
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

            return {"ok": True}

        except Exception:
            logger.exception("Moderation API request failed: %s", method)
            return {
                "ok": False,
                "error": "هنگام ارتباط با API بله خطا رخ داد.",
            }

    def ban(self, chat_id, user_id, duration_seconds=None):
        """بن دائمی؛ مدت‌دار فعلاً اجرا نمی‌شود."""
        self._validate_ids(chat_id, user_id)

        if duration_seconds is not None:
            return {
                "ok": False,
                "error": (
                    "بن زمان‌دار فعلاً فعال نیست. "
                    "در حال حاضر از دستور بن بدون مدت استفاده کن."
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
        """رفع بن کاربر."""
        self._validate_ids(chat_id, user_id)

        # پارامتر اختیاری تأییدنشده نمی‌فرستیم.
        return self._request(
            "unbanChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )

    def kick(self, chat_id, user_id):
        """
        اخراج قابل بازگشت:
        ابتدا بن و سپس رفع بن انجام می‌شود.
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
                    "بن انجام شد، اما رفع بن تأیید نشد. "
                    "وضعیت عضو را در گروه بررسی کن."
                ),
            }

        return {"ok": True}

    @staticmethod
    def _get_target(message):
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    @staticmethod
    def _parse_command(text):
        normalized = normalize_text(text)

        if not normalized:
            return None, None

        # دستورهای فارسی چندکلمه‌ای
        if normalized in {
            normalize_text(alias) for alias in UNBAN_ALIASES
        }:
            return "unban", None

        if normalized in {
            normalize_text(alias) for alias in KICK_ALIASES
        }:
            return "kick", None

        if normalized in {
            normalize_text(alias) for alias in BAN_ALIASES
        }:
            return "ban", None

        # دستورهای انگلیسی و دستورهای دارای نام ربات
        parts = normalized.split()
        first = parts[0]

        if first.startswith("/"):
            first = first[1:].split("@", 1)[0]

        rest = " ".join(parts[1:])

        if first == "unban" and not rest:
            return "unban", None

        if first == "kick" and not rest:
            return "kick", None

        if first == "ban":
            if not rest:
                return "ban", None

            duration = parse_duration(rest)

            if duration is not None:
                return "ban", duration

            return None, None

        # بن زمان‌دار فارسی؛ شناسایی می‌شود ولی اجرا نخواهد شد.
        for alias in BAN_ALIASES:
            normalized_alias = normalize_text(alias)

            if normalized.startswith(normalized_alias + " "):
                rest = normalized[len(normalized_alias):].strip()
                duration = parse_duration(rest)

                if duration is not None:
                    return "ban", duration

        return None, None

    def handle_message(self, message):
        """تشخیص و اجرای دستورهای مدیریت اعضا."""
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

        if chat_id is None:
            return {
                "ok": False,
                "error": "شناسه گروه موجود نیست.",
            }

        if target_id is None:
            return {
                "ok": False,
                "error": (
                    "روی پیام کاربر موردنظر ریپلای کن "
                    "و بعد دستور را بفرست."
                ),
            }

        if sender_id is not None and str(target_id) == str(sender_id):
            return {
                "ok": False,
                "error": "نمی‌توانی این دستور را روی خودت اجرا کنی.",
            }

        # اگر بررسی دسترسی خطا بدهد، عملیات اجرا نمی‌شود.
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
            return {
                "ok": False,
                "error": str(error),
            }

        return None
