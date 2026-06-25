from supabase import create_client, Client

# импортируем переменные из конфига
from config import SUPABASE_URL, SUPABASE_KEY, ADMIN_IDS

# инициализация клиента Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_appointment_to_db(user_id, name, phone, date, time, row_idx):
    try:
        data = {
            "user_id": user_id,
            "name": name,
            "phone": phone,
            "date": date,
            "time": time,
            "row_idx": row_idx,
            "status": "confirmed"
        }
        supabase.table("appointments").insert(data).execute()
    except Exception as e:
        print(f"Помилка запису в Supabase: {e}")

def log_user_visit(user_id):
    try:
        is_admin = user_id in ADMIN_IDS
        data = {
            "user_id": user_id,
            "is_admin": is_admin
        }
        supabase.table("user_visits").insert(data).execute()
    except Exception as e:
        print(f"Помилка логування візиту: {e}")

# --- ФУНКЦИИ ДЛЯ СОХРАНЕНИЯ СОСТОЯНИЯ ---
def load_state():
    try:
        response = supabase.table("bot_state").select("value").eq("key", "roma_monitoring").execute()
        return response.data[0]["value"] if response.data else {}
    except Exception as e:
        print(f"Помилка завантаження стану: {e}")
        return {}
    
def save_state(state):
    try:
        supabase.table("bot_state").upsert({
            "key": "roma_monitoring",
            "value": state
        }).execute()
    except Exception as e:
        print(f"Помилка збереження файлу: {e}")

# Глобальная переменная для мониторинга (загружается при старте)
last_seen_doctors = load_state()

# --- ФУНКЦИИ СТАТИСТИКИ ---
async def get_bot_stats():
    try:
        # Отримуємо всі дані
        res = supabase.table("user_visits").select("user_id, is_admin").execute()
        data = res.data
        
        total_entries = len(data)
        unique_users = len(set(row['user_id'] for row in data))
        admin_entries = len([row for row in data if row['is_admin']])
        user_entries = total_entries - admin_entries
        
        return (
            f"📊 <b>Статистика бота</b>\n\n"
            f"👥 Унікальних користувачів: <b>{unique_users}</b>\n"
            f"🚀 Всього заходів: <b>{total_entries}</b>\n"
            f"👑 Заходів адмінів: <b>{admin_entries}</b>\n"
            f"👤 Заходів пацієнтів: <b>{user_entries}</b>"
        )
    except Exception as e:
        return f"Помилка отримання статистики: {e}"

# --- РОБОТА З НАЛАШТУВАННЯМИ ---
async def get_admin_settings(admin_id):
    try:
        res = supabase.table("admin_settings").select("*").eq("admin_id", admin_id).execute()
        if res.data:
            return res.data[0]
        else:
            default = {
                "admin_id": admin_id, 
                "sync_notifications": True, 
                "execution_notifications": True, 
                "track_danilo": True
            }
            supabase.table("admin_settings").insert(default).execute()
            return default
    except Exception as e:
        print(f"Помилка отримання налаштувань: {e}")
        return {"sync_notifications": True, "execution_notifications": True, "track_danilo": True}

async def update_admin_setting(admin_id, key, value):
    supabase.table("admin_settings").upsert({"admin_id": admin_id, key: value}).execute()