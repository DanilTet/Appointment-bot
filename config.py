import os
from dotenv import load_dotenv

# подгрузка переменных окружения их .env
load_dotenv()

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]
SKIP_ROWS = [25, 46, 67, 83, 94]

# ключи для базы данных
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


SCHEDULE_SHEET_ID = os.getenv("SCHEDULE_SHEET_ID")


# мапа назв: "Повна назва для юзера": "Коротка для таблиці/логіки"
SERVICE_MAP = {
    "Гастроскопія + Колоноскопія": "Гастро + колоно",
    "Гастроскопія": "Гастро",
    "Колоноскопія": "Колоно",
    "Бронхоскопія": "Бронхо",
    "Ректоскопія": "Ректо",
    "Консультація": "Консультация",
    "УЗД": "УЗД"
}

# списки для відображення в кнопках
USER_SERVICES = [
    ["Гастроскопія + Колоноскопія", "Гастроскопія"],
    ["Колоноскопія", "Бронхоскопія"],
    ["Ректоскопія", "Консультація"],
    ["УЗД"]
]

REVERSE_SERVICE_MAP = {} 

for k, v in SERVICE_MAP.items():
    REVERSE_SERVICE_MAP[v] = k