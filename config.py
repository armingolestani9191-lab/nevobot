import os

# NevoBot configuration

BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "nevobot.db"
)

API_BASE_URL = "https://tapi.bale.ai"

REQUEST_TIMEOUT = 20
POLLING_TIMEOUT = 25

BOT_NAME = "NevoBot"
BOT_NAME_FA = "نِووبات"
