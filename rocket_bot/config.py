import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

API_ID = int(os.getenv("TELEGRAM_API_ID", "38883376"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "67909afdf0fe6c23e6af29985f645297")
SESSION_NAME = str(BASE_DIR / "userbot_session")
BOT_USERNAME = os.getenv("TARGET_BOT", "epic_gift_bot")
TARGET_USER = os.getenv("TARGET_USER", "xua202")

# Signal Bot sozlamalari (signallarni kanal yoki guruhga yuborish uchun)
SIGNAL_BOT_TOKEN = os.getenv("SIGNAL_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("SIGNAL_CHANNEL_ID", "")
