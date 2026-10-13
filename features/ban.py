
import logging

logger = logging.getLogger("NevoBot.Ban")


class BanFeature:
    """عملیات بن، رفع بن و اخراج اعضا."""

    def __init__(self, api):
        # تابع API ربات از main.py به این کلاس داده می‌شود.
        self.api = api

    def _call(self, method, payload):
        try:
            result = self.api(method, payload)

            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "error": "پاسخ API نامعتبر است."
                }

            if not result.get("ok"):
                return {
                    "ok": False,
                    "error": result.get(
                        "description",
                        "عملیات ناموفق بود."
                    )
                }

            return result

        except Exception:
            logger.exception("Ban operation failed")
            return {
                "ok": False,
                "error": "خطا هنگام اجرای عملیات."
            }

    @staticmethod
    def _validate(chat_id, user_id):
        if chat_id is None or user_id is None:
            raise ValueError("شناسه گروه و کاربر الزامی است.")

    def ban(self, chat_id, user_id):
        """مسدودکردن عضو؛ نیازمند پشتیبانی API بله است."""
        self._validate(chat_id, user_id)

        return self._call(
            "kickChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

    def unban(self, chat_id, user_id):
        """رفع مسدودیت عضو."""
        self._validate(chat_id, user_id)

        return self._call(
            "unbanChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )

    def kick(self, chat_id, user_id):
        """اخراج عضو؛ امکان بازگشت باید جداگانه بررسی شود."""
        self._validate(chat_id, user_id)

        return self._call(
            "kickChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )
