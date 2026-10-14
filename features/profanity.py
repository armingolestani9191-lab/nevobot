# ==========================================
#       NevoBot | Profanity Filter
# ==========================================

import logging
import re
from functools import lru_cache

from database import get_connection

from features.profanity_words import (
    PROFANITY_LEVELS,
    DEFAULT_LEVEL,
    SAFE_WORDS,
)


logger = logging.getLogger("NevoBot.Profanity")


# حروفی که در پیام‌های فارسی و عربی معادل هستند.
CHAR_REPLACEMENTS = str.maketrans({
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
})


# ==========================================
#                 Feature
# ==========================================

class ProfanityFeature:

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

    # ==========================================
    #                  Database
    # ==========================================

    def init_database(self):
        with get_connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS profanity_settings (
                    chat_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    level TEXT NOT NULL DEFAULT 'strict'
                )
            """)

            connection.commit()

    def get_settings(self, chat_id):
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
    #             Text Normalization
    # ==========================================

    @staticmethod
    def normalize_characters(text):
        text = str(text or "")

        text = text.translate(CHAR_REPLACEMENTS)

        # یکسان‌سازی نیم‌فاصله و حذف کنترل‌های نامرئی
        text = text.replace("\u200c", " ")
        text = text.replace("\u200d", "")
        text = text.replace("\u200e", "")
        text = text.replace("\u200f", "")
        text = text.replace("\ufeff", "")

        return text.lower()

    @classmethod
    def normalize_word(cls, text):
        text = cls.normalize_characters(text)

        # فقط حروف و اعداد باقی می‌مانند.
        return re.sub(
            r"[^a-zA-Z0-9\u0600-\u06FF]",
            "",
            text,
        )

    @classmethod
    def tokenize(cls, text):
        text = cls.normalize_characters(text)

        return re.findall(
            r"[a-zA-Z0-9\u0600-\u06FF]+",
            text,
        )

    # ==========================================
    #            Compiled Detection
    # ==========================================

    @staticmethod
    @lru_cache(maxsize=4096)
    def make_flexible_pattern(word, separator_limit):
        """
        ساخت الگوی آماده برای تشخیص حروفی که بینشان
        فاصله یا علامت قرار گرفته است.

        نمونه‌ها:
        ک س ک ش
        ک.س.ک.ش
        ک.........سکش
        """

        word = ProfanityFeature.normalize_word(word)

        if not word:
            return None

        # کلمات بسیار کوتاه فقط به شکل مستقل بررسی می‌شوند.
        if len(word) <= 2:
            return re.compile(
                r"(?<!\w)"
                + re.escape(word)
                + r"(?!\w)"
            )

        separator = (
            r"[\W_]{0,"
            + str(int(separator_limit))
            + r"}"
        )

        letters = separator.join(
            re.escape(char)
            for char in word
        )

        return re.compile(
            r"(?<!\w)"
            + letters
            + r"(?!\w)"
        )

    @classmethod
    @lru_cache(maxsize=3)
    def get_detection_patterns(cls, level):
        """
        الگوهای هر درجه فقط یک‌بار ساخته می‌شوند.
        این کار از ساخت دوباره آن‌ها برای هر پیام جلوگیری می‌کند.
        """

        if level not in PROFANITY_LEVELS:
            level = DEFAULT_LEVEL

        config = PROFANITY_LEVELS[level]

        separator_limit = config["separator_limit"]

        patterns = []

        for word in config["words"]:
            normalized = cls.normalize_word(word)

            if not normalized:
                continue

            # در آسان فقط شکل معمول کلمه بررسی می‌شود.
            if separator_limit == 0:
                pattern = re.compile(
                    r"(?<!\w)"
                    + re.escape(normalized)
                    + r"(?!\w)"
                )
            else:
                pattern = cls.make_flexible_pattern(
                    normalized,
                    separator_limit,
                )

            patterns.append(pattern)

        return tuple(patterns)

    # ==========================================
    #             Profanity Detection
    # ==========================================

    @classmethod
    def contains_profanity(cls, text, level=DEFAULT_LEVEL):
        if not text:
            return False

        if level not in PROFANITY_LEVELS:
            level = DEFAULT_LEVEL

        normalized_text = cls.normalize_characters(text)
        tokens = cls.tokenize(normalized_text)

        safe_words = {
            cls.normalize_word(word)
            for word in SAFE_WORDS
        }

        # کلمات عادی مستقل را از بررسی دقیق کنار می‌گذاریم.
        # البته وجود فحش دیگری در همان پیام همچنان بررسی می‌شود.
        safe_token_set = set(tokens)

        patterns = cls.get_detection_patterns(level)

        # در حالت آسان، تطبیق روی کلمات و عبارت‌های معمولی است.
        if level == "easy":
            for token in tokens:
                normalized_token = cls.normalize_word(token)

                if normalized_token in safe_words:
                    continue

                for word in PROFANITY_LEVELS["easy"]["words"]:
                    normalized_bad = cls.normalize_word(word)

                    if not normalized_bad:
                        continue

                    if normalized_token == normalized_bad:
                        return True

            # عبارت‌های چندکلمه‌ای در حالت آسان
            for word in PROFANITY_LEVELS["easy"]["words"]:
                if " " not in word:
                    continue

                phrase = cls.normalize_characters(word)
                phrase = re.sub(r"\s+", " ", phrase).strip()

                text_with_spaces = re.sub(
                    r"\s+",
                    " ",
                    normalized_text,
                ).strip()

                if phrase and phrase in text_with_spaces:
                    return True

            return False

        # متوسط و سخت‌گیرانه از الگوهای آماده استفاده می‌کنند.
        # این کار فاصله‌ها و علائم میان حروف را تشخیص می‌دهد.
        for pattern in patterns:
            if pattern is None:
                continue

            match = pattern.search(normalized_text)

            if not match:
                continue

            matched_text = match.group(0)
            matched_word = cls.normalize_word(matched_text)

            # جلوگیری از برخی تطبیق‌های اشتباه روی واژه‌های عادی
            if matched_word in safe_words:
                continue

            # اگر فقط یک کلمه عادی با الگو تطبیق کرده باشد،
            # آن را فحش محسوب نمی‌کنیم.
            if matched_word in safe_token_set:
                if matched_word in safe_words:
                    continue

            return True

        return False

    # ==========================================
    #                Delete Message
    # ==========================================

    def delete_message(self, chat_id, message_id):
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
                "Profanity deletion failed | chat_id=%s | "
                "message_id=%s | response=%s",
                chat_id,
                message_id,
                result,
            )

            return False

        except Exception:
            logger.exception(
                "Profanity deletion exception | chat_id=%s",
                chat_id,
            )

            return False

    # ==========================================
    #               Message Handler
    # ==========================================

    def handle_message(self, message):
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}

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

        command = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        commands = {
            "ضد فحش روشن",
            "ضد فحش خاموش",
            "ضد فحش",
            "درجه فحش آسان",
            "درجه فحش متوسط",
            "درجه فحش سختگیرانه",
            "درجه فحش سخت گیرانه",
        }

        # ==========================================
        #                Settings Commands
        # ==========================================

        if command in commands:
            try:
                authorized = self.is_authorized(message)
            except Exception:
                logger.exception(
                    "Could not verify moderation authorization."
                )

                return {
                    "text": "❌ بررسی دسترسی ناموفق بود؛ دوباره تلاش کن."
                }

            if not authorized:
                return {
                    "text": (
                        "⛔ فقط مدیران و ناظرهای مجاز "
                        "می‌توانند تنظیمات ضد فحش را تغییر دهند."
                    )
                }

            settings = self.get_settings(chat_id)

            if command == "ضد فحش روشن":
                # طبق درخواست: با روشن کردن ضد فحش،
                # درجه روی سخت‌گیرانه قرار می‌گیرد.
                self.save_settings(
                    chat_id,
                    enabled=True,
                    level="strict",
                )

                return {
                    "text": (
                        "🛡️ ضد فحش فعال شد.\n\n"
                        "🔴 درجه پیش‌فرض: سخت‌گیرانه\n"
                        "تمام فحش‌های فهرست این درجه بررسی می‌شوند."
                    )
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
                    "✅ درجه سخت‌گیری تغییر کرد به: "
                    + PROFANITY_LEVELS[selected_level]["title"]
                )
            }

        # ==========================================
        #               Filter Messages
        # ==========================================

        if not text:
            return None

        # مدیران و ناظرهای مجاز از فیلتر معاف‌اند.
        try:
            if self.is_authorized(message):
                return None

        except Exception:
            logger.exception(
                "Authorization check failed for profanity filter."
            )

            # در صورت خطا، پیام را حذف نمی‌کنیم.
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

        if self.delete_message(chat_id, message_id):
            logger.info(
                "Profanity message deleted | chat_id=%s",
                chat_id,
            )

            return {
                "deleted": True,
            }

        return None
