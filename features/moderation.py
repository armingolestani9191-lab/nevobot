
import logging
import time


logger = logging.getLogger("NevoBot.Moderation")


class Moderation:
    """
    ابزارهای مدیریت گروه نِووبات.

    api باید تابع api(method, payload) موجود در main.py باشد.
    مجوز مدیر اجراکننده باید قبل از فراخوانی این متدها بررسی شود.
    """

    def __init__(self, api):
        self.api = api

    def _request(self, method, payload):
        """ارسال درخواست و بررسی پاسخ API."""

        try:
            result = self.api(method, payload)

            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "error": "پاسخی از API دریافت نشد."
                }

            if not result.get("ok"):
                logger.warning(
                    "Moderation API failed: %s",
                    method
                )

                return {
                    "ok": False,
                    "error": result.get(
                        "description",
                        "درخواست ناموفق بود."
                    )
                }

            return result

        except Exception:
            logger.exception(
                "Moderation request failed: %s",
                method
            )

            return {
                "ok": False,
                "error": "خطای داخلی هنگام اجرای درخواست."
            }

    @staticmethod
    def _validate_ids(chat_id, user_id):
        """اعتبارسنجی شناسه‌های گروه و کاربر."""

        if chat_id is None or user_id is None:
            raise ValueError(
                "شناسه گروه و کاربر الزامی است."
            )

        if str(chat_id).strip() == "":
            raise ValueError("شناسه گروه معتبر نیست.")

        if str(user_id).strip() == "":
            raise ValueError("شناسه کاربر معتبر نیست.")

        if str(chat_id) == str(user_id):
            raise ValueError(
                "شناسه گروه و کاربر نمی‌توانند یکسان باشند."
            )

    def kick_member(self, chat_id, user_id):
        """
        اخراج عضو با امکان بازگشت.

        ابتدا عضو اخراج و سپس در صورت موفقیت،
        مسدودیت او برداشته می‌شود.
        """

        self._validate_ids(chat_id, user_id)

        result = self._request(
            "kickChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

        if not result.get("ok"):
            return result

        unban_result = self.unban_member(
            chat_id,
            user_id
        )

        if not unban_result.get("ok"):
            return {
                "ok": False,
                "error": (
                    "اخراج انجام شد، اما برداشتن "
                    "مسدودیت تأیید نشد."
                )
            }

        return {
            "ok": True,
            "message": "عضو اخراج شد و امکان بازگشت دارد."
        }

    def ban_member(self, chat_id, user_id):
        """مسدودکردن عضو تا زمان رفع مسدودیت."""

        self._validate_ids(chat_id, user_id)

        return self._request(
            "kickChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

    def unban_member(self, chat_id, user_id):
        """رفع مسدودیت عضو."""

        self._validate_ids(chat_id, user_id)

        return self._request(
            "unbanChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

    def mute_member(
        self,
        chat_id,
        user_id,
        duration_seconds=3600
    ):
        """محدودکردن ارسال پیام برای مدت مشخص."""

        self._validate_ids(chat_id, user_id)

        try:
            duration_seconds = int(duration_seconds)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "مدت سکوت باید عدد باشد."
            }

        if not 30 <= duration_seconds <= 366 * 24 * 60 * 60:
            return {
                "ok": False,
                "error": "مدت سکوت باید بین ۳۰ ثانیه و ۳۶۶ روز باشد."
            }

        until_date = int(time.time()) + duration_seconds

        return self._request(
            "restrictChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "permissions": {
                    "can_send_messages": False,
                    "can_send_media_messages": False,
                    "can_send_other_messages": False,
                    "can_add_web_page_previews": False,
                },
                "until_date": until_date,
            }
        )

    def unmute_member(self, chat_id, user_id):
        """برداشتن محدودیت ارسال پیام."""

        self._validate_ids(chat_id, user_id)

        return self._request(
            "restrictChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "permissions": {
                    "can_send_messages": True,
                    "can_send_media_messages": True,
                    "can_send_other_messages": True,
                    "can_add_web_page_previews": True,
                },
            }
        )
