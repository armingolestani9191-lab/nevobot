# ==========================================
#              NevoBot | Main
# ==========================================

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
from features.spam import SpamFeature, normalize_text
from features.whisper import WhisperFeature
from features.profanity import ProfanityFeature
from features.warnings import WarningsFeature
from features.panel import PanelFeature

# قابلیت جدید گیف‌ساز
from features.gif_maker import GifMakerFeature


# ==========================================
#                  Logging
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

        self.base_url = (
            f"{API_BASE_URL}/bot{BOT_TOKEN}"
        )

        self.session = requests.Session()

        self.session.headers.update({
            "Content-Type": "application/json",
        })

        self.offset = 0

        # --------------------------------------
        #             Moderators
        # --------------------------------------

        self.moderators_feature = ModeratorsFeature(
            is_group_admin=self.is_group_admin,
        )

        # --------------------------------------
        #                 Ban
        # --------------------------------------

        self.ban_feature = BanFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # --------------------------------------
        #                 Mute
        # --------------------------------------

        self.mute_feature = MuteFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # --------------------------------------
        #                 Spam
        # --------------------------------------

        self.spam_feature = SpamFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # --------------------------------------
        #                 Locks
        # --------------------------------------

        self.locks_feature = LocksFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # --------------------------------------
        #                Whisper
        # --------------------------------------

        self.whisper_feature = WhisperFeature(
            api=self.api,
            send_message=self.send_message,
            delete_message=self.delete_message,
            answer_callback=self.answer_callback,
        )

        # --------------------------------------
        #                Profanity
        # --------------------------------------

        self.profanity_feature = ProfanityFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

        # --------------------------------------
        #                Warnings
        # --------------------------------------

        self.warnings_feature = WarningsFeature(
            api=self.api,
            send_message=self.send_message,
            ban_feature=self.ban_feature,
            mute_feature=self.mute_feature,
        )

        # --------------------------------------
        #             Management Panel
        # --------------------------------------

        self.panel_feature = PanelFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
            spam_feature=self.spam_feature,
            profanity_feature=self.profanity_feature,
            warnings_feature=self.warnings_feature,
        )

        # --------------------------------------
        #               GIF Maker
        # --------------------------------------

        self.gif_maker_feature = GifMakerFeature(
            token=BOT_TOKEN,
            api_base_url=API_BASE_URL,
            request_timeout=REQUEST_TIMEOUT,
            send_message=self.send_message,
        )

        logger.info(
            "All NevoBot features initialized."
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

            try:
                data = response.json()

            except ValueError:
                data = None

            if response.status_code >= 400:
                logger.error(
                    "Bale API HTTP error | "
                    "method=%s | status=%s | response=%s",
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
                    "Invalid API response | "
                    "method=%s | response=%s",
                    method,
                    response.text[:1000],
                )

                return {
                    "ok": False,
                    "description": "پاسخ API معتبر نیست.",
                }

            if not data.get("ok"):
                logger.warning(
                    "Bale API error | method=%s | description=%s",
                    method,
                    data.get(
                        "description",
                        "Unknown error",
                    ),
                )

            return data

        except requests.Timeout:
            logger.error(
                "Bale API timeout | method=%s",
                method,
            )

            return {
                "ok": False,
                "description": (
                    "درخواست به علت اتمام زمان پاسخ ناموفق بود."
                ),
            }

        except requests.RequestException as error:
            logger.error(
                "Bale API request exception | "
                "method=%s | error=%s",
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

    # ==========================================
    #               Send Message
    # ==========================================

    def send_message(
        self,
        chat_id,
        text,
        keyboard=None,
    ):
        """ارسال پیام به گروه یا گفت‌وگوی خصوصی."""

        if chat_id is None:
            return {
                "ok": False,
                "description": "شناسه گفتگو مشخص نیست.",
            }

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

    # ==========================================
    #               Edit Message
    # ==========================================

    def edit_message(
        self,
        chat_id,
        message_id,
        text,
        keyboard=None,
    ):
        """ویرایش پیام موجود."""

        if chat_id is None or message_id is None:
            return {
                "ok": False,
                "description": "شناسه پیام معتبر نیست.",
            }

        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": str(text),
            "reply_markup": (
                keyboard
                if keyboard is not None
                else {"inline_keyboard": []}
            ),
        }

        return self.api(
            "editMessageText",
            payload,
        )

    # ==========================================
    #               Delete Message
    # ==========================================

    def delete_message(
        self,
        chat_id,
        message_id,
    ):
        """حذف پیام."""

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
            isinstance(result, dict)
            and result.get("ok") is True
        )

    # ==========================================
    #              Callback Answer
    # ==========================================

    def answer_callback(
        self,
        callback_id,
        text="",
        show_alert=True,
    ):
        """پاسخ به کلیک دکمه."""

        if not callback_id:
            return {
                "ok": False,
                "description": "callback_query_id خالی است.",
            }

        callback_text = str(text or "")[:190]

        result = self.api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": callback_text,
                "show_alert": bool(show_alert),
            },
        )

        if isinstance(result, dict) and result.get("ok"):
            return result

        logger.warning(
            "Callback answer failed | callback_id=%s",
            callback_id,
        )

        # تلاش دوباره بدون show_alert
        fallback = self.api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": callback_text,
            },
        )

        return fallback

    # ==========================================
    #           Administrator Checking
    # ==========================================

    def is_group_admin(self, message):
        """بررسی ادمین واقعی گروه از طریق API."""

        if not isinstance(message, dict):
            return False

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
            not isinstance(result, dict)
            or not result.get("ok")
        ):
            logger.warning(
                "Could not verify administrator | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
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

    # ==========================================
    #           Moderation Authorization
    # ==========================================

    def is_moderation_authorized(self, message):
        """
        اجازه مدیریت برای ادمین واقعی یا ناظر ثبت‌شده.

        اگر بررسی دسترسی ناموفق باشد،
        دسترسی مدیریتی داده نمی‌شود.
        """

        if not isinstance(message, dict):
            return False

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

        if chat_id is None or user_id is None:
            return False

        try:
            return bool(
                self.moderators_feature.is_moderator(
                    chat_id,
                    user_id,
                )
            )

        except Exception:
            logger.exception(
                "Moderator verification failed."
            )

            return False

    # ==========================================
    #              Warning Helpers
    # ==========================================

    def _record_confirmed_violation(
        self,
        message,
        reason,
    ):
        """
        ثبت اخطار فقط برای تخلف تأییدشده.

        قفل رسانه‌ها از این تابع استفاده نمی‌کند.
        """

        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")

        if chat_id is None or user_id is None:
            return None

        # به ربات‌ها اخطار داده نمی‌شود.
        if sender.get("is_bot"):
            return None

        # ادمین و ناظر مجاز از سیستم اخطار معاف‌اند.
        if self.is_moderation_authorized(message):
            return None

        try:
            result = self.warnings_feature.record_violation(
                chat_id=chat_id,
                user_id=user_id,
                reason=reason,
            )

            logger.info(
                "Violation processed | chat=%s | "
                "user=%s | reason=%s | result=%s",
                chat_id,
                user_id,
                reason,
                result,
            )

            return result

        except Exception:
            logger.exception(
                "Could not record violation | "
                "chat=%s | user=%s | reason=%s",
                chat_id,
                user_id,
                reason,
            )

            return None

    # ==========================================
    #            Whisper Command Helpers
    # ==========================================

    @staticmethod
    def _is_whisper_command(message):
        """بررسی اینکه پیام، تلاش برای اجرای دستور نجواست."""

        text = (
            message.get("text")
            or message.get("caption")
            or ""
        )

        if not isinstance(text, str):
            return False

        normalized = normalize_text(text)

        return (
            normalized == "نجوا"
            or normalized.startswith("نجوا ")
        )

    def _handle_disabled_whisper(self, message):
        """
        وقتی نجوا خاموش است:
        پیام تلاش برای نجوا حذف و علت اعلام می‌شود.
        """

        if not self._is_whisper_command(message):
            return False

        chat = message.get("chat") or {}

        chat_id = chat.get("id")
        message_id = message.get("message_id")

        if chat_id is None or message_id is None:
            return True

        deleted = self.delete_message(
            chat_id,
            message_id,
        )

        if deleted:
            self.send_message(
                chat_id,
                (
                    "🔕 قابلیت نجوا در این گروه خاموش است.\n\n"
                    "برای فعال‌کردن آن، از ادمین گروه یا "
                    "ناظر مجاز بخواه تنظیم نجوا را روشن کند."
                ),
            )

        else:
            logger.warning(
                "Could not delete disabled whisper attempt | "
                "chat=%s | message=%s",
                chat_id,
                message_id,
            )

            self.send_message(
                chat_id,
                (
                    "🔕 قابلیت نجوا در این گروه خاموش است.\n"
                    "برای فعال‌کردن آن، از ادمین گروه یا "
                    "ناظر مجاز بخواه تنظیم نجوا را روشن کند.\n\n"
                    "برای حذف پیام، مجوز حذف پیام‌های گروه "
                    "را نیز بررسی کن."
                ),
            )

        return True

    # ==========================================
    #              Moderation Handler
    # ==========================================

    def handle_moderation(self, message):
        """
        پردازش قابلیت‌های مدیریتی.

        ترتیب حفظ شده:
        ناظرها، سکوت، قفل‌ها، ضد فحش،
        ضداسپم و بن/آنبن/اخراج.
        """

        if not isinstance(message, dict):
            return False

        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return False

        # --------------------------------------
        #               Moderators
        # --------------------------------------

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

        # --------------------------------------
        #                  Mute
        # --------------------------------------

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

        # --------------------------------------
        #                  Locks
        # --------------------------------------

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

            # تخلف از قفل رسانه‌ها اخطار ندارد.
            return True

        # --------------------------------------
        #                Profanity
        # --------------------------------------

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

            # فقط نتیجه‌ای که ماژول ضد فحش به‌عنوان
            # تخلف شناسایی‌شده برگردانده باشد.
            if profanity_result.get("deleted") is True:
                self._record_confirmed_violation(
                    message,
                    "ارسال پیام حاوی فحش",
                )

            return True

        # --------------------------------------
        #                   Spam
        # --------------------------------------

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

            # دستورهای معمولی مثل «اسپم روشن»
            # فقط text برمی‌گردانند و اخطار ندارند.
            if spam_result.get("deleted") is True:
                self._record_confirmed_violation(
                    message,
                    "ارسال پیام اسپم",
                )

            return True

        # --------------------------------------
        #              Ban / Unban / Kick
        # --------------------------------------

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

        action, _duration = (
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
    #               Group Commands
    # ==========================================

    def _handle_group_panel_command(self, message):
        """
        بازکردن پنل با دستور متنی.

        دستور:
            پنل
            پنل مدیریت

        پیام فرمان به پیام پنل تبدیل می‌شود.
        """

        text = message.get("text", "")

        if not isinstance(text, str):
            return False

        normalized = normalize_text(text)

        if normalized not in {
            "پنل",
            "پنل مدیریت",
        }:
            return False

        if not self.is_moderation_authorized(message):
            self.send_message(
                (message.get("chat") or {}).get("id"),
                (
                    "⛔ فقط ادمین گروه یا ناظر ثبت‌شده "
                    "می‌تواند پنل مدیریت را باز کند."
                ),
            )

            return True

        opened = self.panel_feature.open_panel(message)

        if not opened:
            logger.warning(
                "Could not open panel | chat=%s | message=%s",
                (message.get("chat") or {}).get("id"),
                message.get("message_id"),
            )

            self.send_message(
                (message.get("chat") or {}).get("id"),
                (
                    "❌ بازکردن پنل ناموفق بود. "
                    "لطفاً مجوزها و پاسخ API بله را بررسی کن."
                ),
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

        # --------------------------------------
        #              Save User
        # --------------------------------------

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

        # --------------------------------------
        #             Group Messages
        # --------------------------------------

        if chat_type in {
            "group",
            "supergroup",
        }:

            # ثبت گروه و آمار پیام‌ها
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
                    "Could not update group statistics | "
                    "chat_id=%s",
                    chat_id,
                )

            # ----------------------------------
            #          Open Management Panel
            # ----------------------------------

            if self._handle_group_panel_command(message):
                return

            # ----------------------------------
            #           Whisper Disabled
            # ----------------------------------

            try:
                whisper_enabled = (
                    self.panel_feature.get_whisper_enabled(
                        chat_id
                    )
                )

            except Exception:
                logger.exception(
                    "Could not read whisper setting | chat=%s",
                    chat_id,
                )

                # در صورت خطا، نجوا را اجرا نکن.
                whisper_enabled = False

            if not whisper_enabled:
                if self._handle_disabled_whisper(message):
                    return

            else:
                # ----------------------------------
                #              Whisper
                # ----------------------------------

                if self.whisper_feature.handle_message(message):
                    return

            # ----------------------------------
            #                GIF Maker
            # ----------------------------------

            # فقط دستور گیف روی رسانه را پردازش می‌کند.
            # پیام‌های دیگر به مسیر قبلی ربات می‌روند.
            try:
                if self.gif_maker_feature.handle_message(message):
                    return

            except Exception:
                logger.exception(
                    "GIF maker handler failed | chat=%s",
                    chat_id,
                )

            # ----------------------------------
            #             Moderation
            # ----------------------------------

            if self.handle_moderation(message):
                return

        # --------------------------------------
        #             Text Validation
        # --------------------------------------

        if not isinstance(text, str):
            return

        command = (
            text.split(maxsplit=1)[0].lower()
            if text
            else ""
        )

        # --------------------------------------
        #                 /start
        # --------------------------------------

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

        # --------------------------------------
        #                  Help
        # --------------------------------------

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

        # --------------------------------------
        #             Manage Groups
        # --------------------------------------

        elif (
            chat_type == "private"
            and text == "📊 مدیریت گروه‌ها"
        ):
            self.send_message(
                chat_id,
                "📊 بخش مدیریت گروه‌ها در حال توسعه است.",
                main_menu(),
            )

        # --------------------------------------
        #                Settings
        # --------------------------------------

        elif (
            chat_type == "private"
            and text == "⚙️ تنظیمات"
        ):
            self.send_message(
                chat_id,
                "⚙️ تنظیمات NevoBot در حال توسعه است.",
                main_menu(),
            )

        # --------------------------------------
        #              AI Assistant
        # --------------------------------------

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
    #                Callback Handling
    # ==========================================

    def handle_callback_query(self, callback_query):
        """
        هدایت callbackها به ماژول مربوطه.

        ابتدا پنل مدیریت بررسی می‌شود؛
        سپس callbackهای نجوا به ماژول نجوا می‌روند.
        """

        if not isinstance(callback_query, dict):
            return

        data = str(
            callback_query.get("data", "")
        )

        # --------------------------------------
        #             Management Panel
        # --------------------------------------

        if data.startswith("nvpanel:"):
            try:
                self.panel_feature.handle_callback_query(
                    callback_query
                )

            except Exception:
                logger.exception(
                    "Panel callback processing failed."
                )

                self.answer_callback(
                    callback_query.get("id"),
                    "❌ اجرای عملیات پنل ناموفق بود.",
                    True,
                )

            return

        # --------------------------------------
        #                 Whisper
        # --------------------------------------

        if data.startswith("whisper_open:"):
            try:
                self.whisper_feature.handle_callback_query(
                    callback_query
                )

            except Exception:
                logger.exception(
                    "Whisper callback processing failed."
                )

                self.answer_callback(
                    callback_query.get("id"),
                    "❌ بازکردن نجوا ناموفق بود.",
                    True,
                )

            return

    # ==========================================
    #                  Main Loop
    # ==========================================

    def run(self):
        """راه‌اندازی دیتابیس و اجرای حلقه ربات."""

        logger.info(
            "Initializing NevoBot database..."
        )

        # ابتدا دیتابیس اصلی
        init_database()

        # سپس جدول‌های قابلیت‌ها
        self.locks_feature.init_database()
        self.whisper_feature.init_database()
        self.profanity_feature.init_database()
        self.warnings_feature.init_database()

        logger.info(
            "All feature databases initialized."
        )

        # --------------------------------------
        #                Verify Bot
        # --------------------------------------

        logger.info(
            "Connecting to Bale API..."
        )

        info = self.api("getMe")

        if (
            not isinstance(info, dict)
            or not info.get("ok")
        ):
            description = (
                info.get(
                    "description",
                    "Unknown error",
                )
                if isinstance(info, dict)
                else "No response"
            )

            raise RuntimeError(
                "اتصال به API بله ناموفق بود؛ "
                "توکن و اتصال شبکه را بررسی کن. علت: "
                f"{description}"
            )

        bot_info = info.get("result") or {}

        logger.info(
            "Bot connected: @%s",
            bot_info.get("username", "unknown"),
        )

        logger.info(
            "NevoBot is running. Polling started."
        )

        # --------------------------------------
        #                 Polling
        # --------------------------------------

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
                not isinstance(updates, dict)
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
                    # --------------------------
                    #          Callback
                    # --------------------------

                    if "callback_query" in update:
                        self.handle_callback_query(
                            update["callback_query"]
                        )

                    # --------------------------
                    #           Message
                    # --------------------------

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
        logger.info(
            "NevoBot stopped manually."
        )

    except Exception:
        logger.exception(
            "NevoBot failed to start or stopped unexpectedly."
        )
        raise
