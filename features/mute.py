
import logging
import re
import time

logger = logging.getLogger("NevoBot.Mute")


class MuteFeature:
    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    # تبدیل اعداد فارسی و عربی به انگلیسی
    def _normalize_digits(self, text):
        translation = str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
        return text.translate(translation)

    # تشخیص دستور سکوت و رفع سکوت
    def _is_mute_command(self, text):
        text = self._normalize_digits(text.strip().lower())
        text = " ".join(text.split())

        unmute_commands = (
            "حذف سکوت",
            "سکوت حذف",
            "رفع سکوت",
            "لغو سکوت",
            "آزاد",
            "unmute",
        )

        if text in unmute_commands:
            return "unmute", None, None

        mute_commands = (
            "سکوت",
            "ساکت",
            "سکوت کن",
            "ساکت کن",
            "mute",
        )

        if text in mute_commands:
            return "mute", None, None

        for command in sorted(mute_commands, key=len, reverse=True):
            if text.startswith(command + " "):
                remainder = text[len(command):].strip()
                duration = self._parse_duration(remainder)

                if duration is None:
                    return (
                        "mute",
                        None,
                        "مدت نامعتبر است. مثال: سکوت ۵ دقیقه",
                    )

                return "mute", duration, None

        return None, None, None

    # مدت سکوت؛ حداکثر یک ساعت
    def _parse_duration(self, text):
        match = re.fullmatch(
            r"(\d+)\s*"
            r"(ثانیه|ثانیه‌ای|دقیقه|دقیقه‌ای|ساعت|ساعتی|"
            r"second|seconds|minute|minutes|hour|hours)",
            text.strip(),
            re.IGNORECASE,
        )

        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        if amount <= 0:
            return None

        if unit.startswith("ثانیه") or unit.startswith("second"):
            seconds = amount
        elif unit.startswith("دقیقه") or unit.startswith("minute"):
            seconds = amount * 60
        else:
            seconds = amount * 3600

        # API ممکن است محدودیت کمتر از ۳۰ ثانیه را دائمی تلقی کند.
        if seconds < 30 or seconds > 3600:
            return None

        return seconds

    # استخراج کاربری که روی پیامش ریپلای شده
    def _get_target(self, message):
        reply = message.get("reply_to_message")

        if not isinstance(reply, dict):
            return None

        user = reply.get("from")

        if not isinstance(user, dict):
            return None

        user_id = user.get("id")

        if user_id is None:
            return None

        return user_id

    # اعمال سکوت یا رفع سکوت از طریق API بله
    def _api_restrict(self, chat_id, user_id, muted, until_date=None):
        allowed = not muted

        # پارامترهای مجوز به‌صورت مستقیم ارسال می‌شوند.
        # از ساختار تو‌در‌توی permissions استفاده نمی‌کنیم.
        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
            "can_send_messages": allowed,
            "can_send_media_messages": allowed,
            "can_send_other_messages": allowed,
            "can_add_web_page_previews": allowed,
        }

        if until_date is not None:
            payload["until_date"] = int(until_date)

        try:
            result = self.api("restrictChatMember", payload)

            if not isinstance(result, dict):
                return False, (
                    "پاسخ معتبر از API دریافت نشد؛ "
                    "خطای درخواست را در لاگ ربات بررسی کن."
                )

            if result.get("ok") is not True:
                error = result.get(
                    "description",
                    result.get("error", "درخواست توسط API تأیید نشد."),
                )

                logger.error(
                    "restrictChatMember failed | chat=%s user=%s "
                    "muted=%s error=%s",
                    chat_id,
                    user_id,
                    muted,
                    error,
                )

                return False, str(error)

            logger.info(
                "%s successful | chat=%s user=%s",
                "Mute" if muted else "Unmute",
                chat_id,
                user_id,
            )

            return True, None

        except Exception as exc:
            logger.exception(
                "Restriction request crashed | chat=%s user=%s",
                chat_id,
                user_id,
            )
            return False, str(exc)

    # تابع اصلی؛ سازگار با main.py فعلی
    def handle_message(self, message):
        if not isinstance(message, dict):
            return None

        text = message.get("text", "")

        if not isinstance(text, str) or not text.strip():
            return None

        action, duration, duration_error = self._is_mute_command(text)

        # پیام عادی را به‌عنوان دستور مدیریت دریافت نکن
        if action is None:
            return None

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")

        if chat_type not in ("group", "supergroup"):
            return {
                "text": "❌ این دستور فقط داخل گروه قابل استفاده است."
            }

        if chat_id is None:
            return {"text": "❌ شناسه گروه دریافت نشد."}

        if duration_error:
            return {"text": f"❌ {duration_error}"}

        target_id = self._get_target(message)

        if target_id is None:
            return {
                "text": (
                    "⚠️ روی پیام فرد موردنظر ریپلای کن، "
                    "بعد دستور سکوت یا رفع سکوت را بفرست."
                )
            }

        sender = message.get("from") or {}

        if sender.get("id") is None:
            return {"text": "❌ اطلاعات اجراکننده دستور دریافت نشد."}

        if str(sender["id"]) == str(target_id):
            return {"text": "⚠️ نمی‌توانی روی پیام خودت این دستور را اجرا کنی."}

        # بررسی دسترسی با همان تابعی که main.py به کلاس می‌دهد
        try:
            authorized = self.is_authorized(message)
        except Exception:
            logger.exception("Mute authorization check failed")
            return {"text": "❌ بررسی دسترسی ناموفق بود؛ لاگ را بررسی کن."}

        if not authorized:
            return {"text": "⛔ اجازه اجرای این دستور را نداری."}

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
                        "❌ سکوت اعمال نشد.\n"
                        f"دلیل API: {error}"
                    )
                }

            if duration is None:
                return {"text": "🔇 سکوت دائمی اعمال شد."}

            if duration % 3600 == 0:
                duration_text = f"{duration // 3600} ساعت"
            elif duration % 60 == 0:
                duration_text = f"{duration // 60} دقیقه"
            else:
                duration_text = f"{duration} ثانیه"

            return {
                "text": (
                    "🔇 سکوت با موفقیت اعمال شد.\n"
                    f"⏱ مدت: {duration_text}"
                )
            }

        if action == "unmute":
            success, error = self._api_restrict(
                chat_id=chat_id,
                user_id=target_id,
                muted=False,
            )

            if not success:
                return {
                    "text": (
                        "❌ رفع سکوت انجام نشد.\n"
                        f"دلیل API: {error}"
                    )
                }

            return {"text": "🔊 درخواست رفع سکوت موفق بود."}

        return None
