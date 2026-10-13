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
        normalized = " ".join(text.split())

        unmute_commands = (
            "حذف سکوت",
            "سکوت حذف",
            "رفع سکوت",
            "لغو سکوت",
            "آزاد",
            "unmute",
        )

        if normalized in unmute_commands:
            return "unmute", None, None

        mute_commands = (
            "سکوت",
            "ساکت",
            "سکوت کن",
            "ساکت کن",
            "mute",
        )

        if normalized in mute_commands:
            return "mute", None, None

        for command in sorted(mute_commands, key=len, reverse=True):
            if normalized.startswith(command + " "):
                remainder = normalized[len(command):].strip()

                duration = self._parse_duration(remainder)

                if duration is None:
                    return (
                        "mute",
                        None,
                        "مدت واردشده نامعتبر است. مثال: سکوت ۵ دقیقه",
                    )

                return "mute", duration, None

        return None, None, None

    # تبدیل مدت به ثانیه؛ حداکثر یک ساعت
    def _parse_duration(self, text):
        text = text.strip()

        match = re.fullmatch(
            r"(\d+)\s*"
            r"(ثانیه|ثانیه‌ای|دقیقه|دقیقه‌ای|ساعت|ساعتی|"
            r"second|seconds|minute|minutes|hour|hours)",
            text,
            re.IGNORECASE,
        )

        if not match:
            return None

        try:
            amount = int(match.group(1))
        except (TypeError, ValueError):
            return None

        unit = match.group(2).lower()

        if amount <= 0:
            return None

        if unit.startswith("ثانیه") or unit.startswith("second"):
            seconds = amount
        elif unit.startswith("دقیقه") or unit.startswith("minute"):
            seconds = amount * 60
        else:
            seconds = amount * 3600

        if seconds > 3600:
            return None

        return seconds

    # پیدا کردن فردی که روی پیامش ریپلای شده
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

    # ارسال درخواست محدودسازی به API
    def _api_restrict(self, chat_id, user_id, muted, until_date=None):
        allowed = not muted

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
            payload["until_date"] = until_date

        try:
            result = self.api("restrictChatMember", payload)

            if not isinstance(result, dict):
                return False, "پاسخ نامعتبر از API دریافت شد."

            if result.get("ok") is not True:
                error = result.get(
                    "description",
                    "API درخواست را تأیید نکرد.",
                )
                return False, str(error)

            return True, None

        except Exception as exc:
            logger.exception("Mute API request failed")
            return False, str(exc)

    # تابع اصلی؛ نام آن باید با main.py هماهنگ باشد
    def handle_message(self, message):
        if not isinstance(message, dict):
            return None

        text = message.get("text", "")

        if not isinstance(text, str) or not text.strip():
            return None

        action, duration, duration_error = self._is_mute_command(text)

        # پیام عادی گروه را به‌عنوان دستور مدیریت نشناس
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
            return {
                "text": "❌ شناسه گروه دریافت نشد."
            }

        target_id = self._get_target(message)

        if target_id is None:
            return {
                "text": (
                    "⚠️ برای استفاده از این دستور، روی پیام "
                    "فرد موردنظر ریپلای کن."
                )
            }

        sender = message.get("from") or {}

        if sender.get("id") is None:
            return {
                "text": "❌ اطلاعات اجراکننده دستور دریافت نشد."
            }

        # اعتبارسنجی با همان ساختاری که main.py انتظار دارد
        try:
            authorized = self.is_authorized(message)
        except Exception:
            logger.exception("Mute authorization check failed")
            return {
                "text": "❌ هنگام بررسی دسترسی خطایی رخ داد."
            }

        if not authorized:
            return {
                "text": "⛔ اجازه اجرای این دستور را نداری."
            }

        # مدت نامعتبر نباید به سکوت دائمی تبدیل شود
        if duration_error:
            return {"text": f"❌ {duration_error}"}

        # جلوگیری از سکوت‌کردن خود ربات در صورت مشخص بودن شناسه
        # شناسه ربات در این کلاس در دسترس نیست؛ کنترل اصلی با API است.

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
                logger.error("Mute failed: %s", error)
                return {
                    "text": (
                        "❌ سکوت اعمال نشد.\n"
                        f"دلیل API: {error}"
                    )
                }

            if duration is not None:
                if duration < 60:
                    duration_text = f"{duration} ثانیه"
                elif duration % 3600 == 0:
                    duration_text = f"{duration // 3600} ساعت"
                else:
                    duration_text = f"{duration // 60} دقیقه"

                return {
                    "text": (
                        "🔇 درخواست سکوت با موفقیت تأیید شد.\n"
                        f"مدت: {duration_text}"
                    )
                }

            return {
                "text": "🔇 درخواست سکوت دائمی ثبت شد."
            }

        if action == "unmute":
            success, error = self._api_restrict(
                chat_id=chat_id,
                user_id=target_id,
                muted=False,
            )

            if not success:
                logger.error("Unmute failed: %s", error)
                return {
                    "text": (
                        "❌ رفع سکوت انجام نشد.\n"
                        f"دلیل API: {error}"
                    )
                }

            return {
                "text": "🔊 درخواست رفع سکوت با موفقیت تأیید شد."
            }

        return None
