# features/profanity.py

import re
import logging

from database import get_connection

from features.profanity_words import (
    PROFANITY_LEVELS,
    DEFAULT_LEVEL,
    SAFE_WORDS,
)


logger = logging.getLogger("NevoBot.Profanity")


class ProfanityFeature:
    """
    سیستم ضد فحش NevoBot.

    امکانات:
    - فعال و غیرفعال کردن ضد فحش
    - سه درجه سخت‌گیری
    - ذخیره تنظیمات هر گروه در SQLite
    - نرمال‌سازی متن فارسی
    - تشخیص فاصله و جداکننده بین حروف
    - حذف پیام حاوی فحش
    - معاف کردن مدیران و ناظرهای مجاز
    """

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    # ==========================================
    #                 Database
    # ==========================================

    def init_database(self):
        """ساخت جدول تنظیمات ضد فحش."""

        with get_connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS profanity_settings (
                    chat_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    level TEXT NOT NULL DEFAULT 'medium'
                )
            """)

            connection.commit()

    def get_settings(self, chat_id):
        """دریافت تنظیمات ضد فحش یک گروه."""

        with get_connection() as connection:
            row = connection.execute("""
                SELECT enabled, level
                FROM profanity_settings
                WHERE chat_id = ?
            """, (str(chat_id),)).fetchone()

        if not row:
            return {
                "enabled": False,
                "level": DEFAULT_LEVEL,
            }

        level = row["level"]

        if level not in PROFANITY_LEVELS:
            level = DEFAULT_LEVEL

        return {
            "enabled": bool(row["enabled"]),
            "level": level,
        }

    def save_settings(
        self,
        chat_id,
        enabled=None,
        level=None,
    ):
        """ذخیره تنظیمات بدون از بین بردن گزینه‌های قبلی."""

        current = self.get_settings(chat_id)

        if enabled is None:
            enabled = current["enabled"]

        if level is None:
            level = current["level"]

        if level not in PROFANITY_LEVELS:
            level = DEFAULT_LEVEL

        with get_connection() as connection:
            connection.execute("""
                INSERT INTO profanity_settings (
                    chat_id,
                    enabled,
                    level
                )
                VALUES (?, ?, ?)

                ON CONFLICT(chat_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    level = excluded.level
            """, (
                str(chat_id),
                int(bool(enabled)),
                level,
            ))

            connection.commit()

    # ==========================================
    #              Text Normalization
    # ==========================================

    @staticmethod
    def normalize_characters(text):
        """
        یکسان‌سازی حروف فارسی و عربی و حذف کشیدگی.
        """

        text = str(text or "")

        replacements = {
            "ي": "ی",
            "ى": "ی",
            "ك": "ک",
            "أ": "ا",
            "إ": "ا",
            "ٱ": "ا",
            "ؤ": "و",
            "ئ": "ی",
            "ة": "ه",
            "ۀ": "ه",
            "ـ": "",
            "\u200c": " ",
            "\u200f": "",
            "\u200e": "",
            "\ufeff": "",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        return text.lower()

    @classmethod
    def normalize_word(cls, text):
        """
        حذف جداکننده‌ها از یک کلمه برای تشخیص شکل‌های
        تغییریافته مثل ک.س یا ک-س.
        """

        text = cls.normalize_characters(text)

        return re.sub(
            r"[^a-zA-Z0-9\u0600-\u06FF]",
            "",
            text,
        )

    @classmethod
    def tokenize(cls, text):
        """
        استخراج کلمات و ترکیب حروفی که جداگانه نوشته شده‌اند.

        مثال:
        ک.س.ن.ن.ت -> کسننت
        ک س ن ن ت -> کسننت
        """

        text = cls.normalize_characters(text)

        # کلمات، اعداد و نشانه‌های جداکننده
        parts = re.findall(
            r"[a-zA-Z0-9\u0600-\u06FF]+|[^\w\s]",
            text,
        )

        tokens = []
        single_letters = []

        def flush_single_letters():
            nonlocal single_letters

            if len(single_letters) >= 2:
                tokens.append("".join(single_letters))
            else:
                tokens.extend(single_letters)

            single_letters = []

        for part in parts:
            if re.fullmatch(
                r"[a-zA-Z\u0600-\u06FF]",
                part,
            ):
                single_letters.append(part)
                continue

            flush_single_letters()

            # نشانه‌ها وارد کلمه نمی‌شوند.
            if re.fullmatch(
                r"[a-zA-Z0-9\u0600-\u06FF]+",
                part,
            ):
                tokens.append(part)

        flush_single_letters()

        return [
            cls.normalize_word(token)
            for token in tokens
            if token
        ]

    # ==========================================
    #             Profanity Detection
    # ==========================================

    @classmethod
    def contains_profanity(cls, text, level="medium"):
        """
        بررسی متن براساس درجه انتخاب‌شده.

        نتیجه:
        True  -> مورد ممنوع تشخیص داده شده
        False -> مورد ممنوع تشخیص داده نشده
        """

        if level not in PROFANITY_LEVELS:
            level = DEFAULT_LEVEL

        config = PROFANITY_LEVELS[level]

        # نرمال‌سازی فهرست ممنوع
        bad_words = {
            cls.normalize_word(word)
            for word in config["words"]
        }

        # عبارت‌های ممنوع چندکلمه‌ای
        normalized_text = cls.normalize_characters(text)

        for phrase in config["words"]:
            if " " not in phrase:
                continue

            normalized_phrase = re.sub(
                r"\s+",
                " ",
                cls.normalize_characters(phrase),
            )

            if normalized_phrase in normalized_text:
                return True

        tokens = cls.tokenize(text)

        safe_words = {
            cls.normalize_word(word)
            for word in SAFE_WORDS
        }

        for token in tokens:
            if token in safe_words:
                continue

            # تطبیق کلمات کامل و شکل‌های تغییر‌یافته
            if token in bad_words:
                return True

            # بررسی شکل‌های متصل به پسوند یا حروف اضافه
            for bad_word in bad_words:
                if len(bad_word) < 4:
                    continue

                if token.startswith(bad_word):
                    return True

                if token.endswith(bad_word):
                    return True

        return False

    # ==========================================
    #                Message Delete
    # ==========================================

    def delete_message(self, chat_id, message_id):
        """حذف پیام از طریق API بله."""

        if chat_id is None or message_id is None:
            return False

        try:
            result = self.api(
                "deleteMessage",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
            )

            if result and result.get("ok"):
                return True

            logger.warning(
                "Profanity message deletion failed | chat_id=%s | "
                "message_id=%s | response=%s",
                chat_id,
                message_id,
                result,
            )

            return False

        except Exception:
            logger.exception(
                "Error deleting profanity message | chat_id=%s",
                chat_id,
            )

            return False

    # ==========================================
    #                Message Handler
    # ==========================================

    def handle_message(self, message):
        """پردازش دستورات و پیام‌های گروه."""

        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        chat_type = chat.get("type")

        if chat_id is None:
            return None

        if chat_type not in {"group", "supergroup"}:
            return None

        text = str(
            message.get("text")
            or message.get("caption")
            or ""
        ).strip()

        # دستورات تنظیمات
        command = re.sub(r"\s+", " ", text).strip()

        commands = {
            "ضد فحش روشن",
            "ضد فحش خاموش",
            "ضد فحش",
            "درجه فحش آسان",
            "درجه فحش متوسط",
            "درجه فحش سختگیرانه",
            "درجه فحش سخت گیرانه",
        }

        if command in commands:
            if not self.is_authorized(message):
                return {
                    "text": "⛔ فقط مدیران و ناظرهای مجاز می‌توانند تنظیمات ضد فحش را تغییر دهند."
                }

            settings = self.get_settings(chat_id)

            if command == "ضد فحش روشن":
                self.save_settings(
                    chat_id,
                    enabled=True,
                )

                return {
                    "text": "✅ ضد فحش فعال شد."
                }

            if command == "ضد فحش خاموش":
                self.save_settings(
                    chat_id,
                    enabled=False,
                )

                return {
                    "text": "🔓 ضد فحش غیرفعال شد."
                }

            if command == "ضد فحش":
                status = (
                    "روشن 🟢"
                    if settings["enabled"]
                    else "خاموش 🔴"
                )

                level_name = PROFANITY_LEVELS[
                    settings["level"]
                ]["title"]

                return {
                    "text": (
                        "🛡️ وضعیت ضد فحش\n\n"
                        f"وضعیت: {status}\n"
                        f"درجه سخت‌گیری: {level_name}"
                    )
                }

            level_commands = {
                "درجه فحش آسان": "easy",
                "درجه فحش متوسط": "medium",
                "درجه فحش سختگیرانه": "strict",
                "درجه فحش سخت گیرانه": "strict",
            }

            selected_level = level_commands[command]

            self.save_settings(
                chat_id,
                level=selected_level,
            )

            return {
                "text": (
                    "✅ درجه سخت‌گیری ضد فحش تغییر کرد به: "
                    + PROFANITY_LEVELS[selected_level]["title"]
                )
            }

        # اگر متن وجود ندارد، چیزی برای بررسی نیست.
        if not text:
            return None

        # مدیران و ناظرهای مجاز از فیلتر مستثنا هستند.
        try:
            if self.is_authorized(message):
                return None
        except Exception:
            logger.exception(
                "Could not check authorization for profanity filter."
            )

            # در صورت خطا در بررسی مجوز، پیام را حذف نکن.
            return None

        settings = self.get_settings(chat_id)

        if not settings["enabled"]:
            return None

        if not self.contains_profanity(
            text,
            settings["level"],
        ):
            return None

        message_id = message.get("message_id")

        deleted = self.delete_message(
            chat_id,
            message_id,
        )

        if deleted:
            logger.info(
                "Profanity message deleted | chat_id=%s | user_id=%s",
                chat_id,
                sender.get("id"),
            )

            return {
                "deleted": True,
            }

        return None
