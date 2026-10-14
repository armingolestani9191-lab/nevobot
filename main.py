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
    save_user,
    save_group,
    record_group_message,
)

from keyboards import main_menu

from features.ban import BanFeature
from features.locks import LocksFeature
from features.moderators import ModeratorsFeature
from features.mute import MuteFeature
from features.spam import SpamFeature
from features.whisper import WhisperFeature
from features.profanity import ProfanityFeature


# ==========================================
#                 Logging
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("NevoBot")


# ==========================================
#                  NevoBot
# ==========================================

class NevoBot:

    def __init__(self):
        if not BOT_TOKEN:
            raise RuntimeError(
                "توکن ربات در config.py تنظیم نشده است."
            )

        self.base_url = f"{API_BASE_URL}/bot{BOT_TOKEN}"

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })

        self.offset = 0

        self.moderators_feature = ModeratorsFeature(
            is_group_admin=self.is_group_admin,
        )

        self.ban_feature = BanFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        self.mute_feature = MuteFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        self.spam_feature = SpamFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # اتصال قابلیت قفل رسانه‌ها
        self.locks_feature = LocksFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # اتصال قابلیت نجوا
        self.whisper_feature = WhisperFeature(
            api=self.api,
            send_message=self.send_message,
            delete_message=self.delete_message,
            answer_callback=self.answer_callback,
        )

        # اتصال قابلیت ضد فحش
        self.profanity_feature = ProfanityFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

    # ==========================================
    #                  Bale API
    # ==========================================

    def api(self, method, payload=None):
        """ارسال درخواست به API بله و ثبت خطا."""

        url = f"{self.base_url}/{method}"

        try:
            response = self.session.post(
                url,
                json=payload or {},
                timeout=REQUEST_TIMEOUT,
            )

            try:
                data = response.json()
            except ValueError:
                data = None

            if response.status_code >= 400:
                logger.error(
                    "Bale API HTTP error | method=%s | status=%s | "
                    "response=%s",
                    method,
                    response.status_code,
                    (
                        response.text[:1500]
                        if response.text
                        else "(empty response)"
                    ),
                )

                if isinstance(data, dict):
                    return data

                return {
                    "ok": False,
                    "description": (
                        f"HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    ),
                }

            if not isinstance(data, dict):
                logger.error(
                    "Invalid API response | method=%s | response=%s",
                    method,
                    response.text[:1000],
                )

                return {
                    "ok": False,
                    "description": "پاسخ API معتبر نیست.",
                }

            if not data.get("ok"):
                logger.warning(
                    "Bale API returned an error | method=%s | "
                    "description=%s",
                    method,
                    data.get("description", "Unknown error"),
                )

            return data

        except requests.Timeout:
            logger.error(
                "Bale API timeout | method=%s",
                method,
            )

            return {
                "ok": False,
                "description": "درخواست به علت اتمام زمان پاسخ ناموفق بود.",
            }

        except requests.RequestException as error:
            logger.error(
                "Bale API request exception | method=%s | error=%s",
                method,
                str(error),
            )

            return {
                "ok": False,
                "description": str(error),
            }

        except Exception:
            logger.exception(
                "Unexpected API error | method=%s",
                method,
            )
            return None

    def send_message(
        self,
        chat_id,
        text,
        keyboard=None,
    ):
        """ارسال پیام به گروه یا گفت‌وگوی خصوصی."""

        payload = {
            "chat_id": chat_id,
            "text": str(text),
        }

        if keyboard is not None:
            payload["reply_markup"] = keyboard

        return self.api(
            "sendMessage",
            payload,
        )

    def delete_message(
        self,
        chat_id,
        message_id,
    ):
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

        return bool(
            result and result.get("ok")
        )

    def answer_callback(
        self,
        callback_id,
        text,
        show_alert=True,
    ):
        """پاسخ به کلیک دکمه."""

        if not callback_id:
            logger.warning(
                "Cannot answer callback: callback ID is missing."
            )

            return {
                "ok": False,
                "description": "callback_query_id خالی است.",
            }

        callback_text = str(text or "")[:190]

        payload = {
            "callback_query_id": callback_id,
            "text": callback_text,
            "show_alert": bool(show_alert),
        }

        result = self.api(
            "answerCallbackQuery",
            payload,
        )

        if result and result.get("ok"):
            return result

        description = (
            result.get("description", "Unknown error")
            if isinstance(result, dict)
            else "No API response"
        )

        logger.warning(
            "answerCallbackQuery failed | callback_id=%s | "
            "description=%s",
            callback_id,
            description,
        )

        # تلاش دوم بدون پارامتر اختیاری show_alert
        fallback_result = self.api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": callback_text,
            },
        )

        if fallback_result and fallback_result.get("ok"):
            logger.info(
                "Callback answered using fallback | callback_id=%s",
                callback_id,
            )
            return fallback_result

        fallback_description = (
            fallback_result.get("description", "Unknown error")
            if isinstance(fallback_result, dict)
            else "No API response"
        )

        logger.error(
            "Callback could not be answered | callback_id=%s | "
            "first_error=%s | fallback_error=%s",
            callback_id,
            description,
            fallback_description,
        )

        return fallback_result

    # ==========================================
    #           Administrator Checking
    # ==========================================

    def is_group_admin(self, message):
        """بررسی ادمین واقعی گروه از طریق API."""

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")

        if chat.get("type") not in {
            "group",
            "supergroup",
        }:
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

        if (
            not result
            or not result.get("ok")
        ):
            logger.warning(
                "Could not verify group administrator | response=%s",
                (
                    result.get("description")
                    if isinstance(result, dict)
                    else "No response"
                ),
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
        """اجازه مدیریت برای ادمین یا ناظر ثبت‌شده."""

        if self.is_group_admin(message):
            return True

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")

        if chat.get("type") not in {
            "group",
            "supergroup",
        }:
            return False

        return self.moderators_feature.is_moderator(
            chat_id,
            user_id,
        )

    # ==========================================
    #                  Moderation
    # ==========================================

    def handle_moderation(self, message):
        """پردازش ناظرها، سکوت، قفل‌ها، ضداسپم و بن."""

        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return False

        # مدیریت ناظرها
        moderator_result = (
            self.moderators_feature.handle_message(
                message
            )
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

        # قابلیت سکوت
        mute_result = (
            self.mute_feature.handle_message(
                message
            )
        )

        if mute_result is not None:
            self.send_message(
                chat_id,
                mute_result.get(
                    "text",
                    "عملیات سکوت انجام نشد.",
                ),
            )
            return True

        # قابلیت قفل رسانه‌ها
        locks_result = (
            self.locks_feature.handle_message(
                message
            )
        )

        if locks_result is not None:
            if "text" in locks_result:
                self.send_message(
                    chat_id,
                    locks_result["text"],
                )

            return True

        # قابلیت ضد فحش
        profanity_result = (
            self.profanity_feature.handle_message(
                message
            )
        )

        if profanity_result is not None:
            if "text" in profanity_result:
                self.send_message(
                    chat_id,
                    profanity_result["text"],
                )

            return True

        # قابلیت ضداسپم
        spam_result = (
            self.spam_feature.handle_message(
                message
            )
        )

        if spam_result is not None:
            if "text" in spam_result:
                self.send_message(
                    chat_id,
                    spam_result["text"],
                )

            return True

        # قابلیت بن، آنبن و اخراج
        result = self.ban_feature.handle_message(
            message
        )

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

        action, duration = (
            self.ban_feature._parse_command(
                message.get("text", "")
            )
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
    #                Message Handling
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
        try:
            save_user(
                user_id,
                username,
                first_name,
            )
        except Exception:
            logger.exception(
                "Could not save user | user_id=%s",
                user_id,
            )

        # پیام‌های گروه
        if chat_type in {
            "group",
            "supergroup",
        }:
            try:
                save_group(
                    chat_id,
                    chat.get("title", ""),
                )

                if text:
                    record_group_message(
                        chat_id,
                        user_id,
                    )

            except Exception:
                logger.exception(
                    "Could not update group statistics | chat_id=%s",
                    chat_id,
                )

            # فقط اتصال به قابلیت نجوا
            if self.whisper_feature.handle_message(message):
                return

            # فرمان‌های مدیریتی، قفل‌ها، ضد فحش و ضداسپم
            if self.handle_moderation(message):
                return

        if not isinstance(text, str):
            return

        command = (
            text.split(maxsplit=1)[0].lower()
            if text
            else ""
        )

        # شروع ربات
        if (
            chat_type == "private"
            and command in {
                "/start",
                "/start@nevobot",
            }
        ):
            self.send_message(
                chat_id,
                (
                    f"سلام {first_name or 'رفیق'}! 👋\n\n"
                    "به NevoBot خوش اومدی.\n"
                    "مدیریت گروه‌ها با امکانات قابل تنظیم.\n\n"
                    "برای شروع از منوی زیر استفاده کن."
                ),
                main_menu(),
            )

        # راهنما
        elif (
            chat_type == "private"
            and text == "📚 راهنما"
        ):
            self.send_message(
                chat_id,
                (
                    "📚 راهنمای NevoBot\n\n"
                    "ربات را به گروه اضافه کن و مجوزهای "
                    "موردنیاز را به آن بده.\n"
                    "برای استفاده از سکوت، بن و مدیریت اعضا، "
                    "ربات باید مجوزهای لازم را داشته باشد."
                ),
                main_menu(),
            )

        # مدیریت گروه‌ها
        elif (
            chat_type == "private"
            and text == "📊 مدیریت گروه‌ها"
        ):
            self.send_message(
                chat_id,
                "📊 بخش مدیریت گروه‌ها در حال توسعه است.",
                main_menu(),
            )

        # تنظیمات
        elif (
            chat_type == "private"
            and text == "⚙️ تنظیمات"
        ):
            self.send_message(
                chat_id,
                "⚙️ تنظیمات NevoBot در حال توسعه است.",
                main_menu(),
            )

        # دستیار هوشمند
        elif (
            chat_type == "private"
            and text == "🤖 دستیار هوشمند"
        ):
            self.send_message(
                chat_id,
                "🤖 این بخش هنوز فعال نشده است.",
                main_menu(),
            )

    # ==========================================
    #                    Main Loop
    # ==========================================

    def run(self):
        """راه‌اندازی دیتابیس و اجرای حلقه ربات."""

        logger.info("Initializing database...")

        init_database()

        # راه‌اندازی دیتابیس اختصاصی قفل رسانه‌ها
        self.locks_feature.init_database()

        # راه‌اندازی دیتابیس اختصاصی نجوا
        self.whisper_feature.init_database()

        # راه‌اندازی دیتابیس اختصاصی ضد فحش
        self.profanity_feature.init_database()

        logger.info("NevoBot is starting...")

        info = self.api("getMe")

        if (
            not info
            or not info.get("ok")
        ):
            description = (
                info.get("description", "Unknown error")
                if isinstance(info, dict)
                else "No response"
            )

            raise RuntimeError(
                "اتصال به API بله ناموفق بود؛ "
                f"توکن و اتصال شبکه را بررسی کن. علت: {description}"
            )

        bot_info = info.get("result") or {}

        logger.info(
            "Bot connected: @%s",
            bot_info.get("username", "unknown"),
        )

        logger.info("Polling started.")

        while True:
            updates = self.api(
                "getUpdates",
                {
                    "offset": self.offset,
                    "timeout": POLLING_TIMEOUT,
                    "allowed_updates": [
                        "message",
                        "callback_query",
                    ],
                },
            )

            if (
                not updates
                or not updates.get("ok")
            ):
                logger.warning(
                    "getUpdates failed; retrying | response=%s",
                    (
                        updates.get("description")
                        if isinstance(updates, dict)
                        else "No response"
                    ),
                )
                time.sleep(2)
                continue

            for update in updates.get("result", []):
                update_id = update.get("update_id")

                # جلوگیری از پردازش دوباره آپدیت
                if isinstance(update_id, int):
                    self.offset = max(
                        self.offset,
                        update_id + 1,
                    )

                try:
                    if "callback_query" in update:

                        # اتصال کلیک دکمه به قابلیت نجوا
                        self.whisper_feature.handle_callback_query(
                            update["callback_query"]
                        )

                    elif "message" in update:
                        self.handle_message(
                            update["message"]
                        )

                except Exception:
                    logger.exception(
                        "Failed to process update | update_id=%s",
                        update_id,
                    )


# ==========================================
#                 Program Entry
# ==========================================

if __name__ == "__main__":
    try:
        NevoBot().run()

    except KeyboardInterrupt:
        logger.info("NevoBot stopped manually.")

    except Exception:
        logger.exception(
            "NevoBot failed to start or stopped unexpectedly."
        )
        raise
