# ==========================================
#              NevoBot | Whisper
# ==========================================

import logging
import random
import re
import unicodedata

from database import get_connection


logger = logging.getLogger("NevoBot.Whisper")

MAX_WHISPER_LENGTH = 3000
WHISPER_CHUNK_SIZE = 100


# ==========================================
#             Text Normalization
# ==========================================

def normalize_whisper_text(value):
    """
    یکسان‌سازی متن برای تشخیص مطمئن‌تر دستور نجوا.

    تفاوت حروف عربی و فارسی و فاصله‌ها
    در تشخیص دستور مدیریت می‌شوند.
    """

    value = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    value = (
        value
        .replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .replace("\u200d", "")
        .replace("\u200e", "")
        .replace("\u200f", "")
        .replace("\ufeff", "")
    )

    return " ".join(
        value.lower().split()
    ).strip()


def parse_whisper_command(text):
    """
    تشخیص دستور نجوا.

    خروجی:
        None:
            پیام دستور نجوا نیست.

        str:
            متن بعد از دستور نجوا.
    """

    if not isinstance(text, str):
        return None

    normalized = normalize_whisper_text(text)

    if normalized == "نجوا":
        return ""

    if normalized.startswith("نجوا "):
        # متن اصلی را نگه می‌داریم تا شکل نوشتن
        # و محتوای پیام بی‌دلیل تغییر نکند.
        original = text.strip()

        match = re.match(
            r"^\s*نجوا(?:\s+)(.*)$",
            original,
            flags=re.DOTALL,
        )

        if match:
            return match.group(1).strip()

        # پشتیبان برای فاصله‌گذاری‌های غیرمعمول
        parts = original.split(maxsplit=1)

        if len(parts) == 2:
            return parts[1].strip()

    return None


# ==========================================
#                 Database
# ==========================================

def init_whisper():
    """ساخت جدول نجوا و سازگاری با دیتابیس قبلی."""

    with get_connection() as connection:

        connection.execute("""
            CREATE TABLE IF NOT EXISTS whispers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                chat_id TEXT NOT NULL,

                group_message_id TEXT NOT NULL,

                sender_id TEXT NOT NULL,

                sender_name TEXT NOT NULL DEFAULT '',

                recipient_id TEXT NOT NULL,

                text TEXT NOT NULL,

                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                opened_at TEXT
            )
        """)

        # سازگاری با دیتابیس‌های قدیمی
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()
        }

        if "sender_name" not in columns:
            connection.execute("""
                ALTER TABLE whispers
                ADD COLUMN sender_name TEXT NOT NULL DEFAULT ''
            """)

        if "opened_at" not in columns:
            connection.execute("""
                ALTER TABLE whispers
                ADD COLUMN opened_at TEXT
            """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_whispers_recipient
            ON whispers(recipient_id)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_whispers_chat
            ON whispers(chat_id)
        """)

        connection.commit()


def save_whisper(
    chat_id,
    group_message_id,
    sender_id,
    recipient_id,
    text,
    sender_name="",
):
    """ذخیره متن نجوا و مشخصات فرستنده."""

    text = str(text or "").strip()
    sender_name = str(sender_name or "").strip()

    if chat_id is None:
        raise ValueError(
            "شناسه گروه معتبر نیست."
        )

    if group_message_id is None:
        raise ValueError(
            "شناسه پیام معتبر نیست."
        )

    if sender_id is None or recipient_id is None:
        raise ValueError(
            "شناسه فرستنده یا مخاطب معتبر نیست."
        )

    if not text:
        raise ValueError(
            "متن نجوا خالی است."
        )

    if len(text) > MAX_WHISPER_LENGTH:
        raise ValueError(
            f"متن نجوا نباید بیشتر از "
            f"{MAX_WHISPER_LENGTH} نویسه باشد."
        )

    if not sender_name:
        sender_name = "کاربر"

    with get_connection() as connection:

        cursor = connection.execute("""
            INSERT INTO whispers (
                chat_id,
                group_message_id,
                sender_id,
                sender_name,
                recipient_id,
                text
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(chat_id),
            str(group_message_id),
            str(sender_id),
            sender_name,
            str(recipient_id),
            text,
        ))

        connection.commit()

        return cursor.lastrowid


