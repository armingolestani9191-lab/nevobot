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
from features.moderators import ModeratorsFeature


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("NevoBot")


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

        # مدیریت ناظرها؛ فقط ادمین واقعی گروه اجازه مدیریت دارد.
        self.moderators_feature = ModeratorsFeature(
            is_group_admin=self.is_group_admin,
        )

        # ادمین واقعی و ناظر ثبت‌شده می‌توانند از این بخش استفاده کنند.
        self.ban_feature = BanFeature(
            api=self.api,
            is_authorized=self.is_moderation_authorized,
        )

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
                logger.warning("Invalid API response: %s", method)
                return None

            if not data.get("ok"):
                logger.warning(
                    "API error (%s): %s",
                    method,
                    data.get("description", "Unknown error"),
                )

            return data

        except (requests.RequestException, ValueError):
            logger.exception("Request failed: %s", method)
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

    def is_group_admin(self, message):
        """
        بررسی ادمین واقعی گروه از طریق API.
        اگر API نتواند دسترسی را تأیید کند، نتیجه False است.
        """
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
            logger.warning("Could not verify group administrator")
            return False

        member = result.get("result") or {}
        status = str(member.get("status", "")).lower()

        return status in {
            "creator",
            "administrator",
            "owner",
            "admin",
        }

    def is_moderation_authorized(self, message):
        """
        اجازه مدیریت اعضا برای ادمین واقعی یا ناظر همان گروه.
        این تابع اجازه مدیریت ناظرها را نمی‌دهد؛ آن کار فقط
        در ModeratorsFeature و با بررسی ادمین واقعی انجام می‌شود.
        """
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

    def handle_moderation(self, message):
        """پردازش اول دستورهای ناظر و سپس دستورهای بن."""
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return False

        # اول مدیریت ناظرها؛ فقط ادمین واقعی می‌تواند تغییر دهد.
        moderator_result = self.moderators_feature.handle_message(
            message
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

        # بعد بن، آنبن و کیک؛ ادمین واقعی یا ناظر مجاز.
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

        self.send_message(chat_id, response_text)
        return True

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
        save_user(user_id, username, first_name)

        if chat_type in {"group", "supergroup"}:
            save_group(chat_id, chat.get("title", ""))

            if text:
                record_group_message(chat_id, user_id)

            if self.handle_moderation(message):
                return

        if not isinstance(text, str):
            return

        command = text.split(maxsplit=1)[0].lower() if text else ""

        if chat_type == "private" and command in {
            "/start",
            "/start@nevobot",
        }:
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

        elif chat_type == "private" and text == "📚 راهنما":
            self.send_message(
                chat_id,
                (
                    "📚 راهنمای NevoBot\n\n"
                    "ربات را به گروه اضافه کن و مجوزهای "
                    "موردنیاز را به آن بده.\n"
                    "برای استفاده از بن و مدیریت اعضا، "
                    "ربات باید مجوزهای لازم را داشته باشد."
                ),
                main_menu(),
            )

        elif chat_type == "private" and text == "📊 مدیریت گروه‌ها":
            self.send_message(
                chat_id,
                "📊 بخش مدیریت گروه‌ها در حال توسعه است.",
                main_menu(),
            )

        elif chat_type == "private" and text == "⚙️ تنظیمات":
            self.send_message(
                chat_id,
                "⚙️ تنظیمات NevoBot در حال توسعه است.",
                main_menu(),
            )

        elif chat_type == "private" and text == "🤖 دستیار هوشمند":
            self.send_message(
                chat_id,
                "🤖 این بخش هنوز فعال نشده است.",
                main_menu(),
            )

    def run(self):
        init_database()
        logger.info("NevoBot is starting...")

        info = self.api("getMe")

        if not info or not info.get("ok"):
            raise RuntimeError(
                "اتصال به API بله ناموفق بود؛ "
                "توکن و اتصال شبکه را بررسی کن."
            )

        bot_info = info.get("result") or {}
        logger.info(
            "Bot connected: @%s",
            bot_info.get("username", "unknown"),
        )

        while True:
            updates = self.api(
                "getUpdates",
                {
                    "offset": self.offset,
                    "timeout": POLLING_TIMEOUT,
                    "allowed_updates": ["message"],
                },
            )

            if not updates or not updates.get("ok"):
                time.sleep(2)
                continue

            for update in updates.get("result", []):
                update_id = update.get("update_id")

                if isinstance(update_id, int):
                    self.offset = max(
                        self.offset,
                        update_id + 1,
                    )

                try:
                    self.handle_message(
                        update.get("message", {})
                    )
                except Exception:
                    logger.exception("Failed to process update")


if __name__ == "__main__":
    NevoBot().run()
