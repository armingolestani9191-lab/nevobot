
import os

# ==========================================
#          NevoBot | نِووبات
# ==========================================

# توکن ربات بله
BOT_TOKEN = "1954705755:v_Lh_gczzr0I3fKJPshwhxMRgoqhDQ2aBV0"

# آیدی عددی ادمین اصلی
ADMIN_ID = 595450272

# آیدی ادمین‌های اضافی
ADMIN_IDS = {
    ADMIN_ID,
}

# مشخصات ربات
BOT_NAME = "NevoBot"
BOT_NAME_FA = "نِووبات"

# آدرس API بله
API_BASE_URL = "https://tapi.bale.ai"


# ==========================================
#          تنظیمات دیتابیس
# ==========================================

# تشخیص اجرای ربات روی Railway
IS_RAILWAY = any(
    os.environ.get(name)
    for name in (
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_SERVICE_ID",
    )
)

# مسیر پوشه پروژه
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# مسیر ذخیره دیتابیس
if IS_RAILWAY:
    DB_PATH = "/data/nevobot.db"
else:
    DB_PATH = os.path.join(BASE_DIR, "nevobot.db")

# نام سازگار با فایل‌های فعلی پروژه
DATABASE_PATH = DB_PATH


# ==========================================
#          تنظیمات ارتباط با API
# ==========================================

REQUEST_TIMEOUT = 40
POLLING_TIMEOUT = 25

# تنظیمات عمومی
DEBUG = False