def get_whisper(whisper_id):
    """دریافت اطلاعات یک نجوا."""

    try:
        whisper_id = int(whisper_id)

    except (TypeError, ValueError):
        return None

    if whisper_id <= 0:
        return None

    with get_connection() as connection:

        row = connection.execute("""
            SELECT *
            FROM whispers
            WHERE id = ?
            LIMIT 1
        """, (
            whisper_id,
        )).fetchone()

        return dict(row) if row else None


def open_whisper(whisper_id, recipient_id):
    """
    دریافت نجوا فقط برای مخاطب اصلی.

    بررسی مخاطب در خود کوئری دیتابیس انجام می‌شود.
    """

    try:
        whisper_id = int(whisper_id)

    except (TypeError, ValueError):
        return None

    if whisper_id <= 0 or recipient_id is None:
        return None

    with get_connection() as connection:

        row = connection.execute("""
            SELECT *
            FROM whispers
            WHERE id = ?
              AND recipient_id = ?
            LIMIT 1
        """, (
            whisper_id,
            str(recipient_id),
        )).fetchone()

        return dict(row) if row else None


def mark_whisper_opened(
    whisper_id,
    recipient_id,
):
    """ثبت زمان اولین بازشدن نجوا."""

    try:
        whisper_id = int(whisper_id)

    except (TypeError, ValueError):
        return False

    if whisper_id <= 0 or recipient_id is None:
        return False

    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE whispers
            SET opened_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND recipient_id = ?
              AND opened_at IS NULL
        """, (
            whisper_id,
            str(recipient_id),
        ))

        connection.commit()

        return cursor.rowcount > 0


# ==========================================
#              Whisper Feature
# ==========================================

class WhisperFeature:
    """مدیریت قابلیت نجوا در گروه‌های NevoBot."""

    def __init__(
        self,
        api,
        send_message,
        delete_message,
        answer_callback,
    ):
        self.api = api
        self.send_message = send_message
        self.delete_message = delete_message
        self.answer_callback = answer_callback

    # ======================================
    #                Database
    # ======================================

    @staticmethod
    def init_database():
        """راه‌اندازی دیتابیس اختصاصی نجوا."""

        init_whisper()

    # ======================================
    #                 Utilities
    # ======================================

    @staticmethod
    def get_display_name(user):
        """ساخت نام نمایشی فرستنده."""

        if not isinstance(user, dict):
            return "کاربر"

        first_name = str(
            user.get("first_name") or ""
        ).strip()

        last_name = str(
            user.get("last_name") or ""
        ).strip()

        full_name = (
            f"{first_name} {last_name}"
        ).strip()

        if full_name:
            return full_name

        username = str(
            user.get("username") or ""
        ).strip()

        if username:
            return f"@{username}"

        return "کاربر"

    @staticmethod
    def _is_group(message):
        """بررسی گروهی بودن پیام."""

        chat = message.get("chat") or {}

        return chat.get("type") in {
            "group",
            "supergroup",
        }

    def _send_error(self, chat_id, text):
        """ارسال پیام خطا با مدیریت استثنا."""

        try:
            return self.send_message(
                chat_id,
                text,
            )

        except Exception:
            logger.exception(
                "Could not send whisper message | chat=%s",
                chat_id,
            )

            return None

    def discard_whisper(self, whisper_id):
        """حذف نجوا در صورت شکست عملیات ارسال."""

        try:
            with get_connection() as connection:

                connection.execute(
                    """
                    DELETE FROM whispers
                    WHERE id = ?
                    """,
                    (
                        int(whisper_id),
                    ),
                )

                connection.commit()

        except Exception:
            logger.exception(
                "Could not discard whisper | id=%s",
                whisper_id,
            )

    # ======================================
    #             Command Detection
    # ======================================

    @staticmethod
    def is_whisper_command(text):
        """بررسی اینکه متن، دستور نجواست یا خیر."""

        return (
            parse_whisper_command(text)
            is not None
        )

    # ======================================
    #              Sending Whispers
    # ======================================

    def handle_message(self, message):
        """
        پردازش دستور نجوا.

        روش استفاده:
        ۱. روی پیام مخاطب ریپلای کن.
        ۲. بنویس: نجوا سلام!
        ۳. پیام اصلی دستور حذف می‌شود.
        ۴. دکمه بازکردن نجوا در گروه ارسال می‌شود.

        فعال یا غیرفعال بودن قابلیت در main.py
        و با تنظیمات پنل بررسی می‌شود.
        """

        if not isinstance(message, dict):
            return False

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        chat_type = chat.get("type")
        sender_id = sender.get("id")
        message_id = message.get("message_id")

        if chat_type not in {
            "group",
            "supergroup",
        }:
            return False

        if sender_id is None or message_id is None:
            return False

        # ربات‌ها نباید دستور نجوا را اجرا کنند.
        if sender.get("is_bot"):
            return False

        text = message.get("text", "")

        whisper_text = parse_whisper_command(text)

        # پیام مربوط به نجوا نیست.
        if whisper_text is None:
            return False

        # ----------------------------------
        #             Validate Text
        # ----------------------------------

        if not whisper_text:
            replied_message = (
                message.get("reply_to_message") or {}
            )

            if not replied_message:
                self._send_error(
                    chat_id,
                    (
                        "❓ برای نجوا باید روی پیام کاربر هدف "
                        "ریپلای کنی.\n\n"
                        "بعد بنویس:\n"
                        "نجوا متن پیام"
                    ),
                )

            else:
                self._send_error(
                    chat_id,
                    (
                        "✍️ متن نجوا رو هم بعد از دستور بنویس.\n"
                        "مثال: نجوا سلام!"
                    ),
                )

            return True

        if len(whisper_text) > MAX_WHISPER_LENGTH:
            self._send_error(
                chat_id,
                (
                    "❌ متن نجوا بیش از حد طولانی است.\n"
                    f"حداکثر طول مجاز: "
                    f"{MAX_WHISPER_LENGTH} نویسه."
                ),
            )

            return True

        # ----------------------------------
        #             Validate Reply
        # ----------------------------------

        replied_message = (
            message.get("reply_to_message") or {}
        )

        if not replied_message:
            self._send_error(
                chat_id,
                (
                    "❓ برای ارسال نجوا باید روی پیام "
                    "مخاطب ریپلای کنی.\n\n"
                    "سپس بنویس:\n"
                    "نجوا متن پیام"
                ),
            )

            return True

        recipient = (
            replied_message.get("from") or {}
        )

        recipient_id = recipient.get("id")

        if recipient_id is None:
            self._send_error(
                chat_id,
                "❌ فقط می‌تونی برای یک کاربر واقعی نجوا بفرستی.",
            )

            return True

        if str(recipient_id) == str(sender_id):
            self._send_error(
                chat_id,
                "❌ نمی‌تونی برای خودت نجوا بفرستی!",
            )

            return True

        if recipient.get("is_bot"):
            self._send_error(
                chat_id,
                "❌ فقط می‌تونی برای یک کاربر واقعی نجوا بفرستی.",
            )

            return True

        # ----------------------------------
        #             Save Whisper
        # ----------------------------------

        sender_name = self.get_display_name(
            sender
        )

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
            self._send_error(
                chat_id,
                f"❌ {error}",
            )

            return True

        except Exception:
            logger.exception(
                "Could not save whisper | chat=%s | sender=%s",
                chat_id,
                sender_id,
            )

            self._send_error(
                chat_id,
                (
                    "❌ ذخیره نجوا ناموفق بود؛ "
                    "دوباره تلاش کن."
                ),
            )

            return True

        # ----------------------------------
        #             Delete Original
        # ----------------------------------

        try:
            deleted = self.delete_message(
                chat_id,
                message_id,
            )

        except Exception:
            logger.exception(
                "Could not delete whisper command | "
                "chat=%s | message=%s",
                chat_id,
                message_id,
            )

            deleted = False

        if not deleted:
            self.discard_whisper(
                whisper_id
            )

            self._send_error(
                chat_id,
                (
                    "❌ نتونستم پیام نجوا رو حذف کنم.\n"
                    "مجوز حذف پیام‌های گروه رو بررسی کن."
                ),
            )

            return True

        # ----------------------------------
        #            Whisper Keyboard
        # ----------------------------------

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "دیدم 👀",
                        "callback_data": (
                            f"whisper_open:{whisper_id}:0"
                        ),
                    }
                ]
            ]
        }

        # منشن مخفی مخاطب
        notification_text = (
            f"🤫 [👇](uid:{recipient_id}) "
            "نجوا داری! بازش کن"
        )

        # ----------------------------------
        #          Send Notification
        # ----------------------------------

        try:
            notification = self.send_message(
                chat_id,
                notification_text,
                keyboard,
            )

        except Exception:
            logger.exception(
                "Could not send whisper notification | "
                "chat=%s | whisper=%s",
                chat_id,
                whisper_id,
            )

            notification = None

        if (
            not isinstance(notification, dict)
            or not notification.get("ok")
        ):
            self.discard_whisper(
                whisper_id
            )

            logger.error(
                "Whisper notification failed | "
                "chat=%s | whisper=%s | response=%s",
                chat_id,
                whisper_id,
                notification,
            )

            self._send_error(
                chat_id,
                "❌ ارسال دکمه نجوا ناموفق بود.",
            )

            return True

        logger.info(
            "Whisper created | id=%s | chat=%s | "
            "sender=%s | recipient=%s",
            whisper_id,
            chat_id,
            sender_id,
            recipient_id,
        )

        return True

    # ======================================
    #              Callback Helpers
    # ======================================

    def _answer(
        self,
        callback_id,
        text,
        show_alert=True,
    ):
        """پاسخ امن به کلیک دکمه."""

        if not callback_id:
            return None

        try:
            return self.answer_callback(
                callback_id,
                str(text or "")[:180],
                show_alert=show_alert,
            )

        except Exception:
            logger.exception(
                "Could not answer whisper callback."
            )

            return None

    # ======================================
    #              Opening Whispers
    # ======================================

    def handle_callback_query(self, callback_query):
        """
        بازکردن نجوا فقط برای مخاطب اصلی.

        قالب callback:
            whisper_open:<whisper_id>:<page>
        """

        if not isinstance(callback_query, dict):
            logger.warning(
                "Invalid whisper callback query."
            )

            return

        callback_id = callback_query.get("id")

        callback_data = str(
            callback_query.get("data", "")
        )

        # این ماژول فقط callbackهای نجوا را پردازش می‌کند.
        if not callback_data.startswith(
            "whisper_open:"
        ):
            return

        user = callback_query.get("from") or {}
        user_id = user.get("id")

        if not callback_id or user_id is None:
            logger.warning(
                "Whisper callback missing user or callback ID."
            )

            return

        # ----------------------------------
        #          Validate Callback
        # ----------------------------------

        try:
            parts = callback_data.split(":")

            if len(parts) != 3:
                raise ValueError(
                    "Invalid callback format"
                )

            whisper_id = int(parts[1])
            page = int(parts[2])

            if whisper_id <= 0 or page < 0:
                raise ValueError(
                    "Invalid whisper ID or page"
                )

        except (ValueError, IndexError):
            self._answer(
                callback_id,
                "این دکمه معتبر نیست.",
                True,
            )

            return

        # ----------------------------------
        #         Verify Recipient
        # ----------------------------------

        try:
            whisper = open_whisper(
                whisper_id,
                user_id,
            )

        except Exception:
            logger.exception(
                "Could not load whisper | id=%s",
                whisper_id,
            )

            self._answer(
                callback_id,
                (
                    "فعلاً بازکردن نجوا ممکن نیست؛ "
                    "دوباره تلاش کن."
                ),
                True,
            )

            return

        if not whisper:
            self._answer(
                callback_id,
                (
                    "این نجوا برای تو نیست "
                    "یا دیگر در دسترس نیست."
                ),
                True,
            )

            logger.info(
                "Whisper access denied | "
                "whisper=%s | user=%s",
                whisper_id,
                user_id,
            )

            return

        # ----------------------------------
        #           Split Whisper
        # ----------------------------------

        whisper_text = str(
            whisper.get("text") or ""
        )

        sender_name = str(
            whisper.get("sender_name") or "کاربر"
        ).strip()[:35]

        chunks = [
            whisper_text[index:index + WHISPER_CHUNK_SIZE]
            for index in range(
                0,
                len(whisper_text),
                WHISPER_CHUNK_SIZE,
            )
        ]

        if not chunks:
            chunks = ["متن خالی است."]

        if page >= len(chunks):
            self._answer(
                callback_id,
                "این نجوا تمام شده است.",
                True,
            )

            return

        alert_text = (
            f"نجوا از طرف {sender_name} "
            f"({page + 1}/{len(chunks)})\n\n"
            f"{chunks[page]}"
        )

        # ----------------------------------
        #            Show Whisper
        # ----------------------------------

        result = self._answer(
            callback_id,
            alert_text,
            True,
        )

        if (
            not isinstance(result, dict)
            or not result.get("ok")
        ):
            logger.warning(
                "Whisper popup failed | "
                "user=%s | whisper=%s",
                user_id,
                whisper_id,
            )

            return

        # ----------------------------------
        #           Mark As Opened
        # ----------------------------------

        try:
            mark_whisper_opened(
                whisper_id,
                user_id,
            )

        except Exception:
            logger.exception(
                "Could not mark whisper as opened | id=%s",
                whisper_id,
            )

        # ----------------------------------
        #            Callback Message
        # ----------------------------------

        callback_message = (
            callback_query.get("message") or {}
        )

        callback_chat = (
            callback_message.get("chat") or {}
        )

        callback_chat_id = callback_chat.get("id")

        callback_message_id = (
            callback_message.get("message_id")
        )

        if (
            callback_chat_id is None
            or callback_message_id is None
        ):
            logger.warning(
                "Whisper callback message missing | id=%s",
                whisper_id,
            )

            return

        # ----------------------------------
        #             Next Page
        # ----------------------------------

        if page + 1 < len(chunks):
            next_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": (
                                f"📩 ادامه نجوا "
                                f"({page + 2}/{len(chunks)})"
                            ),
                            "callback_data": (
                                f"whisper_open:"
                                f"{whisper_id}:"
                                f"{page + 1}"
                            ),
                        }
                    ]
                ]
            }

            try:
                edit_result = self.api(
                    "editMessageReplyMarkup",
                    {
                        "chat_id": callback_chat_id,
                        "message_id": callback_message_id,
                        "reply_markup": next_keyboard,
                    },
                )

            except Exception:
                logger.exception(
                    "Could not update whisper keyboard | id=%s",
                    whisper_id,
                )

                return

        else:
            # پس از نمایش آخرین بخش، اعلان قبلی
            # به یک ایموجی تبدیل می‌شود.
            final_emoji = random.choice([
                "👍",
                "✅",
                "👌",
            ])

            try:
                edit_result = self.api(
                    "editMessageText",
                    {
                        "chat_id": callback_chat_id,
                        "message_id": callback_message_id,
                        "text": final_emoji,
                        "reply_markup": {
                            "inline_keyboard": [],
                        },
                    },
                )

            except Exception:
                logger.exception(
                    "Could not finalize whisper notification | id=%s",
                    whisper_id,
                )

                return

        # ----------------------------------
        #             Log Result
        # ----------------------------------

        if (
            not isinstance(edit_result, dict)
            or not edit_result.get("ok")
        ):
            logger.warning(
                "Could not update whisper notification | "
                "id=%s | response=%s",
                whisper_id,
                edit_result,
        )
