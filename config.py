from os import getenv
from dotenv import load_dotenv
import logging
from logging.handlers import RotatingFileHandler

load_dotenv()
LOGS = logging.getLogger(__name__)

class Config:
    API_ID = getenv("API_ID")
    API_HASH = getenv("API_HASH")
    BOT_TOKEN = getenv("BOT_TOKEN")
    DB_URI = getenv("DB_URI")
    DB_NAME = getenv("DB_NAME")
    DB_CHANNEL_ID = int(getenv("CHANNEL_ID", "0"))
    MAIN_CHANNEL_URL = getenv("MAIN_CHANNEL_URL")
    PROTECT_CONTENT = True if getenv('PROTECT_CONTENT', "False") == "True" else False
    THUMBNAIL = getenv("THUMBNAIL", "https://envs.sh/im5.jpg")
    CHANNEL_USERNAME = getenv("CHANNEL_USERNAME", "@seishiro_atanime")
    START_PIC = getenv("START_PIC", "https://envs.sh/im5.jpg")
    FORCE_PIC = getenv("FORCE_PIC", "https://envs.sh/im5.jpg")

LOG_FILE_NAME = "automanga.txt"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt='%d-%b-%y %H:%M:%S',
    handlers=[
        RotatingFileHandler(
            LOG_FILE_NAME,
            maxBytes=50000000,
            backupCount=10
        ),
        logging.StreamHandler()
    ]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
