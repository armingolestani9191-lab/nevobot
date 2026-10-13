# main.py

import logging
import time
import requests

from config import (
    API_BASE_URL,
    BOT_TOKEN,
    REQUEST_TIMEOUT,
    POLLING_TIMEOUT,
)

from database import (
    init_database,
    get_connection,
    save_user,
    save_group,
    record_group_message,
)

from keyboards import main_menu

from features.ban import BanFeature
from features.moderators import ModeratorsFeature
from features.mute import MuteFeature
from features.spam import SpamFeature

from features.whisper import (
    init_whisper,
    save_whisper,
    open_whisper,
    mark_whisper_opened,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("NevoBot")


class NevoBot:

    def __init__(self):
        if not BOT_TOKEN:
            raise RuntimeError(
                "توکن ربات در تنظیمات تنظیم نشده است."
            )

        self.base_url = f"{API_BASE_URL}/bot{BOT_TOKEN}"

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })

        self.offset = 0

        # مدیریت ناظرها
        self.moderators_feature = ModeratorsFeature(
            is_group_admin=self.is_group_admin,
        )

        # بن، رفع بن و اخراج
        self.ban_feature = BanFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # سکوت و رفع سکوت
        self.mute_feature = MuteFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # ضداسپم
        self.spam_feature = SpamFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

    # ==========================================
    #                  Bale API
    # ==========================================

    def api(self, method, payload=None):
        """ارسال درخواست به API بله."""

        url = f"{self.base_url}/{method}"

        try:
            response = self.session.post(
                url,
                json=payload or {},
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                logger.warning(
                    "Invalid API response: %s",
                    method,
                )
                return None

            if not data.get("ok"):
                logger.warning(
                    "API error (%s): %s",
                    method,
                    data.get("description", "Unknown error"),
                )

            return data

        except (requests.RequestException, ValueError):
            logger.exception(
                "Request failed: %s",
                method,
            )
            return None

    def send_message(self, chat_id, text, keyboard=None):
        """ارسال پیام به گروه یا گفت‌وگوی خصوصی."""

        payload = {
            "chat_id": chat_id,
            "text": str(text),
        }

        if keyboard is not None:
            payload["reply_markup"] = keyboard

        return self.api("sendMessage", payload)

    def delete_message(self, chat_id, message_id):
        """حذف پیام از گروه."""

        if chat_id is None or message_id is None:
            return False

        result = self.api(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )

        return bool(result and result.get("ok"))

    def answer_callback(self, callback_id, text, show_alert=True):
        """نمایش پنجره هشدار هنگام کلیک روی دکمه."""

        if not callback_id:
            return None

        return self.api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": str(text),
                "show_alert": show_alert,
            },
        )

    # ==========================================
    #                Whisper Feature
    # ==========================================

    def discard_whisper(self, whisper_id):
        """حذف نجوا در صورت شکست ارسال اعلان."""

        try:
            with get_connection() as connection:
                connection.execute(
                    "DELETE FROM whispers WHERE id = ?",
                    (int(whisper_id),),
                )
                connection.commit()

        except Exception:
            logger.exception(
                "Could not discard whisper %s",
                whisper_id,
            )

    @staticmethod
    def get_display_name(user):
        """ساخت اسم نمایشی کاربر."""

        first_name = str(
            user.get("first_name") or ""
        ).strip()

        last_name = str(
            user.get("last_name") or ""
        ).strip()

        full_name = f"{first_name} {last_name}".strip()

        if full_name:
            return full_name

        username = str(
            user.get("username") or ""
        ).strip()

        if username:
            return f"@{username}"

        return "کاربر"

    def handle_whisper_message(self, message):
        """
        استفاده:
        روی پیام شخص ریپلای کن و بنویس:
        نجوا متن پیام
        """

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        chat_type = chat.get("type")
        sender_id = sender.get("id")
        message_id = message.get("message_id")
        text = message.get("text", "")

        # نجوا فقط در گروه فعال است.
        if chat_type not in {"group", "supergroup"}:
            return False

        if not isinstance(text, str):
            return False

        text = text.strip()

        # شناسایی دستور نجوا
        if text != "نجوا" and not text.startswith("نجوا "):
            return False

        whisper_text = text[len("نجوا"):].strip()

        # دستور بدون متن
        if not whisper_text:
            self.send_message(
                chat_id,
                "💌 برای ارسال نجوا، روی پیام مخاطب ریپلای کن و بنویس:\n"
                "نجوا متن پیام",
            )
            return True

        # بررسی ریپلای
        replied_message = (
            message.get("reply_to_message") or {}
        )

        recipient = replied_message.get("from") or {}
        recipient_id = recipient.get("id")

        if recipient_id is None:
            self.send_message(
                chat_id,
                "❌ برای ارسال نجوا باید روی پیام شخص موردنظر ریپلای کنی.",
            )
            return True

        # جلوگیری از نجوا به خود
        if str(recipient_id) == str(sender_id):
            self.send_message(
                chat_id,
                "❌ نمی‌تونی برای خودت نجوا بفرستی!",
            )
            return True

        # پیام‌های خود ربات را هدف نجوا قرار نده.
        if recipient.get("is_bot"):
            self.send_message(
                chat_id,
                "❌ نمی‌تونی برای ربات نجوا بفرستی.",
            )
            return True

        sender_name = self.get_display_name(sender)

        # ذخیره متن و اسم فعلی فرستنده
        try:
            whisper_id = save_whisper(
                chat_id=chat_id,
                group_message_id=message_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                text=whisper_text,
                sender_name=sender_name,
            )

        except ValueError as error:
            self.send_message(
                chat_id,
                f"❌ {error}",
            )
            return True

        except Exception:
            logger.exception("Could not save whisper")

            self.send_message(
                chat_id,
                "❌ ذخیره نجوا ناموفق بود؛ دوباره تلاش کن.",
            )
            return True

        # حذف متن اصلی تا نجوا در گروه افشا نشود.
        deleted = self.delete_message(
            chat_id,
            message_id,
        )

        if not deleted:
            self.discard_whisper(whisper_id)

            self.send_message(
                chat_id,
                "❌ نتونستم پیام نجوا رو حذف کنم. "
                "مجوز حذف پیام‌ها رو بررسی کن.",
            )
            return True

        # دکمه بازکردن نجوا
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "💌 باز کردن",
                        "callback_data": (
                            f"whisper_open:{whisper_id}"
                        ),
                    }
                ]
            ]
        }

        # اعلان در خود گروه، نه پیام خصوصی
        result = self.send_message(
            chat_id,
            "💌 یه نجوا داری!",
            keyboard,
        )

        if not result or not result.get("ok"):
            self.discard_whisper(whisper_id)

            self.send_message(
                chat_id,
                "❌ ارسال اعلان نجوا ناموفق بود.",
            )
            return True

        logger.info(
            "Whisper %s sent from %s to %s",
            whisper_id,
            sender_id,
            recipient_id,
        )

        return True

    def handle_callback_query(self, callback_query):
        """رسیدگی امن به کلیک روی دکمه نجوا."""

        if not isinstance(callback_query, dict):
            return

        callback_id = callback_query.get("id")
        callback_data = str(
            callback_query.get("data", "")
        )

        user = callback_query.get("from") or {}
        user_id = user.get("id")

        if not callback_id or user_id is None:
            return

        if not callback_data.startswith("whisper_open:"):
            return

        # بررسی شناسه
        try:
            whisper_id = int(
                callback_data.split(":", 1)[1]
            )
        except (ValueError, IndexError):
            self.answer_callback(
                callback_id,
                "این دکمه معتبر نیست.",
                show_alert=True,
            )
            return

        # فقط مخاطب اصلی می‌تواند متن را دریافت کند.
        # این بررسی از دیتابیس انجام می‌شود، نه از اطلاعات دکمه.
        whisper = open_whisper(
            whisper_id,
            user_id,
        )

        if not whisper:
            self.answer_callback(
                callback_id,
                "این نجوا مال تو نیست و نمی‌تونی بخونیش!",
                show_alert=True,
            )
            return

        whisper_text = str(
            whisper.get("text") or ""
        )

        sender_name = str(
            whisper.get("sender_name") or "کاربر"
        )

        # متن پنجره برای دریافت‌کننده اصلی
        alert_text = (
            f"نجوا از طرف {sender_name}\n\n"
            f"{whisper_text}"
        )

        mark_whisper_opened(
            whisper_id,
            user_id,
        )

        result = self.answer_callback(
            callback_id,
            alert_text,
            show_alert=True,
        )

        # اگر API نمایش پنجره را نپذیرفت، متن فقط به
        # گفت‌وگوی خصوصی دریافت‌کننده ارسال می‌شود.
        # متن هرگز برای کاربر غیرمجاز ارسال نمی‌شود.
        if not result or not result.get("ok"):
            logger.warning(
                "Could not display whisper popup for user %s",
                user_id,
            )

            self.send_message(
                user_id,
                alert_text,
            )

    # ==========================================
    #          Administrator Checking
    # ==========================================

    def is_group_admin(self, message):
        """بررسی دسترسی مدیریت گروه از طریق API."""

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")

        if chat.get("type") not in {"group", "supergroup"}:
            return False

        if chat_id is None or user_id is None:
            return False

        result = self.api(
            "getChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )

        if not result or not result.get("ok"):
            logger.warning(
                "Could not verify group administrator"
            )
            return False

        member = result.get("result") or {}
        status = str(
            member.get("status", "")
        ).lower()

        return status in {
            "creator",
            "administrator",
            "owner",
            "admin",
        }

    def is_moderation_authorized(self, message):
        """اجازه مدیریت برای ادمین واقعی یا ناظر ثبت‌شده."""

        if self.is_group_admin(message):
            return True

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")

        if chat.get("type") not in {"group", "supergroup"}:
            return False

        return self.moderators_feature.is_moderator(
            chat_id,
            user_id,
        )

    # ==========================================
    #                 Moderation
    # ==========================================

    def handle_moderation(self, message):
        """پردازش ناظرها، سکوت، ضداسپم و بن."""

        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return False

        # ۱. مدیریت ناظرها
        moderator_result = (
            self.moderators_feature.handle_message(message)
        )

        if moderator_result is not None:
            self.send_message(
                chat_id,
                moderator_result.get(
                    "text",
                    "عملیات ناظرها انجام نشد.",
                ),
            )
            return True

        # ۲. سکوت و رفع سکوت
        mute_result = self.mute_feature.handle_message(message)

        if mute_result is not None:
            self.send_message(
                chat_id,
                mute_result.get(
                    "text",
                    "عملیات سکوت انجام نشد.",
                ),
            )
            return True

        # ۳. ضداسپم
        spam_result = self.spam_feature.handle_message(message)

        if spam_result is not None:
            if "text" in spam_result:
                self.send_message(
                    chat_id,
                    spam_result["text"],
                )

            return True

        # ۴. بن، رفع بن و اخراج
        result = self.ban_feature.handle_message(message)

        if result is None:
            return False

        if not result.get("ok"):
            self.send_message(
                chat_id,
                "❌ " + result.get(
                    "error",
                    "اجرای دستور ناموفق بود.",
                ),
            )
            return True

        action, duration = self.ban_feature._parse_command(
            message.get("text", "")
        )

        if action == "ban":
            response_text = "✅ بن کاربر انجام شد."
        elif action == "unban":
            response_text = "✅ رفع بن کاربر انجام شد."
        elif action == "kick":
            response_text = "✅ درخواست اخراج انجام شد."
        else:
            response_text = "✅ عملیات انجام شد."

        self.send_message(
            chat_id,
            response_text,
        )

        return True

    # ==========================================
    #               Message Handling
    # ==========================================

    def handle_message(self, message):
        """پردازش پیام دریافتی."""

        if not isinstance(message, dict):
            return

        chat = message.get("chat") or {}
        user = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = user.get("id")

        if chat_id is None or user_id is None:
            return

        first_name = user.get("first_name", "")
        username = user.get("username", "")
        chat_type = chat.get("type", "")
        text = message.get("text", "")

        # ثبت اطلاعات پایه کاربر
        save_user(
            user_id,
            username,
            first_name,
        )

        if chat_type in {"group", "supergroup"}:
            save_group(
                chat_id,
                chat.get("title", ""),
            )

            if text:
                record_group
