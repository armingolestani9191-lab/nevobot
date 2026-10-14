# ==========================================
#       NevoBot | Warning System
# ==========================================

import logging
import time

from database import get_connection


logger = logging.getLogger("NevoBot.Warnings")


# ==========================================
#              Default Settings
# ==========================================

DEFAULT_MAX_WARNINGS = 3
DEFAULT_PUNISHMENT = "kick"
DEFAULT_ENABLED = True

VALID_PUNISHMENTS = {
    "kick",
    "ban",
    "mute",
}


PUNISHMENT_NAMES = {
    "kick": "اخراج",
    "ban": "بن",
    "mute": "سکوت",
}


# ==========================================
#             Warnings Feature
# ==========================================

class WarningsFeature:
    """
    سیستم اخطار NevoBot.

    قابلیت‌ها:
        - تنظیم مستقل برای هر گروه
        - روشن و خاموش کردن سیستم اخطار
        - تنظیم سقف اخطار
        - انتخاب مجازات نهایی
        - ثبت تعداد اخطارهای هر کاربر
        - اجرای مجازات پس از رسیدن به سقف

    نکته:
        ثبت اخطار باید فقط پس از تأیید تخلف واقعی
        توسط قابلیت مربوطه انجام شود.

        این کلاس نباید برای تخلف از قفل رسانه‌ها
        فراخوانی شود.
    """

    def __init__(
        self,
        api,
        send_message=None,
        ban_feature=None,
        mute_feature=None,
    ):
        self.api = api
        self.send_message = send_message
        self.ban_feature = ban_feature
        self.mute_feature = mute_feature

        self.init_database()

    # ======================================
    #                 Database
    # ======================================

    @staticmethod
    def init_database():
        """
        ساخت جدول‌های سیستم اخطار.

        از CREATE TABLE IF NOT EXISTS استفاده می‌شود
        تا اطلاعات و جدول‌های قبلی پاک نشوند.
        """

        with get_connection() as connection:

            # تنظیمات اخطار هر گروه
            connection.execute("""
                CREATE TABLE IF NOT EXISTS group_warning_settings (
                    chat_id TEXT PRIMARY KEY,

                    enabled INTEGER NOT NULL DEFAULT 1,

                    max_warnings INTEGER NOT NULL DEFAULT 3,

                    punishment TEXT NOT NULL DEFAULT 'kick',

                    updated_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # تعداد اخطارهای کاربران در هر گروه
            connection.execute("""
                CREATE TABLE IF NOT EXISTS user_warnings (
                    chat_id TEXT NOT NULL,

                    user_id TEXT NOT NULL,

                    warning_count INTEGER NOT NULL DEFAULT 0,

                    last_reason TEXT NOT NULL DEFAULT '',

                    updated_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,

                    PRIMARY KEY (chat_id, user_id)
                )
            """)

            # تاریخچه اخطارها
            connection.execute("""
                CREATE TABLE IF NOT EXISTS warning_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    chat_id TEXT NOT NULL,

                    user_id TEXT NOT NULL,

                    reason TEXT NOT NULL DEFAULT '',

                    warning_number INTEGER NOT NULL DEFAULT 0,

                    punishment TEXT NOT NULL DEFAULT '',

                    punishment_applied INTEGER NOT NULL DEFAULT 0,

                    created_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # بهبود سرعت دریافت اخطارهای یک کاربر
            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_user_warnings_chat_user
                ON user_warnings(chat_id, user_id)
            """)

            # بهبود سرعت تاریخچه گروه
            connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_warning_history_chat
                ON warning_history(chat_id, created_at)
            """)

            connection.commit()

    # ======================================
    #             Default Settings
    # ======================================

    @staticmethod
    def _default_settings():
        """تنظیمات پیش‌فرض سیستم اخطار."""

        return {
            "enabled": DEFAULT_ENABLED,
            "max_warnings": DEFAULT_MAX_WARNINGS,
            "punishment": DEFAULT_PUNISHMENT,
        }

    # ======================================
    #              Read Settings
    # ======================================

    def get_settings(self, chat_id):
        """
        دریافت تنظیمات اخطار یک گروه.

        اگر گروه قبلاً تنظیمی نداشته باشد،
        مقادیر پیش‌فرض برگردانده می‌شوند.
        """

        if chat_id is None:
            return self._default_settings()

        try:
            with get_connection() as connection:
                row = connection.execute("""
                    SELECT
                        enabled,
                        max_warnings,
                        punishment
                    FROM group_warning_settings
                    WHERE chat_id = ?
                    LIMIT 1
                """, (
                    str(chat_id),
                )).fetchone()

            if not row:
                return self._default_settings()

            punishment = str(
                row["punishment"] or DEFAULT_PUNISHMENT
            ).lower()

            if punishment not in VALID_PUNISHMENTS:
                punishment = DEFAULT_PUNISHMENT

            try:
                max_warnings = int(row["max_warnings"])
            except (TypeError, ValueError):
                max_warnings = DEFAULT_MAX_WARNINGS

            max_warnings = max(
                1,
                min(max_warnings, 100),
            )

            return {
                "enabled": bool(row["enabled"]),
                "max_warnings": max_warnings,
                "punishment": punishment,
            }

        except Exception:
            logger.exception(
                "Could not read warning settings | chat=%s",
                chat_id,
            )

            return self._default_settings()

    # ======================================
    #              Save Settings
    # ======================================

    def save_settings(
        self,
        chat_id,
        enabled=None,
        max_warnings=None,
        punishment=None,
    ):
        """
        ذخیره تنظیمات گروه.

        پارامترهایی که ارسال نشوند،
        مقدار فعلی خود را حفظ می‌کنند.
        """

        if chat_id is None:
            return False

        current = self.get_settings(chat_id)

        if enabled is None:
            enabled = current["enabled"]

        if max_warnings is None:
            max_warnings = current["max_warnings"]

        if punishment is None:
            punishment = current["punishment"]

        try:
            max_warnings = int(max_warnings)
        except (TypeError, ValueError):
            return False

        # جلوگیری از تنظیم سقف نامعتبر
        if not 1 <= max_warnings <= 100:
            return False

        punishment = str(
            punishment or ""
        ).strip().lower()

        if punishment not in VALID_PUNISHMENTS:
            return False

        try:
            with get_connection() as connection:
                connection.execute("""
                    INSERT INTO group_warning_settings (
                        chat_id,
                        enabled,
                        max_warnings,
                        punishment,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)

                    ON CONFLICT(chat_id)
                    DO UPDATE SET
                        enabled = excluded.enabled,
                        max_warnings = excluded.max_warnings,
                        punishment = excluded.punishment,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    str(chat_id),
                    1 if enabled else 0,
                    max_warnings,
                    punishment,
                ))

                connection.commit()

            logger.info(
                "Warning settings saved | chat=%s | enabled=%s | "
                "max=%s | punishment=%s",
                chat_id,
                bool(enabled),
                max_warnings,
                punishment,
            )

            return True

        except Exception:
            logger.exception(
                "Could not save warning settings | chat=%s",
                chat_id,
            )

            return False

    # ======================================
    #              Toggle System
    # ======================================

    def set_enabled(self, chat_id, enabled):
        """روشن یا خاموش کردن اخطارهای یک گروه."""

        return self.save_settings(
            chat_id,
            enabled=bool(enabled),
        )

    # ======================================
    #             Maximum Warnings
    # ======================================

    def set_max_warnings(self, chat_id, value):
        """تنظیم مستقیم سقف اخطارها."""

        try:
            value = int(value)
        except (TypeError, ValueError):
            return False

        if not 1 <= value <= 100:
            return False

        return self.save_settings(
            chat_id,
            max_warnings=value,
        )

    def change_max_warnings(self, chat_id, amount):
        """
        افزایش یا کاهش سقف اخطارها.

        مثال:
            change_max_warnings(chat_id, 1)
            change_max_warnings(chat_id, -1)
        """

        settings = self.get_settings(chat_id)

        new_value = (
            settings["max_warnings"] + int(amount)
        )

        # حداقل سقف اخطار یک عدد است.
        new_value = max(
            1,
            min(new_value, 100),
        )

        saved = self.set_max_warnings(
            chat_id,
            new_value,
        )

        if not saved:
            return None

        return new_value

    # ======================================
    #            Final Punishment
    # ======================================

    def set_punishment(self, chat_id, punishment):
        """
        انتخاب مجازات نهایی.

        مقادیر مجاز:
            kick
            ban
            mute
        """

        punishment = str(
            punishment or ""
        ).strip().lower()

        if punishment not in VALID_PUNISHMENTS:
            return False

        return self.save_settings(
            chat_id,
            punishment=punishment,
        )

    # ======================================
    #              User Warnings
    # ======================================

    def get_warning_count(self, chat_id, user_id):
        """دریافت تعداد اخطارهای کاربر در یک گروه."""

        if chat_id is None or user_id is None:
            return 0

        try:
            with get_connection() as connection:
                row = connection.execute("""
                    SELECT warning_count
                    FROM user_warnings
                    WHERE chat_id = ?
                      AND user_id = ?
                    LIMIT 1
                """, (
                    str(chat_id),
                    str(user_id),
                )).fetchone()

            if not row:
                return 0

            return max(
                0,
                int(row["warning_count"]),
            )

        except Exception:
            logger.exception(
                "Could not read warning count | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return 0

    def get_user_warnings(self, chat_id, user_id):
        """دریافت اطلاعات کامل اخطار کاربر."""

        if chat_id is None or user_id is None:
            return {
                "warning_count": 0,
                "last_reason": "",
            }

        try:
            with get_connection() as connection:
                row = connection.execute("""
                    SELECT
                        warning_count,
                        last_reason,
                        updated_at
                    FROM user_warnings
                    WHERE chat_id = ?
                      AND user_id = ?
                    LIMIT 1
                """, (
                    str(chat_id),
                    str(user_id),
                )).fetchone()

            if not row:
                return {
                    "warning_count": 0,
                    "last_reason": "",
                }

            return {
                "warning_count": int(row["warning_count"]),
                "last_reason": str(row["last_reason"] or ""),
                "updated_at": row["updated_at"],
            }

        except Exception:
            logger.exception(
                "Could not read user warnings | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return {
                "warning_count": 0,
                "last_reason": "",
            }

    # ======================================
    #              Clear Warnings
    # ======================================

    def clear_warnings(self, chat_id, user_id):
        """پاک کردن تعداد فعلی اخطارهای کاربر."""

        if chat_id is None or user_id is None:
            return False

        try:
            with get_connection() as connection:
                connection.execute("""
                    DELETE FROM user_warnings
                    WHERE chat_id = ?
                      AND user_id = ?
                """, (
                    str(chat_id),
                    str(user_id),
                ))

                connection.commit()

            return True

        except Exception:
            logger.exception(
                "Could not clear warnings | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return False

    # ======================================
    #            Warning History
    # ======================================

    def get_history(
        self,
        chat_id,
        user_id=None,
        limit=20,
    ):
        """دریافت تاریخچه اخطارها."""

        if chat_id is None:
            return []

        try:
            limit = max(
                1,
                min(int(limit), 100),
            )
        except (TypeError, ValueError):
            limit = 20

        try:
            with get_connection() as connection:

                if user_id is None:
                    rows = connection.execute("""
                        SELECT *
                        FROM warning_history
                        WHERE chat_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (
                        str(chat_id),
                        limit,
                    )).fetchall()

                else:
                    rows = connection.execute("""
                        SELECT *
                        FROM warning_history
                        WHERE chat_id = ?
                          AND user_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (
                        str(chat_id),
                        str(user_id),
                        limit,
                    )).fetchall()

            return [
                dict(row)
                for row in rows
            ]

        except Exception:
            logger.exception(
                "Could not read warning history | chat=%s",
                chat_id,
            )

            return []

    # ======================================
    #             Apply Punishment
    # ======================================

    def _apply_kick(self, chat_id, user_id):
        """اخراج کاربر با استفاده از منطق فعلی BanFeature."""

        if self.ban_feature is None:
            logger.error(
                "Cannot kick user: BanFeature is not connected."
            )
            return False

        try:
            result = self.ban_feature.kick(
                chat_id,
                user_id,
            )

            return bool(
                isinstance(result, dict)
                and result.get("ok")
            )

        except Exception:
            logger.exception(
                "Could not kick warned user | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return False

    def _apply_ban(self, chat_id, user_id):
        """بن کاربر با استفاده از منطق فعلی BanFeature."""

        if self.ban_feature is None:
            logger.error(
                "Cannot ban user: BanFeature is not connected."
            )
            return False

        try:
            result = self.ban_feature.ban(
                chat_id,
                user_id,
            )

            return bool(
                isinstance(result, dict)
                and result.get("ok")
            )

        except Exception:
            logger.exception(
                "Could not ban warned user | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return False

    def _apply_mute(self, chat_id, user_id):
        """اعمال سکوت با استفاده از قابلیت فعلی سکوت."""

        if self.mute_feature is None:
            logger.error(
                "Cannot mute user: MuteFeature is not connected."
            )
            return False

        try:
            # از همان تابع داخلی قابلیت سکوت فعلی استفاده می‌کنیم.
            result = self.mute_feature._api_restrict(
                chat_id,
                user_id,
                True,
                int(time.time()) + 3600,
            )

            if isinstance(result, tuple):
                return bool(result[0])

            return bool(result)

        except Exception:
            logger.exception(
                "Could not mute warned user | "
                "chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return False

    def apply_punishment(
        self,
        chat_id,
        user_id,
        punishment,
    ):
        """اجرای مجازات تعیین‌شده برای کاربر."""

        if punishment == "kick":
            return self._apply_kick(
                chat_id,
                user_id,
            )

        if punishment == "ban":
            return self._apply_ban(
                chat_id,
                user_id,
            )

        if punishment == "mute":
            return self._apply_mute(
                chat_id,
                user_id,
            )

        return False

    # ======================================
    #              Notifications
    # ======================================

    def _notify(self, chat_id, text):
        """
        ارسال پیام اطلاع‌رسانی.

        اگر تابع ارسال پیام به کلاس متصل نشده باشد،
        فقط رویداد در لاگ ثبت می‌شود.
        """

        if not callable(self.send_message):
            logger.info(
                "Warning notification | chat=%s | text=%s",
                chat_id,
                text,
            )
            return False

        try:
            result = self.send_message(
                chat_id,
                text,
            )

            return bool(
                isinstance(result, dict)
                and result.get("ok")
            )

        except Exception:
            logger.exception(
                "Could not send warning notification | chat=%s",
                chat_id,
            )

            return False

    # ======================================
    #             Record Violation
    # ======================================

    def record_violation(
        self,
        chat_id,
        user_id,
        reason,
    ):
        """
        ثبت یک اخطار برای تخلف تأییدشده.

        این تابع باید فقط وقتی فراخوانی شود که
        قابلیت مربوطه تخلف را واقعاً تأیید کرده باشد.

        ورودی‌ها:
            chat_id:
                شناسه گروه

            user_id:
                شناسه کاربر متخلف

            reason:
                دلیل تخلف

        نتیجه:
            دیکشنری شامل وضعیت، تعداد اخطارها
            و نتیجه مجازات احتمالی.
        """

        if chat_id is None or user_id is None:
            return {
                "ok": False,
                "reason": "شناسه گروه یا کاربر معتبر نیست.",
            }

        settings = self.get_settings(chat_id)

        # اگر سیستم اخطار خاموش باشد،
        # هیچ اخطاری ثبت نمی‌شود.
        if not settings["enabled"]:
            return {
                "ok": True,
                "recorded": False,
                "disabled": True,
                "warning_count": self.get_warning_count(
                    chat_id,
                    user_id,
                ),
            }

        reason = str(
            reason or "تخلف از قوانین گروه"
        ).strip()[:500]

        punishment = settings["punishment"]
        max_warnings = settings["max_warnings"]

        try:
            with get_connection() as connection:

                # ثبت یا افزایش شمارنده کاربر
                connection.execute("""
                    INSERT INTO user_warnings (
                        chat_id,
                        user_id,
                        warning_count,
                        last_reason,
                        updated_at
                    )
                    VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)

                    ON CONFLICT(chat_id, user_id)
                    DO UPDATE SET
                        warning_count = warning_count + 1,
                        last_reason = excluded.last_reason,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    str(chat_id),
                    str(user_id),
                    reason,
                ))

                row = connection.execute("""
                    SELECT warning_count
                    FROM user_warnings
                    WHERE chat_id = ?
                      AND user_id = ?
                    LIMIT 1
                """, (
                    str(chat_id),
                    str(user_id),
                )).fetchone()

                warning_count = int(
                    row["warning_count"]
                )

                # ثبت تاریخچه؛ مجازات پس از ثبت اخطار
                # در ادامه اجرا و نتیجه‌اش ذخیره می‌شود.
                cursor = connection.execute("""
                    INSERT INTO warning_history (
                        chat_id,
                        user_id,
                        reason,
                        warning_number,
                        punishment,
                        punishment_applied
                    )
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (
                    str(chat_id),
                    str(user_id),
                    reason,
                    warning_count,
                    punishment,
                ))

                history_id = cursor.lastrowid

                connection.commit()

        except Exception:
            logger.exception(
                "Could not record warning | chat=%s | user=%s",
                chat_id,
                user_id,
            )

            return {
                "ok": False,
                "reason": "ثبت اخطار در دیتابیس ناموفق بود.",
            }

        logger.info(
            "Warning recorded | chat=%s | user=%s | "
            "count=%s | max=%s | reason=%s",
            chat_id,
            user_id,
            warning_count,
            max_warnings,
            reason,
        )

        # هنوز سقف اخطار تکمیل نشده است.
        if warning_count < max_warnings:
            return {
                "ok": True,
                "recorded": True,
                "warning_count": warning_count,
                "max_warnings": max_warnings,
                "punished": False,
                "reason": reason,
            }

        # رسیدن به سقف اخطارها:
        # مجازات نهایی اجرا می‌شود.
        punished = self.apply_punishment(
            chat_id,
            user_id,
            punishment,
        )

        # نتیجه واقعی عملیات در تاریخچه ثبت شود.
        try:
            with get_connection() as connection:
                connection.execute("""
                    UPDATE warning_history
                    SET punishment_applied = ?
                    WHERE id = ?
                """, (
                    1 if punished else 0,
                    int(history_id),
                ))

                connection.commit()

        except Exception:
            logger.exception(
                "Could not update punishment history | id=%s",
                history_id,
            )

        if punished:
            punishment_name = PUNISHMENT_NAMES.get(
                punishment,
                punishment,
            )

            notification_text = (
                "⚠️ کاربر به سقف اخطارهای گروه رسید.\n\n"
                f"👤 شناسه کاربر: {user_id}\n"
                f"📊 تعداد اخطارها: {warning_count}/{max_warnings}\n"
                f"🔨 مجازات اجراشده: {punishment_name}"
            )

            self._notify(
                chat_id,
                notification_text,
            )

            # بعد از مجازات موفق، شمارنده فعلی پاک می‌شود
            # تا در صورت بازگشت کاربر، شمارش از نو شروع شود.
            self.clear_warnings(
                chat_id,
                user_id,
            )

        else:
            logger.error(
                "Final punishment failed | chat=%s | "
                "user=%s | punishment=%s",
                chat_id,
                user_id,
                punishment,
            )

            self._notify(
                chat_id,
                (
                    "❌ کاربر به سقف اخطارها رسید، "
                    "اما اجرای مجازات ناموفق بود.\n"
                    f"شناسه کاربر: {user_id}\n"
                    "مجوزهای مدیریتی ربات را بررسی کنید."
                ),
            )

        return {
            "ok": True,
            "recorded": True,
            "warning_count": warning_count,
            "max_warnings": max_warnings,
            "punished": punished,
            "punishment": punishment,
            "reason": reason,
      }
