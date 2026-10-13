import logging
import re
import time

logger = logging.getLogger("NevoBot.Mute")


class MuteFeature:
    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

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
                    "دلیل خطا مشخص نیست."
                )
                return False, error

            return True, None

        except Exception as exc:
            logger.exception("Mute API request failed")
            return False, str(exc)

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

    def _parse_duration(self, text):
        text = text.strip()

        match = re.search(
            r"(\d+)\s*(ثانیه|ثانیه‌ای|دقیقه|دقیقه‌ای|"
            r"ساعت|ساعتی|second|seconds|minute|minutes|"
            r"hour|hours)",
            text,
            re.IGNORECASE,
        )

        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        if unit.startswith("ثانیه") or unit.startswith("second"):
            seconds = amount
        elif unit.startswith("دقیقه") or unit.startswith("minute"):
            seconds = amount * 60
        else:
            seconds = amount * 3600

        # حداکثر زمان سکوت: یک ساعت
        if seconds <= 0 or seconds > 3600:
            return None

        return seconds

    def _is_mute_command(self, text):
        normalized = " ".join(text.strip().lower().split())

        mute_commands = (
            "سکوت",
            "ساکت",
            "سکوت کن",
            "ساکت کن",
            "mute",
        )

        unmute_commands = (
            "حذف سکوت",
            "سکوت حذف",
            "رفع سکوت",
            "لغو سکوت",
            "آزاد",
            "unmute",
        )

        if normalized in unmute_commands:
            return "unmute", None

        if normalized in mute_commands:
            return "mute", None

        for command in ("سکوت", "ساکت", "mute"):
            if normalized.startswith(command + " "):
                duration = self._parse_duration(
                    normalized[len(command):]
                )
                return "mute", duration

        return None, None

    def handle(self, message):
        if not isinstance(message, dict):
            return {"handled": False}

        text = message.get("text", "")

        if not isinstance(text, str) or not text.strip():
            return {"handled": False}

        action, duration = self._is_mute_command(text)

        if action is None:
            return {"handled": False}

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")

        if chat_type not in ("group", "supergroup"):
            return {
                "handled": True,
                "text": "❌ این دستور فقط داخل گروه قابل استفاده است.",
            }

        if chat_id is None:
            return {
                "handled": True,
                "text": "❌ اطلاعات گروه دریافت نشد.",
            }

        target_id = self._get_target(message)

        if target_id is None:
            return {
                "handled": True,
                "text": (
                    "⚠️ برای استفاده از این دستور، "
                    "روی پیام فرد موردنظر ریپلای کن."
                ),
            }

        sender = message.get("from") or {}
        sender_id = sender.get("id")

        if sender_id is None:
            return {
                "handled": True,
                "text": "❌ اطلاعات اجراکننده دستور دریافت نشد.",
            }

        try:
            authorized = self.is_authorized(chat_id, sender_id)
        except Exception:
            logger.exception("Authorization check failed")
            return {
                "handled": True,
                "text": "❌ بررسی دسترسی انجام نشد.",
            }

        if not authorized:
            return {
                "handled": True,
                "text": "⛔ اجازه اجرای این دستور را نداری.",
            }

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
                    "handled": True,
                    "text": (
                        "❌ سکوت اعمال نشد.\n"
                        f"دلیل API: {error}"
                    ),
                }

            if duration is not None:
                return {
                    "handled": True,
                    "text": (
                        "🔇 درخواست سکوت با موفقیت ثبت شد.\n"
                        f"مدت: {duration // 60 or 1} دقیقه"
                    ),
                }

            return {
                "handled": True,
                "text": "🔇 درخواست سکوت با موفقیت ثبت شد.",
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
                    "handled": True,
                    "text": (
                        "❌ رفع سکوت انجام نشد.\n"
                        f"دلیل API: {error}"
                    ),
                }

            return {
                "handled": True,
                "text": "🔊 درخواست رفع سکوت با موفقیت ثبت شد.",
            }

        return {"handled": False}
