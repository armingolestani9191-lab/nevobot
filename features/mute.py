import logging
import re
import time
import unicodedata

logger = logging.getLogger("NevoBot.Mute")


def normalize_text(value):
    value = unicodedata.normalize("NFKC", str(value or ""))

    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )

    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ")
    value = value.replace("\u200f", "").replace("\u200e", "")

    return " ".join(value.lower().split()).strip()


def parse_mute_command(text):
    value = normalize_text(text)

    unmute_commands = {
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

    mute_commands = {
        "سکوت",
        "ساکت",
        "سکوت کن",
        "ساکت کن",
        "mute",
    }

    if value in unmute_commands:
        return "unmute", None, None

    if value in mute_commands:
        return "mute", None, None

    for command in sorted(mute_commands, key=len, reverse=True):
        if value.startswith(command + " "):
            remainder = value[len(command):].strip()
            remainder = re.sub(r"^(?:برای|به مدت)\s+", "", remainder)

            match = re.fullmatch(
                r"(\d+)\s*"
                r"(ثانیه|ثانیه‌ای|ثانیه ای|"
                r"دقیقه|دقیقه‌ای|دقیقه ای|"
                r"دیقه|دیقه‌ای|دیقه ای|"
                r"ساعت|ساعتی|"
                r"seconds?|secs?|s|"
                r"minutes?|mins?|m|"
                r"hours?|hrs?|h)",
                remainder,
                re.IGNORECASE,
            )

            if not match:
                return (
                    "mute",
                    None,
                    "مدت نامعتبر است. مثال: سکوت ۵ دقیقه",
                )

            amount = int(match.group(1))
            unit = match.group(2).lower().replace(" ", "")

            if amount <= 0:
                return "mute", None, "مدت باید بیشتر از صفر باشد."

            if (
                unit.startswith("ثانیه")
                or unit.startswith("second")
                or unit.startswith("sec")
                or unit == "s"
            ):
                seconds = amount

            elif (
                unit.startswith("دقیقه")
                or unit.startswith("دیقه")
                or unit.startswith("minute")
                or unit.startswith("min")
                or unit == "m"
            ):
                seconds = amount * 60

            else:
                seconds = amount * 3600

            if seconds < 30 or seconds > 3600:
                return (
                    "mute",
                    None,
                    "مدت سکوت باید بین ۳۰ ثانیه تا ۶۰ دقیقه باشد.",
                )

            return "mute", seconds, None

    return None, None, None


class MuteFeature:
    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    def _get_target(self, message):
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    def _api_restrict(self, chat_id, user_id, muted, until_date=None):
        allowed = not muted

        # همان ساختار مجوزهای سکوت معمولی
        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
            "permissions": {
                "can_send_messages": allowed,
                "can_send_media_messages": allowed,
                "can_send_other_messages": allowed,
                "can_add_web_page_previews": allowed,
            },
            "use_independent_chat_permissions": True,
        }

        if until_date is not None:
            payload["until_date"] = int(until_date)

        try:
            result = self.api("restrictChatMember", payload)

            if not isinstance(result, dict):
                return False, "پاسخ معتبر از API دریافت نشد."

            if result.get("ok") is not True:
                error = result.get(
                    "description",
                    "خطای نامشخص API",
                )

                logger.error(
                    "restrictChatMember failed: %s | chat=%s | user=%s",
                    error,
                    chat_id,
                    user_id,
                )

                return False, str(error)

            logger.info(
                "Restriction updated | chat=%s | user=%s | muted=%s",
                chat_id,
                user_id,
                muted,
            )

            return True, None

        except Exception as exc:
            logger.exception("Mute API request failed")
            return False, str(exc)

    def handle_message(self, message):
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}

        if chat.get("type") not in {"group", "supergroup"}:
            return None

        text = message.get("text", "")

        if not isinstance(text, str):
            return None

        action, duration, duration_error = parse_mute_command(text)

        if action is None:
            return None

        if duration_error:
            return {"text": f"⚠️ {duration_error}"}

        chat_id = chat.get("id")
        sender = message.get("from") or {}
        sender_id = sender.get("id")
        target_id = self._get_target(message)

        if chat_id is None:
            return {"text": "❌ شناسه گروه پیدا نشد."}

        if target_id is None:
            return {
                "text": (
                    "⚠️ روی پیام کاربر موردنظر ریپلای کن "
                    "و بعد دستور رو بفرست."
                )
            }

        if sender_id is not None and str(sender_id) == str(target_id):
            return {"text": "❌ نمی‌تونی روی خودت اجراش کنی."}

        try:
            if not self.is_authorized(message):
                return {
                    "text": "⛔ فقط ادمین گروه یا ناظر مجاز می‌تونه این دستور رو اجرا کنه."
                }
        except Exception:
            logger.exception("Mute authorization check failed")
            return {"text": "❌ بررسی دسترسی ناموفق بود."}

        if action == "mute":
            until_date = None

            if duration is not None:
                until_date = int(time.time()) + duration

            success, error = self._api_restrict(
                chat_id=chat_id,
                user_id=target_id,
                muted=True,
                until_date=until_date,
            )

            if not success:
                return {
                    "text": (
                        "❌ سکوت انجام نشد!\n"
                        f"خطای API: {error}\n\n"
                        "مجوز محدودکردن اعضا برای ربات رو بررسی کن."
                    )
                }

            if duration is None:
                return {"text": "🔇 کاربر ساکت شد."}

            return {
                "text": (
                    f"🔇 کاربر برای {duration} ثانیه ساکت شد.\n"
                    "⏱ بعد از پایان زمان، محدودیت خودکار منقضی می‌شه."
                )
            }

        success, error = self._api_restrict(
            chat_id=chat_id,
            user_id=target_id,
            muted=False,
        )

        if not success:
            return {
                "text": f"❌ رفع سکوت انجام نشد!\nخطای API: {error}"
            }

        return {"text": "🔊 سکوت کاربر برداشته شد."}

    def handle(self, message):
        return self.handle_message(message)
