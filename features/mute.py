
import logging
import re
import threading
import time

from database import get_connection

logger = logging.getLogger("NevoBot.Mute")


class MuteFeature:
    CHECK_INTERVAL = 2
    MAX_DURATION = 3600

    def __init__(self, api, is_authorized):
        self.api = api
        self.is_authorized = is_authorized

        self._db_lock = threading.Lock()
        self._worker_started = False

        # جدول زمان پایان سکوت؛ در همان دیتابیس اصلی ربات
        self._init_mute_table()

        # بررسی خودکار سکوت‌های منقضی‌شده
        self._start_expiration_worker()

    # --------------------------------------------------
    # دیتابیس زمان پایان سکوت
    # --------------------------------------------------

    def _init_mute_table(self):
        with get_connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS mute_expirations (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)
            connection.commit()

    def _save_expiration(self, chat_id, user_id, expires_at):
        with self._db_lock:
            with get_connection() as connection:
                connection.execute("""
                    INSERT INTO mute_expirations (
                        chat_id, user_id, expires_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id, user_id)
                    DO UPDATE SET expires_at = excluded.expires_at
                """, (
                    str(chat_id),
                    str(user_id),
                    int(expires_at),
                ))
                connection.commit()

    def _remove_expiration(self, chat_id, user_id):
        with self._db_lock:
            with get_connection() as connection:
                connection.execute("""
                    DELETE FROM mute_expirations
                    WHERE chat_id = ? AND user_id = ?
                """, (str(chat_id), str(user_id)))
                connection.commit()

    def _get_expired_mutes(self):
        with self._db_lock:
            with get_connection() as connection:
                rows = connection.execute("""
                    SELECT chat_id, user_id, expires_at
                    FROM mute_expirations
                    WHERE expires_at <= ?
                """, (int(time.time()),)).fetchall()

                return [dict(row) for row in rows]

    def _start_expiration_worker(self):
        if self._worker_started:
            return

        worker = threading.Thread(
            target=self._expiration_worker,
            name="NevoBot-MuteExpiration",
            daemon=True,
        )
        worker.start()
        self._worker_started = True

    def _expiration_worker(self):
        logger.info("Mute expiration worker started")

        while True:
            try:
                expired_mutes = self._get_expired_mutes()

                for item in expired_mutes:
                    chat_id = item["chat_id"]
                    user_id = item["user_id"]

                    success, error = self._api_restrict(
                        chat_id=chat_id,
                        user_id=user_id,
                        muted=False,
                    )

                    if success:
                        self._remove_expiration(chat_id, user_id)

                        logger.info(
                            "Timed mute expired | chat=%s user=%s",
                            chat_id,
                            user_id,
                        )
                    else:
                        # اگر API موقتاً خطا داد، رکورد باقی می‌ماند
                        # تا در دور بعدی دوباره امتحان شود.
                        logger.warning(
                            "Timed unmute failed | chat=%s user=%s "
                            "error=%s",
                            chat_id,
                            user_id,
                            error,
                        )

            except Exception:
                logger.exception("Mute expiration worker error")

            time.sleep(self.CHECK_INTERVAL)

    # --------------------------------------------------
    # نرمال‌سازی و تشخیص دستور
    # --------------------------------------------------

    def _normalize_digits(self, text):
        translation = str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
        return text.translate(translation)

    def _normalize_text(self, text):
        text = self._normalize_digits(text.strip().lower())

        # یکسان‌سازی حروف عربی و فارسی
        text = text.replace("ي", "ی").replace("ك", "ک")
        text = " ".join(text.split())

        return text

    def _parse_duration(self, text):
        text = self._normalize_text(text)

        # پشتیبانی از شکل‌های رایج دستور:
        # سکوت 5 دقیقه
        # سکوت برای 5 دقیقه
        # سکوت به مدت 5 دقیقه
        text = re.sub(r"^(?:برای|به مدت)\s+", "", text)

        match = re.fullmatch(
            r"(\d+)\s*"
            r"(ثانیه|ثانیه‌ای|ثانیه ای|ثانیهها|"
            r"دقیقه|دقیقه‌ای|دقیقه ای|دیقه|دیقه‌ای|دیقه ای|"
            r"ساعت|ساعتی|ساعت ای|"
            r"seconds?|secs?|s|"
            r"minutes?|mins?|m|"
            r"hours?|hrs?|h)",
            text,
            re.IGNORECASE,
        )

        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower().replace(" ", "")

        if amount <= 0:
            return None

        if (
            unit.startswith("ثانیه")
            or unit.startswith("sec")
            or unit == "s"
        ):
            seconds = amount

        elif (
            unit.startswith("دقیقه")
            or unit.startswith("دیقه")
            or unit.startswith("min")
            or unit == "m"
        ):
            seconds = amount * 60

        else:
            seconds = amount * 3600

        # کمتر از ۳۰ ثانیه یا بیشتر از یک ساعت پذیرفته نمی‌شود
        if seconds < 30 or seconds > self.MAX_DURATION:
            return None

        return seconds

    def _is_mute_command(self, text):
        text = self._normalize_text(text)

        unmute_commands = (
            "حذف سکوت",
            "سکوت حذف",
            "رفع سکوت",
            "لغو سکوت",
            "آزاد",
            "unmute",
        )

        if text in unmute_commands:
            return "unmute", None, None

        mute_commands = (
            "سکوت کن",
            "ساکت کن",
            "سکوت",
            "ساکت",
            "mute",
        )

        if text in mute_commands:
            return "mute", None, None

        for command in sorted(mute_commands, key=len, reverse=True):
            if not text.startswith(command + " "):
                continue

            remainder = text[len(command):].strip()
            duration = self._parse_duration(remainder)

            if duration is None:
                return (
                    "mute",
                    None,
                    "مدت نامعتبر است. مثال‌ها: "
                    "سکوت 5 دقیقه، سکوت 5 دیقه، سکوت 30 ثانیه",
                )

            return "mute", duration, None

        return None, None, None

    # --------------------------------------------------
    # پیدا کردن کاربر هدف
    # --------------------------------------------------

    def _get_target(self, message):
        reply = message.get("reply_to_message")

        if not isinstance(reply, dict):
            return None

        user = reply.get("from")

        if not isinstance(user, dict):
            return None

        return user.get("id")

    # --------------------------------------------------
    # متد اصلی محدودسازی API
    # --------------------------------------------------

    def _api_restrict(self, chat_id, user_id, muted):
        allowed = not muted

        # عمداً until_date ارسال نمی‌کنیم.
        # سکوت واقعی با همان متد API انجام می‌شود و زمان پایان
        # توسط زمان‌بند دیتابیس مدیریت خواهد شد.
        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
            "can_send_messages": allowed,
            "can_send_media_messages": allowed,
            "can_send_other_messages": allowed,
            "can_add_web_page_previews": allowed,
        }

        try:
            result = self.api("restrictChatMember", payload)

            if not isinstance(result, dict):
                return False, "پاسخ معتبر از API دریافت نشد."

            if result.get("ok") is not True:
                error = result.get(
                    "description",
                    result.get("error", "درخواست توسط API تأیید نشد."),
                )

                logger.error(
                    "restrictChatMember failed | chat=%s user=%s "
                    "muted=%s error=%s",
                    chat_id,
                    user_id,
                    muted,
                    error,
                )

                return False, str(error)

            return True, None

        except Exception as exc:
            logger.exception("Restriction API request failed")
            return False, str(exc)

    # --------------------------------------------------
    # اجرای دستورات سکوت و رفع سکوت
    # --------------------------------------------------

    def handle_message(self, message):
        if not isinstance(message, dict):
            return None

        text = message.get("text", "")

        if not isinstance(text, str) or not text.strip():
            return None

        action, duration, duration_error = self._is_mute_command(text)

        if action is None:
            return None

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")

        if chat_type not in ("group", "supergroup"):
            return {
                "text": "❌ این دستور فقط داخل گروه قابل استفاده است."
            }

        if chat_id is None:
            return {"text": "❌ شناسه گروه دریافت نشد."}

        if duration_error:
            return {"text": f"❌ {duration_error}"}

        target_id = self._get_target(message)

        if target_id is None:
            return {
                "text": (
                    "⚠️ روی پیام فرد موردنظر ریپلای کن، "
                    "بعد دستور سکوت یا رفع سکوت را بفرست."
                )
            }

        sender = message.get("from") or {}
        sender_id = sender.get("id")

        if sender_id is None:
            return {"text": "❌ اطلاعات اجراکننده دستور دریافت نشد."}

        if str(sender_id) == str(target_id):
            return {
                "text": "⚠️ نمی‌توانی روی پیام خودت این دستور را اجرا کنی."
            }

        try:
            authorized = self.is_authorized(message)
        except Exception:
            logger.exception("Mute authorization check failed")
            return {
                "text": "❌ بررسی دسترسی ناموفق بود؛ لاگ را بررسی کن."
            }

        if not authorized:
            return {"text": "⛔ اجازه اجرای این دستور را نداری."}

        # ---------------- سکوت ----------------
        if action == "mute":
            success, error = self._api_restrict(
                chat_id=chat_id,
                user_id=target_id,
                muted=True,
            )

            if not success:
                return {
                    "text": (
                        "❌ سکوت اعمال نشد.\n"
                        f"دلیل API: {error}"
                    )
                }

            if duration is None:
                # سکوت دائمی؛ زمان پایان قبلی باید حذف شود.
                try:
                    self._remove_expiration(chat_id, target_id)
                except Exception:
                    logger.exception("Could not clear mute expiration")
                    return {
                        "text": (
                            "⚠️ سکوت اعمال شد، اما ذخیره وضعیت زمان‌بندی "
                            "ناموفق بود. دیتابیس را بررسی کن."
                        )
                    }

                return {"text": "🔇 سکوت دائمی با موفقیت اعمال شد."}

            expires_at = int(time.time()) + duration

            try:
                self._save_expiration(
                    chat_id,
                    target_id,
                    expires_at,
                )
            except Exception:
                logger.exception("Could not save mute expiration")

                # اگر زمان پایان ذخیره نشد، سعی کن کاربر را آزاد کنی
                rollback_ok, rollback_error = self._api_restrict(
                    chat_id=chat_id,
                    user_id=target_id,
                    muted=False,
                )

                if rollback_ok:
                    return {
                        "text": (
                            "❌ زمان پایان سکوت ذخیره نشد؛ "
                            "محدودیت کاربر برداشته شد. دوباره تلاش کن."
                        )
                    }

                return {
                    "text": (
                        "⚠️ سکوت اعمال شد اما زمان پایان ذخیره نشد "
                        "و رفع محدودیت خودکار هم ناموفق بود.\n"
                        f"خطا: {rollback_error}"
                    )
                }

            if duration % 3600 == 0:
                duration_text = f"{duration // 3600} ساعت"
            elif duration % 60 == 0:
                duration_text = f"{duration // 60} دقیقه"
            else:
                duration_text = f"{duration} ثانیه"

            return {
                "text": (
                    "🔇 سکوت مدت‌دار اعمال شد.\n"
                    f"⏱ مدت: {duration_text}\n"
                    "🔊 بعد از پایان زمان، سکوت خودکار برداشته می‌شود."
                )
            }

        # ---------------- رفع سکوت ----------------
        if action == "unmute":
            success, error = self._api_restrict(
                chat_id=chat_id,
                user_id=target_id,
                muted=False,
            )

            if not success:
                return {
                    "text": (
                        "❌ رفع سکوت انجام نشد.\n"
                        f"دلیل API: {error}"
                    )
                }

            try:
                self._remove_expiration(chat_id, target_id)
            except Exception:
                logger.exception("Could not remove mute expiration")
                return {
                    "text": (
                        "🔊 محدودیت برداشته شد، اما حذف زمان پایان "
                        "از دیتابیس ناموفق بود."
                    )
                }

            return {"text": "🔊 سکوت کاربر با موفقیت برداشته شد."}

        return None
