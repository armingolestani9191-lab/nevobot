# features/whisper.py

import logging
import random

from database import get_connection


logger = logging.getLogger("NevoBot.Whisper")

MAX_WHISPER_LENGTH = 3000


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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                opened_at TEXT
            )
        """)

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

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_whispers_recipient
            ON whispers(recipient_id)
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

    if not text:
        raise ValueError("متن نجوا خالی است.")

    if len(text) > MAX_WHISPER_LENGTH:
        raise ValueError("متن نجوا بیش از حد طولانی است.")

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

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM whispers
            WHERE id = ?
        """, (int(whisper_id),)).fetchone()

        return dict(row) if row else None


def open_whisper(whisper_id, recipient_id):
    """دریافت نجوا فقط برای مخاطب اصلی."""

    with get_connection() as connection:
        row = connection.execute("""
            SELECT *
            FROM whispers
            WHERE id = ?
              AND recipient_id = ?
        """, (
            int(whisper_id),
            str(recipient_id),
        )).fetchone()

        return dict(row) if row else None


def mark_whisper_opened(whisper_id, recipient_id):
    """ثبت زمان اولین بازشدن نجوا."""

    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE whispers
            SET opened_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND recipient_id = ?
              AND opened_at IS NULL
        """, (
            int(whisper_id),
            str(recipient_id),
        ))

        connection.commit()
        return cursor.rowcount > 0


# ==========================================
#               Whisper Feature
# ==========================================

class WhisperFeature:
    """تمام منطق قابلیت نجوا در یک فایل مستقل."""

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

    @staticmethod
    def init_database():
        """راه‌اندازی دیتابیس اختصاصی نجوا."""

        init_whisper()

    # ==========================================
    #                 Utilities
    # ==========================================

    @staticmethod
    def get_display_name(user):
        """ساخت نام نمایشی فرستنده نجوا."""

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

    # ==========================================
    #              Sending Whispers
    # ==========================================

    def handle_message(self, message):
        """
        دستور نجوا را بررسی می‌کند.

        روش استفاده:
        روی پیام مخاطب ریپلای کن و بنویس:
        نجوا متن پیام
        """

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        chat_type = chat.get("type")
        sender_id = sender.get("id")
        message_id = message.get("message_id")
        text = message.get("text", "")

        if chat_type not in {
            "group",
            "supergroup",
        }:
            return False

        if not isinstance(text, str):
            return False

        text = text.strip()

        if (
            text != "نجوا"
            and not text.startswith("نجوا ")
        ):
            return False

        whisper_text = text[len("نجوا"):].strip()

        replied_message = (
            message.get("reply_to_message") or {}
        )

        # بررسی متن و ریپلای
        if not whisper_text:
            if not replied_message:
                self.send_message(
                    chat_id,
                    "❓ برای نجوا باید روی پیام کاربر هدف ریپلای کنی.\n"
                    "بعد بنویس: نجوا متن پیام",
                )
            else:
                self.send_message(
                    chat_id,
                    "✍️ متن نجوا رو هم بعد از دستور بنویس.\n"
                    "مثال: نجوا سلام!",
                )

            return True

        recipient = replied_message.get("from") or {}
        recipient_id = recipient.get("id")

        # بررسی کاربر هدف
        if recipient_id is None:
            self.send_message(
                chat_id,
                "❌ فقط می‌تونی برای یک کاربر واقعی نجوا بفرستی.",
            )
            return True

        if str(recipient_id) == str(sender_id):
            self.send_message(
                chat_id,
                "❌ نمی‌تونی برای خودت نجوا بفرستی!",
            )
            return True

        if recipient.get("is_bot"):
            self.send_message(
                chat_id,
                "❌ فقط می‌تونی برای یک کاربر واقعی نجوا بفرستی.",
            )
            return True

        sender_name = self.get_display_name(sender)

        # ذخیره نجوا
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
                "❌ ذخیره نجوا ناموفق بود؛ "
                "دوباره تلاش کن.",
            )
            return True

        # حذف پیام اصلی برای باقی نماندن متن نجوا در گروه
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

        # دکمه باز کردن نجوا
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

        # اعلان با منشن مخفی مخاطب
        notification_text = (
            f"🤫 [👇](uid:{recipient_id}) "
            f"نجوا داری! بازش کن"
        )

        notification = self.send_message(
            chat_id,
            notification_text,
            keyboard,
        )

        if (
            not notification
            or not notification.get("ok")
        ):
            self.discard_whisper(whisper_id)

            logger.error(
                "Could not post whisper button in group %s",
                chat_id,
            )

            self.send_message(
                chat_id,
                "❌ ارسال دکمه نجوا در گروه ناموفق بود.",
            )
            return True

        logger.info(
            "Whisper %s posted in group %s from %s to %s",
            whisper_id,
            chat_id,
            sender_id,
            recipient_id,
        )

        return True

    # ==========================================
    #                Opening Whispers
    # ==========================================

    def handle_callback_query(self, callback_query):
        """بازکردن نجوا و نمایش متن فقط به مخاطب اصلی."""

        if not isinstance(callback_query, dict):
            logger.warning("Invalid callback query received.")
            return

        callback_id = callback_query.get("id")

        callback_data = str(
            callback_query.get("data", "")
        )

        user = callback_query.get("from") or {}
        user_id = user.get("id")

        if not callback_id or user_id is None:
            logger.warning(
                "Callback missing ID or user ID."
            )
            return

        # فقط دکمه‌های مربوط به نجوا
        if not callback_data.startswith("whisper_open:"):
            return

        # قالب دکمه:
        # whisper_open:<whisper_id>:<page>
        try:
            parts = callback_data.split(":")

            if len(parts) != 3:
                raise ValueError("Invalid callback format")

            whisper_id = int(parts[1])
            page = int(parts[2])

            if whisper_id <= 0 or page < 0:
                raise ValueError("Invalid whisper ID or page")

        except (ValueError, IndexError):
            self.answer_callback(
                callback_id,
                "این دکمه معتبر نیست.",
                show_alert=True,
            )
            return

        # دریافت نجوا فقط برای مخاطب اصلی
        try:
            whisper = open_whisper(
                whisper_id,
                user_id,
            )

        except Exception:
            logger.exception(
                "Could not load whisper %s",
                whisper_id,
            )

            self.answer_callback(
                callback_id,
                "فعلاً باز کردن نجوا ممکن نیست؛ دوباره تلاش کن.",
                show_alert=True,
            )
            return

        if not whisper:
            self.answer_callback(
                callback_id,
                "این نجوا برای تو نیست یا دیگر در دسترس نیست.",
                show_alert=True,
            )

            logger.info(
                "Whisper access denied | whisper_id=%s | user_id=%s",
                whisper_id,
                user_id,
            )
            return

        whisper_text = str(
            whisper.get("text") or ""
        )

        sender_name = str(
            whisper.get("sender_name") or "کاربر"
        )[:35]

        # تقسیم متن به بخش‌های قابل نمایش
        chunk_size = 100

        chunks = [
            whisper_text[index:index + chunk_size]
            for index in range(
                0,
                len(whisper_text),
                chunk_size,
            )
        ] or ["(متن خالی است)"]

        if page >= len(chunks):
            self.answer_callback(
                callback_id,
                "این نجوا تمام شده است.",
                show_alert=True,
            )
            return

        alert_text = (
            f"نجوا از طرف {sender_name} "
            f"({page + 1}/{len(chunks)})\n\n"
            f"{chunks[page]}"
        )

        # نمایش متن در پنجره کلیک‌کننده
        result = self.answer_callback(
            callback_id,
            alert_text,
            show_alert=True,
        )

        if not result or not result.get("ok"):
            logger.warning(
                "Could not display whisper popup | "
                "user_id=%s | whisper_id=%s",
                user_id,
                whisper_id,
            )
            return

        # ثبت اولین بازشدن نجوا
        try:
            mark_whisper_opened(
                whisper_id,
                user_id,
            )

        except Exception:
            logger.exception(
                "Could not mark whisper as opened | whisper_id=%s",
                whisper_id,
            )

        callback_message = (
            callback_query.get("message") or {}
        )

        callback_chat = (
            callback_message.get("chat") or {}
        )

        callback_chat_id = callback_chat.get("id")

        callback_message_id = callback_message.get(
            "message_id"
        )

        if (
            callback_chat_id is None
            or callback_message_id is None
        ):
            logger.warning(
                "Callback has no accessible message | whisper_id=%s",
                whisper_id,
            )
            return

        # نمایش دکمه ادامه برای نجواهای چندبخشی
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

            edit_result = self.api(
                "editMessageReplyMarkup",
                {
                    "chat_id": callback_chat_id,
                    "message_id": callback_message_id,
                    "reply_markup": next_keyboard,
                },
            )

        else:
            # بعد از آخرین بخش، اعلان به ایموجی تبدیل می‌شود.
            final_emoji = random.choice([
                "👍",
                "✅",
                "👌",
            ])

            edit_result = self.api(
                "editMessageText",
                {
                    "chat_id": callback_chat_id,
                    "message_id": callback_message_id,
                    "text": final_emoji,
                    "reply_markup": {
                        "inline_keyboard": []
                    },
                },
            )

        if (
            not edit_result
            or not edit_result.get("ok")
        ):
            logger.warning(
                "Could not update whisper notification | "
                "whisper_id=%s | response=%s",
                whisper_id,
                (
                    edit_result.get("description")
                    if isinstance(edit_result, dict)
                    else "No response"
                ),
            )
