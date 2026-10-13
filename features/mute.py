import logging
import re
import time
import unicodedata

logger = logging.getLogger("NevoBot.Mute")


def normalize_text(value):
    """یکسان‌سازی متن و اعداد فارسی برای تشخیص دستورها."""
    value = unicodedata.normalize("NFKC", str(value or ""))

    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )

    value = (
        value.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .replace("\u200f", "")
        .replace("\u200e", "")
    )

    return " ".join(value.lower().split()).strip()


def parse_mute_command(text):
    """تشخیص دستور سکوت، مدت و رفع سکوت."""
    value = normalize_text(text)

    unmute_commands = {
        "حذف سکوت",
        "سکوت حذف",
        "رفع سکوت",
        "لغو سکوت",
        "برداشتن سکوت",
        "سکوت بردار",
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
        if not value.startswith(command + " "):
            continue

        remainder = value[len(command):].strip()

        remainder = re.sub(
            r"^(?:برای|به مدت)\s+",
            "",
            remainder,
        )

        match = re.fullmatch(
            r"(\d+)\s*"
            r"(ثانیه|ثانیه‌ای|"
            r"دقیقه|دقیقه‌ای|دیقه|دیقه‌ای|"
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
        unit = match.group(2).lower()

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
    """
    سکوت و رفع سکوت کاربران با API بله.

    api:
        تابع NevoBot.api در main.py

    is_authorized:
        بررسی مجوز ادمین واقعی یا ناظر گروه
    """

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    @staticmethod
    def _get_target(message):
        """دریافت شناسه کاربری که به پیامش ریپلای شده."""
        reply = message.get("reply_to_message") or {}
        user = reply.get("from") or {}
        return user.get("id")

    @staticmethod
    def _get_permissions(member):
        """خواندن مجوزها از پاسخ API، با پشتیبانی از دو قالب."""
        permissions = member.get("permissions")

        if isinstance(permissions, dict):
            return permissions

        return member

    def _get_member(self, chat_id, user_id):
        """دریافت وضعیت فعلی عضو از API."""
        try:
            response = self.api(
                "getChatMember",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                },
            )
        except Exception:
            logger.exception("getChatMember request failed")
            return None, "ارتباط با API هنگام بررسی عضو ناموفق بود."

        if not isinstance(response, dict):
            return None, "پاسخ معتبر از API دریافت نشد."

        if response.get("ok") is not True:
            error = response.get(
                "description",
                "بررسی وضعیت عضو ناموفق بود.",
            )
            return None, str(error)

        member = response.get("result")

        if not isinstance(member, dict):
            return None, "اطلاعات عضو در پاسخ API موجود نیست."

        return member, None

    def _api_restrict(
        self,
        chat_id,
        user_id,
        muted,
        until_date=None,
    ):
        """
        اعمال محدودیت و سپس بررسی نتیجه واقعی.

        موفقیت درخواست به‌تنهایی برای تأیید سکوت کافی نیست.
        """
        allowed = not muted

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
            response = self.api(
                "restrictChatMember",
                payload,
            )
        except Exception as exc:
            logger.exception("restrictChatMember request failed")
            return False, f"خطای درخواست API: {exc}"

        logger.info(
            "restrictChatMember response | chat=%s | "
            "user=%s | muted=%s | response=%r",
            chat_id,
            user_id,
            muted,
            response,
        )

        if not isinstance(response, dict):
            return False, "پاسخ معتبر از API دریافت نشد."

        if response.get("ok") is not True:
            error = response.get(
                "description",
                "API درخواست محدودسازی را نپذیرفت.",
            )

            logger.error(
                "Restriction request rejected | chat=%s | "
                "user=%s | error=%s",
                chat_id,
                user_id,
                error,
            )

            return False, str(error)

        if response.get("result") is not True:
            return (
                False,
                "API موفقیت عملیات محدودسازی را تأیید نکرد.",
            )

        # موفقیت اولیه ثبت شد؛ اکنون وضعیت واقعی عضو را بررسی می‌کنیم.
        member, error = self._get_member(chat_id, user_id)

        if member is None:
            logger.error(
                "Restriction verification unavailable | "
                "chat=%s | user=%s | error=%s",
                chat_id,
                user_id,
                error,
            )

            return (
                False,
                "درخواست پذیرفته شد، اما وضعیت محدودیت قابل "
                f"تأیید نیست: {error}",
            )

        status = str(member.get("status", "")).lower()
        permissions = self._get_permissions(member)
        can_send = permissions.get("can_send_messages")

        logger.info(
            "Member restriction verification | chat=%s | "
            "user=%s | status=%s | can_send_messages=%r",
            chat_id,
            user_id,
            status,
            can_send,
        )

        if muted:
            # برای تأیید سکوت، هم وضعیت محدودشده و هم
            # ممنوع‌بودن ارسال پیام باید در پاسخ دیده شود.
            if status == "restricted" and can_send is False:
                logger.info(
                    "Mute verified | chat=%s | user=%s",
                    chat_id,
                    user_id,
                )
                return True, None

            logger.error(
                "Mute could not be verified | chat=%s | "
                "user=%s | status=%s | can_send_messages=%r",
                chat_id,
                user_id,
                status,
                can_send,
            )

            return (
                False,
                "API درخواست را پذیرفته، اما پاسخ بررسی عضو "
                "محدودیت ارسال پیام را تأیید نکرد. نوع گروه، "
                "مجوز ربات و وضعیت عضو را بررسی کن.",
            )

        # برای رفع سکوت، عضو نباید همچنان محدود باشد.
        if status == "restricted" and can_send is not True:
            logger.error(
                "Unmute could not be verified | chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return (
                False,
                "رفع سکوت تأیید نشد؛ کاربر ممکن است هنوز محدود باشد.",
            )

        logger.info(
            "Unmute verified | chat=%s | user=%s",
            chat_id,
            user_id,
        )

        return True, None

    def handle_message(self, message):
        """پردازش دستورهای سکوت و رفع سکوت."""
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        chat_type = str(chat.get("type", "")).lower()

        if chat_type not in {"group", "supergroup"}:
            return None

        text = message.get("text", "")

        if not isinstance(text, str):
            return None

        action, duration, parse_error = parse_mute_command(text)

        if action is None:
            return None

        if parse_error:
            return {"text": f"⚠️ {parse_error}"}

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

        if (
            sender_id is not None
            and str(sender_id) == str(target_id)
        ):
            return {
                "text": "❌ نمی‌تونی این دستور رو روی خودت اجرا کنی."
            }

        try:
            authorized = self.is_authorized(message)
        except Exception:
            logger.exception("Mute authorization check failed")
            return {
                "text": "❌ بررسی دسترسی ناموفق بود؛ دستور اجرا نشد."
            }

        if not authorized:
            return {
                "text": (
                    "⛔ فقط ادمین گروه یا ناظر مجاز "
                    "می‌تونه این دستور رو اجرا کنه."
                )
            }

        # طبق مستندات API، restrictChatMember مخصوص سوپرگروه است.
        if chat_type != "supergroup":
            logger.warning(
                "Mute unsupported for chat type | "
                "chat=%s | type=%s",
                chat_id,
                chat_type,
            )

            return {
                "text": (
                    "❌ نوع این گروه برای محدودسازی با "
                    "restrictChatMember پشتیبانی نمی‌شه. "
                    "باید گروه از نوع سوپرگروه باشه."
                )
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
                return {
                    "text": (
                        "❌ سکوت تأیید نشد.\n"
                        f"جزئیات: {error}\n\n"
                        "بررسی کن ربات ادمین سوپرگروه باشه "
                        "و مجوز محدودکردن اعضا رو داشته باشه."
                    )
                }

            if duration is None:
                return {
                    "text": (
                        "🔇 سکوت اعمال شد و وضعیت محدودیت "
                        "از API تأیید شد."
                    )
                }

            return {
                "text": (
                    f"🔇 سکوت {duration} ثانیه‌ای اعمال شد "
                    "و وضعیت محدودیت از API تأیید شد."
                )
            }

        success, error = self._api_restrict(
            chat_id=chat_id,
            user_id=target_id,
            muted=False,
        )

        if not success:
            return {
                "text": f"❌ رفع سکوت تأیید نشد.\nجزئیات: {error}"
            }

        return {
            "text": "🔊 رفع سکوت انجام شد و وضعیت عضو بررسی شد."
        }

    def handle(self, message):
        """سازگاری با فراخوانی‌های قدیمی."""
        return self.handle_message(message)
