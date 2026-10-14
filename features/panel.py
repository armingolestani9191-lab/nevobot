# ==========================================
#          NevoBot | Management Panel
# ==========================================

import logging
import sqlite3

from database import get_connection, get_group
from features.locks import LOCKS, LocksFeature
from features.profanity_words import PROFANITY_LEVELS

logger = logging.getLogger("NevoBot.Panel")


# ==========================================
#                 Constants
# ==========================================

CALLBACK_PREFIX = "nvpanel"

PAGE_MAIN = "main"
PAGE_LOCKS = "locks"
PAGE_COMMANDS = "commands"
PAGE_WARNINGS = "warnings"
PAGE_PROFANITY = "profanity"

PUNISHMENTS = {
    "kick": "اخراج",
    "ban": "بن",
    "mute": "سکوت",
}

PROFANITY_LEVEL_NAMES = {
    "easy": "آسان",
    "medium": "متوسط",
    "strict": "سخت‌گیرانه",
}


# ==========================================
#              Panel Feature
# ==========================================

class PanelFeature:
    """
    پنل مدیریت گروه NevoBot.

    نکات:
    - تنظیمات برای هر گروه جداگانه ذخیره می‌شوند.
    - برای هر callback مجدداً دسترسی بررسی می‌شود.
    - پنل با editMessageText به‌روزرسانی می‌شود.
    - هیچ تنظیمی بدون تأیید دسترسی تغییر نمی‌کند.
    """

    def __init__(
        self,
        api,
        is_authorized,
        spam_feature,
        profanity_feature,
        warnings_feature,
    ):
        self.api = api
        self.is_authorized = is_authorized
        self.spam_feature = spam_feature
        self.profanity_feature = profanity_feature
        self.warnings_feature = warnings_feature

        self._init_database()

    # ======================================
    #                 Database
    # ======================================

    @staticmethod
    def _init_database():
        """ساخت جدول تنظیمات اختصاصی پنل."""

        with get_connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS panel_group_settings (
                    chat_id TEXT PRIMARY KEY,
                    whisper_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                )
            """)

            connection.commit()

    @staticmethod
    def _ensure_group(chat_id):
        """ایجاد تنظیمات اولیه گروه در صورت نیاز."""

        with get_connection() as connection:
            connection.execute("""
                INSERT OR IGNORE INTO panel_group_settings (
                    chat_id,
                    whisper_enabled
                )
                VALUES (?, 1)
            """, (str(chat_id),))

            connection.commit()

    @classmethod
    def get_whisper_enabled(cls, chat_id):
        """دریافت وضعیت نجوا."""

        cls._ensure_group(chat_id)

        with get_connection() as connection:
            row = connection.execute("""
                SELECT whisper_enabled
                FROM panel_group_settings
                WHERE chat_id = ?
                LIMIT 1
            """, (str(chat_id),)).fetchone()

        return bool(row["whisper_enabled"]) if row else True

    @classmethod
    def set_whisper_enabled(cls, chat_id, enabled):
        """ذخیره وضعیت نجوا."""

        cls._ensure_group(chat_id)

        with get_connection() as connection:
            connection.execute("""
                UPDATE panel_group_settings
                SET whisper_enabled = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
            """, (
                int(bool(enabled)),
                str(chat_id),
            ))

            connection.commit()

    # ======================================
    #             General Helpers
    # ======================================

    @staticmethod
    def _is_group_chat(chat):
        return (
            isinstance(chat, dict)
            and chat.get("type") in {
                "group",
                "supergroup",
            }
        )

    @staticmethod
    def _get_message_context(message):
        """استخراج شناسه گروه و پیام."""

        chat = message.get("chat") or {}

        return (
            chat.get("id"),
            message.get("message_id"),
        )

    @staticmethod
    def _keyboard(rows):
        """ساخت صفحه‌کلید اینلاین."""

        return {
            "inline_keyboard": rows,
        }

    @staticmethod
    def _button(text, data):
        return {
            "text": str(text),
            "callback_data": str(data),
        }

    @staticmethod
    def _callback_data(action, *parts):
        """
        ساخت callback کوتاه.

        قالب:
        nvpanel:<action>:<arguments>
        """

        values = [
            CALLBACK_PREFIX,
            str(action),
        ]

        values.extend(str(part) for part in parts)

        data = ":".join(values)

        # محدودیت متداول callback_data در تلگرام و
        # بسیاری از APIهای مشابه ۶۴ بایت است.
        if len(data.encode("utf-8")) > 64:
            raise ValueError(
                "داده دکمه پنل بیش از حد طولانی است."
            )

        return data

    def _answer_callback(
        self,
        callback_id,
        text="",
        show_alert=False,
    ):
        """پاسخ به کلیک بدون ارسال پیام جدید."""

        if not callback_id:
            return

        try:
            self.api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": str(text or "")[:190],
                    "show_alert": bool(show_alert),
                },
            )
        except Exception:
            logger.exception(
                "Could not answer panel callback."
            )

    def _edit_panel(
        self,
        chat_id,
        message_id,
        text,
        keyboard=None,
    ):
        """ویرایش همان پیام پنل."""

        if chat_id is None or message_id is None:
            return False

        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": str(text),
        }

        if keyboard is not None:
            payload["reply_markup"] = keyboard
        else:
            payload["reply_markup"] = {
                "inline_keyboard": [],
            }

        try:
            result = self.api(
                "editMessageText",
                payload,
            )
        except Exception:
            logger.exception(
                "Could not edit panel message."
            )
            return False

        if not isinstance(result, dict):
            return False

        if result.get("ok") is True:
            return True

        description = str(
            result.get("description", "")
        ).lower()

        # اگر متن و صفحه‌کلید از قبل همین باشند،
        # بعضی APIها درخواست را ناموفق اعلام می‌کنند.
        if (
            "message is not modified" in description
            or "message not modified" in description
        ):
            return True

        logger.warning(
            "Panel edit failed: %s",
            result.get("description", "Unknown error"),
        )

        return False

    def _authorized(self, message):
        """
        بررسی امن دسترسی.

        در صورت خطا، دسترسی رد می‌شود.
        """

        try:
            return bool(
                self.is_authorized(message)
            )
        except Exception:
            logger.exception(
                "Panel authorization check failed."
            )
            return False

    @staticmethod
    def _group_id(message):
        return (message.get("chat") or {}).get("id")

    @staticmethod
    def _sender_id(message):
        return (message.get("from") or {}).get("id")

    # ======================================
    #                Main Page
    # ======================================

    def _main_page(self):
        text = (
            "🛡️ پنل مدیریت NevoBot\n\n"
            "از دکمه‌های زیر برای مدیریت گروه استفاده کن.\n\n"
            "🔒 قفل‌ها: کنترل ارسال رسانه‌ها\n"
            "⚙️ دستورات: تنظیم قابلیت‌های گروه\n"
            "⚠️ اخطارها: مدیریت اخطار و مجازات"
        )

        keyboard = self._keyboard([
            [
                self._button(
                    "🔒 قفل‌ها",
                    self._callback_data("page", PAGE_LOCKS),
                ),
                self._button(
                    "⚙️ دستورات",
                    self._callback_data("page", PAGE_COMMANDS),
                ),
            ],
            [
                self._button(
                    "⚠️ اخطارها",
                    self._callback_data("page", PAGE_WARNINGS),
                ),
            ],
            [
                self._button(
                    "❌ بستن پنل",
                    self._callback_data("close"),
                ),
            ],
        ])

        return text, keyboard

    # ======================================
    #                Lock Page
    # ======================================

    def _locks_page(self, chat_id):
        lines = [
            "🔒 مدیریت قفل‌های گروه",
            "",
            "برای تغییر وضعیت هر قفل روی دکمه آن بزن.",
            "",
        ]

        rows = []

        for lock_key, info in LOCKS.items():
            enabled = LocksFeature.get_lock_status(
                chat_id,
                lock_key,
            )

            if enabled is None:
                status = "❔"
            elif enabled:
                status = "🔒 روشن"
            else:
                status = "🔓 خاموش"

            lines.append(
                f"{status} | {info['name']}"
            )

            rows.append([
                self._button(
                    f"{status} {info['name']}",
                    self._callback_data(
                        "lock",
                        lock_key,
                    ),
                )
            ])

        rows.append([
            self._button(
                "↩️ برگشت",
                self._callback_data("back", PAGE_MAIN),
            )
        ])

        return (
            "\n".join(lines),
            self._keyboard(rows),
        )

    # ======================================
    #               Commands Page
    # ======================================

    def _commands_page(self, chat_id):
        try:
            group = get_group(chat_id) or {}
            spam_enabled = bool(
                group.get("anti_spam_enabled", 0)
            )
        except Exception:
            logger.exception(
                "Could not read anti-spam status."
            )
            spam_enabled = False

        try:
            profanity = (
                self.profanity_feature.get_settings(
                    chat_id
                )
            )
        except Exception:
            logger.exception(
                "Could not read profanity settings."
            )
            profanity = {
                "enabled": False,
                "level": "strict",
            }

        whisper_enabled = self.get_whisper_enabled(
            chat_id
        )

        spam_text = (
            "🟢 ضداسپم روشن"
            if spam_enabled
            else "🔴 ضداسپم خاموش"
        )

        profanity_text = (
            "🟢 ضد فحش روشن"
            if profanity["enabled"]
            else "🔴 ضد فحش خاموش"
        )

        whisper_text = (
            "🟢 نجوا روشن"
            if whisper_enabled
            else "🔴 نجوا خاموش"
        )

        level = profanity.get(
            "level",
            "strict",
        )

        level_name = PROFANITY_LEVEL_NAMES.get(
            level,
            level,
        )

        text = (
            "⚙️ تنظیمات دستورات\n\n"
            f"{spam_text}\n"
            "برای روشن یا خاموش کردن ضداسپم کلیک کن.\n\n"
            f"{profanity_text}\n"
            f"درجه فعلی: {level_name}\n\n"
            f"{whisper_text}\n"
            "برای تغییر وضعیت نجوا کلیک کن."
        )

        keyboard = self._keyboard([
            [
                self._button(
                    spam_text,
                    self._callback_data("spam"),
                ),
            ],
            [
                self._button(
                    profanity_text,
                    self._callback_data("profanity_toggle"),
                ),
                self._button(
                    "🎚️ درجه ضد فحش",
                    self._callback_data(
                        "page",
                        PAGE_PROFANITY,
                    ),
                ),
            ],
            [
                self._button(
                    whisper_text,
                    self._callback_data("whisper"),
                ),
            ],
            [
                self._button(
                    "↩️ برگشت",
                    self._callback_data("back", PAGE_MAIN),
                ),
            ],
        ])

        return text, keyboard

    # ======================================
    #              Profanity Page
    # ======================================

    def _profanity_page(self, chat_id):
        settings = self.profanity_feature.get_settings(
            chat_id
        )

        enabled = bool(settings.get("enabled"))
        level = settings.get("level", "strict")

        lines = [
            "🛡️ تنظیمات ضد فحش",
            "",
            (
                "وضعیت: 🟢 روشن"
                if enabled
                else "وضعیت: 🔴 خاموش"
            ),
            f"درجه فعلی: {PROFANITY_LEVEL_NAMES.get(level, level)}",
            "",
        ]

        rows = []

        if enabled:
            for level_key in ("easy", "medium", "strict"):
                if level_key not in PROFANITY_LEVELS:
                    continue

                name = PROFANITY_LEVEL_NAMES.get(
                    level_key,
                    level_key,
                )

                mark = " ✅" if level == level_key else ""

                rows.append([
                    self._button(
                        name + mark,
                        self._callback_data(
                            "profanity_level",
                            level_key,
                        ),
                    )
                ])

            rows.append([
                self._button(
                    "🔴 خاموش کردن ضد فحش",
                    self._callback_data(
                        "profanity_toggle",
                    ),
                )
            ])

        else:
            rows.append([
                self._button(
                    "🟢 روشن کردن ضد فحش",
                    self._callback_data(
                        "profanity_toggle",
                    ),
                )
            ])

        rows.append([
            self._button(
                "↩️ برگشت",
                self._callback_data(
                    "back",
                    PAGE_COMMANDS,
                ),
            )
        ])

        return (
            "\n".join(lines),
            self._keyboard(rows),
        )

    # ======================================
    #               Warnings Page
    # ======================================

    def _warnings_page(self, chat_id):
        settings = self.warnings_feature.get_settings(
            chat_id
        )

        enabled = bool(settings.get("enabled", True))
        max_warnings = int(
            settings.get("max_warnings", 3)
        )

        punishment = settings.get(
            "punishment",
            "kick",
        )

        punishment_name = PUNISHMENTS.get(
            punishment,
            "اخراج",
        )

        status = (
            "🟢 روشن"
            if enabled
            else "🔴 خاموش"
        )

        text = (
            "⚠️ تنظیمات سیستم اخطار\n\n"
            f"وضعیت اخطارها: {status}\n"
            f"حداکثر اخطار: {max_warnings}\n"
            f"مجازات نهایی: {punishment_name}\n\n"
            "هر تخلف تأییدشده از قابلیت‌های نظارتی "
            "می‌تواند یک اخطار ثبت کند.\n"
            "قفل رسانه‌ها شامل اخطار نمی‌شود.\n\n"
            "با رسیدن کاربر به سقف تعیین‌شده، "
            "مجازات انتخاب‌شده اجرا می‌شود."
        )

        rows = [
            [
                self._button(
                    (
                        "🔴 خاموش کردن اخطارها"
                        if enabled
                        else "🟢 روشن کردن اخطارها"
                    ),
                    self._callback_data("warnings_toggle"),
                ),
            ],
            [
                self._button(
                    "➖ کاهش سقف",
                    self._callback_data("warnings_change", -1),
                ),
                self._button(
                    "➕ افزایش سقف",
                    self._callback_data("warnings_change", 1),
                ),
            ],
            [
                self._button(
                    (
                        "🔨 مجازات: اخراج"
                        + (" ✅" if punishment == "kick" else "")
                    ),
                    self._callback_data(
                        "punishment",
                        "kick",
                    ),
                ),
            ],
            [
                self._button(
                    (
                        "⛔ مجازات: بن"
                        + (" ✅" if punishment == "ban" else "")
                    ),
                    self._callback_data(
                        "punishment",
                        "ban",
                    ),
                ),
            ],
            [
                self._button(
                    (
                        "🔇 مجازات: سکوت"
                        + (" ✅" if punishment == "mute" else "")
                    ),
                    self._callback_data(
                        "punishment",
                        "mute",
                    ),
                ),
            ],
            [
                self._button(
                    "📖 راهنمای سیستم اخطار",
                    self._callback_data("warnings_help"),
                ),
            ],
            [
                self._button(
                    "↩️ برگشت",
                    self._callback_data("back", PAGE_MAIN),
                ),
            ],
        ]

        return text, self._keyboard(rows)

    def _warnings_help_page(self):
        text = (
            "📖 راهنمای سیستم اخطار\n\n"
            "۱. فقط تخلف تأییدشده از قابلیت‌های فعال "
            "می‌تواند اخطار ایجاد کند.\n\n"
            "۲. با رسیدن اخطارها به سقف تعیین‌شده، "
            "مجازات انتخابی اجرا می‌شود.\n\n"
            "۳. قفل عکس، ویدیو، فایل و سایر رسانه‌ها "
            "به‌تنهایی اخطار ایجاد نمی‌کند.\n\n"
            "۴. اگر اخطارها خاموش باشند، ثبت اخطار جدید "
            "نباید انجام شود.\n\n"
            "۵. برای اجرای مجازات، ربات باید مجوز لازم "
            "را در گروه داشته باشد."
        )

        keyboard = self._keyboard([
            [
                self._button(
                    "↩️ برگشت",
                    self._callback_data(
                        "back",
                        PAGE_WARNINGS,
                    ),
                ),
            ],
        ])

        return text, keyboard

    # ======================================
    #             Page Dispatcher
    # ======================================

    def _render_page(self, page, chat_id):
        """ساخت متن و دکمه‌های صفحه موردنظر."""

        if page == PAGE_MAIN:
            return self._main_page()

        if page == PAGE_LOCKS:
            return self._locks_page(chat_id)

        if page == PAGE_COMMANDS:
            return self._commands_page(chat_id)

        if page == PAGE_PROFANITY:
            return self._profanity_page(chat_id)

        if page == PAGE_WARNINGS:
            return self._warnings_page(chat_id)

        if page == "warnings_help":
            return self._warnings_help_page()

        return self._main_page()

    # ======================================
    #                Open Panel
    # ======================================

    def open_panel(self, message):
        """
        باز کردن پنل در همان پیام ورودی.

        برای اتصال در main.py باید بعداً یک پیام پنل
        با sendMessage ساخته شود و شناسه پیام آن
        برای callbackها در دسترس باشد.
        """

        if not isinstance(message, dict):
            return False

        chat = message.get("chat") or {}

        if not self._is_group_chat(chat):
            return False

        if not self._authorized(message):
            return False

        chat_id = chat.get("id")

        if chat_id is None:
            return False

        text, keyboard = self._main_page()

        # اگر message_id متعلق به پیام فرمان باشد،
        # تلاش برای ویرایش آن انجام می‌شود.
        message_id = message.get("message_id")

        if message_id is None:
            return False

        return self._edit_panel(
            chat_id,
            message_id,
            text,
            keyboard,
        )

    # ======================================
    #              Callback Entry
    # ======================================

    def handle_callback_query(self, callback_query):
        """پردازش امن کلیک‌های پنل."""

        if not isinstance(callback_query, dict):
            return False

        data = str(
            callback_query.get("data", "")
        )

        if not data.startswith(CALLBACK_PREFIX + ":"):
            return False

        callback_id = callback_query.get("id")
        user = callback_query.get("from") or {}
        callback_message = (
            callback_query.get("message") or {}
        )

        chat = callback_message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = callback_message.get("message_id")

        if (
            user.get("id") is None
            or chat_id is None
            or message_id is None
            or not self._is_group_chat(chat)
        ):
            self._answer_callback(
                callback_id,
                "❌ اطلاعات پنل معتبر نیست.",
                True,
            )
            return True

        # بازسازی پیام جهت بررسی مجدد دسترسی
        auth_message = {
            "chat": chat,
            "from": user,
            "message_id": message_id,
        }

        if not self._authorized(auth_message):
            self._answer_callback(
                callback_id,
                "⛔ فقط ادمین گروه یا ناظر مجاز می‌تواند پنل را کنترل کند.",
                True,
            )
            return True

        parts = data.split(":")

        if len(parts) < 2:
            self._answer_callback(
                callback_id,
                "❌ دکمه نامعتبر است.",
                True,
            )
            return True

        action = parts[1]
        args = parts[2:]

        try:
            page = PAGE_MAIN
            alert_text = "✅ انجام شد."

            # ----------------------------------
            # Close panel
            # ----------------------------------

            if action == "close":
                self._edit_panel(
                    chat_id,
                    message_id,
                    "پنل مدیریت بسته شد.",
                    None,
                )

                self._answer_callback(
                    callback_id,
                    "پنل بسته شد.",
                    False,
                )
                return True

            # ----------------------------------
            # Navigate to page
            # ----------------------------------

            if action == "page":
                if not args:
                    raise ValueError("صفحه مشخص نشده است.")

                page = args[0]

                if page not in {
                    PAGE_MAIN,
                    PAGE_LOCKS,
                    PAGE_COMMANDS,
                    PAGE_WARNINGS,
                    PAGE_PROFANITY,
                }:
                    raise ValueError("صفحه معتبر نیست.")

            # ----------------------------------
            # Back navigation
            # ----------------------------------

            elif action == "back":
                if not args:
                    page = PAGE_MAIN
                else:
                    page = args[0]

                if page not in {
                    PAGE_MAIN,
                    PAGE_LOCKS,
                    PAGE_COMMANDS,
                    PAGE_WARNINGS,
                    PAGE_PROFANITY,
                    "warnings_help",
                }:
                    page = PAGE_MAIN

            # ----------------------------------
            # Toggle media lock
            # ----------------------------------

            elif action == "lock":
                if not args:
                    raise ValueError("نوع قفل مشخص نشده است.")

                lock_key = args[0]

                if lock_key not in LOCKS:
                    raise ValueError("نوع قفل معتبر نیست.")

                current = LocksFeature.get_lock_status(
                    chat_id,
                    lock_key,
                )

                if current is None:
                    raise RuntimeError(
                        "خواندن وضعیت قفل ناموفق بود."
                    )

                saved = LocksFeature.set_lock_status(
                    chat_id,
                    lock_key,
                    not current,
                )

                if not saved:
                    raise RuntimeError(
                        "ذخیره وضعیت قفل ناموفق بود."
                    )

                page = PAGE_LOCKS

            # ----------------------------------
            # Toggle anti-spam
            # ----------------------------------

            elif action == "spam":
                group = get_group(chat_id) or {}

                current = bool(
                    group.get("anti_spam_enabled", 0)
                )

                from database import set_group_feature

                saved = set_group_feature(
                    chat_id,
                    "anti_spam_enabled",
                    not current,
                )

                if not saved:
                    raise RuntimeError(
                        "ذخیره تنظیم ضداسپم ناموفق بود."
                    )

                page = PAGE_COMMANDS

            # ----------------------------------
            # Toggle profanity
            # ----------------------------------

            elif action == "profanity_toggle":
                settings = (
                    self.profanity_feature.get_settings(
                        chat_id
                    )
                )

                self.profanity_feature.save_settings(
                    chat_id,
                    enabled=not bool(
                        settings.get("enabled")
                    ),
                )

                page = (
                    PAGE_PROFANITY
                    if settings.get("enabled")
                    else PAGE_COMMANDS
                )

            # ----------------------------------
            # Change profanity level
            # ----------------------------------

            elif action == "profanity_level":
                if not args:
                    raise ValueError("درجه ضد فحش مشخص نشده است.")

                level = args[0]

                if level not in PROFANITY_LEVELS:
                    raise ValueError("درجه ضد فحش معتبر نیست.")

                settings = (
                    self.profanity_feature.get_settings(
                        chat_id
                    )
                )

                if not settings.get("enabled"):
                    raise ValueError(
                        "ابتدا ضد فحش را روشن کن."
                    )

                self.profanity_feature.save_settings(
                    chat_id,
                    level=level,
                )

                page = PAGE_PROFANITY

            # ----------------------------------
            # Toggle whisper
            # ----------------------------------

            elif action == "whisper":
                current = self.get_whisper_enabled(
                    chat_id
                )

                self.set_whisper_enabled(
                    chat_id,
                    not current,
                )

                page = PAGE_COMMANDS

            # ----------------------------------
            # Toggle warnings
            # ----------------------------------

            elif action == "warnings_toggle":
                settings = (
                    self.warnings_feature.get_settings(
                        chat_id
                    )
                )

                self.warnings_feature.set_enabled(
                    chat_id,
                    not bool(settings.get("enabled", True)),
                )

                page = PAGE_WARNINGS

            # ----------------------------------
            # Change warning limit
            # ----------------------------------

            elif action == "warnings_change":
                if not args:
                    raise ValueError("مقدار تغییر مشخص نشده است.")

                delta = int(args[0])

                if delta not in {-1, 1}:
                    raise ValueError("مقدار تغییر معتبر نیست.")

                self.warnings_feature.change_max_warnings(
                    chat_id,
                    delta,
                )

                page = PAGE_WARNINGS

            # ----------------------------------
            # Select punishment
            # ----------------------------------

            elif action == "punishment":
                if not args:
                    raise ValueError("نوع مجازات مشخص نشده است.")

                punishment = args[0]

                if punishment not in PUNISHMENTS:
                    raise ValueError("نوع مجازات معتبر نیست.")

                self.warnings_feature.set_punishment(
                    chat_id,
                    punishment,
                )

                page = PAGE_WARNINGS

            # ----------------------------------
            # Warning help
            # ----------------------------------

            elif action == "warnings_help":
                page = "warnings_help"

            else:
                self._answer_callback(
                    callback_id,
                    "❌ این دکمه شناخته نشد.",
                    True,
                )
                return True

            # ----------------------------------
            # Render current page
            # ----------------------------------

            text, keyboard = self._render_page(
                page,
                chat_id,
            )

            edited = self._edit_panel(
                chat_id,
                message_id,
                text,
                keyboard,
            )

            if not edited:
                alert_text = (
                    "❌ بروزرسانی پنل ناموفق بود. "
                    "لطفاً دوباره تلاش کن."
                )

            self._answer_callback(
                callback_id,
                alert_text,
                not edited,
            )

        except (ValueError, TypeError) as error:
            logger.warning(
                "Invalid panel callback: %s",
                error,
            )

            self._answer_callback(
                callback_id,
                f"❌ {error}",
                True,
            )

        except Exception:
            logger.exception(
                "Panel callback failed | action=%s",
                action,
            )

            self._answer_callback(
                callback_id,
                "❌ عملیات ناموفق بود؛ تنظیمات را بررسی کن.",
                True,
            )

        return True
