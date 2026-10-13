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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("NevoBot")


class NevoBot:
    def __init__(self):
        if not BOT_TOKEN:
            raise RuntimeError(
                "توکن تنظیم نشده است. "
                "متغیر محیطی BALE_BOT_TOKEN را تنظیم کن."
            )

        self.base_url = f"{API_BASE_URL}/bot{BOT_TOKEN}"
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json"
        })
        self.offset = 0

    def api(self, method, payload=None):
        url = f"{self.base_url}/{method}"

        try:
            response = self.session.post(
                url,
                json=payload or {},
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                logger.warning(
                    "API error on %s: %s",
                    method,
                    data.get("description", "Unknown error")
                )

            return data

        except (requests.RequestException, ValueError) as error:
            logger.warning("Request failed (%s): %s", method, error)
            return None

    def send_message(self, chat_id, text, keyboard=None):
        payload = {
            "chat_id": chat_id,
            "text": text
        }

        if keyboard is not None:
            payload["reply_markup"] = keyboard

        return self.api("sendMessage", payload)

    def handle_message(self, message):
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

        save_user(user_id, username, first_name)

        if chat_type in ("group", "supergroup"):
            save_group(chat_id, chat.get("title", ""))

            if text:
                record_group_message(chat_id, user_id)

        if not isinstance(text, str):
            return

        command = text.split(maxsplit=1)[0].lower() if text else ""

        if chat_type == "private" and command in ("/start", "/start@nevobot"):
            self.send_message(
                chat_id,
                (
                    f"سلام {first_name or 'رفیق'}! 👋\n\n"
                    "به NevoBot خوش اومدی.\n"
                    "مدیریت هوشمند گروه‌ها، با امکانات "
                    "قابل تنظیم و توسعه.\n\n"
                    "برای شروع، از منوی زیر استفاده کن."
                ),
                main_menu()
            )

        elif chat_type == "private" and text == "📚 راهنما":
            self.send_message(
                chat_id,
                (
                    "📚 راهنمای NevoBot\n\n"
                    "۱. ربات را به گروه اضافه کن.\n"
                    "۲. دسترسی‌های موردنیاز را به آن بده.\n"
                    "۳. قابلیت‌های مدیریت را پس از فعال‌شدن "
                    "از منو تنظیم کن.\n\n"
                    "توجه: امکانات مدیریتی در حال توسعه هستند."
                ),
                main_menu()
            )

        elif chat_type == "private" and text == "📊 مدیریت گروه‌ها":
            self.send_message(
                chat_id,
                "📊 بخش مدیریت گروه‌ها در نسخه‌های بعدی تکمیل می‌شود.",
                main_menu()
            )

        elif chat_type == "private" and text == "⚙️ تنظیمات":
            self.send_message(
                chat_id,
                "⚙️ تنظیمات NevoBot هنوز در حال توسعه است.",
                main_menu()
            )

        elif chat_type == "private" and text == "🤖 دستیار هوشمند":
            self.send_message(
                chat_id,
                "🤖 بخش دستیار هوشمند بعد از آماده‌شدن اتصال آن فعال می‌شود.",
                main_menu()
            )

    def run(self):
        init_database()

        logger.info("NevoBot is starting...")

        info = self.api("getMe")

        if not info or not info.get("ok"):
            raise RuntimeError(
                "اتصال اولیه به API بله موفق نبود؛ "
                "توکن و دسترسی شبکه را بررسی کن."
            )

        bot_info = info.get("result") or {}
        logger.info(
            "Bot connected: @%s",
            bot_info.get("username", "unknown")
        )

        while True:
            updates = self.api("getUpdates", {
                "offset": self.offset,
                "timeout": POLLING_TIMEOUT,
                "allowed_updates": ["message"]
            })

            if not updates or not updates.get("ok"):
                time.sleep(3)
                continue

            for update in updates.get("result", []):
                update_id = update.get("update_id")

                if isinstance(update_id, int):
                    self.offset = max(self.offset, update_id + 1)

                try:
                    self.handle_message(update.get("message", {}))
                except Exception:
                    logger.exception("Failed to process an update")


if __name__ == "__main__":
    NevoBot().run()
