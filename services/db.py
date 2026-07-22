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

# --- ФУНКЦІЇ ДЛЯ РОЗСИЛОК (BROADCASTS) ---
def get_all_bot_user_ids(exclude_admins=True):
    """
    Повертає список унікальних user_id всіх користувачів, які взаємодіяли з ботом.
    """
    try:
        user_ids = set()
        
        # 1. Запит унікальних відвідувачів з user_visits
        try:
            res_visits = supabase.table("user_visits").select("user_id, is_admin").execute()
            data_visits = res_visits.data or []
            for row in data_visits:
                uid = row.get('user_id')
                if uid:
                    is_adm = row.get('is_admin') or (uid in ADMIN_IDS)
                    if not (exclude_admins and is_adm):
                        user_ids.add(int(uid))
        except Exception as e:
            print(f"Помилка читання user_visits: {e}")

        # 2. Запит користувачів зAppointments (пацієнти з записом)
        try:
            res_appts = supabase.table("appointments").select("user_id").execute()
            data_appts = res_appts.data or []
            for row in data_appts:
                uid = row.get('user_id')
                if uid and int(uid) != 0:
                    if not (exclude_admins and int(uid) in ADMIN_IDS):
                        user_ids.add(int(uid))
        except Exception as e:
            print(f"Помилка читання appointments: {e}")

        return list(user_ids)
    except Exception as e:
        print(f"Помилка отримання користувачів для розсилки: {e}")
        return []


def load_broadcasts():
    try:
        response = supabase.table("bot_state").select("value").eq("key", "broadcasts").execute()
        return response.data[0]["value"] if (response.data and "value" in response.data[0]) else {}
    except Exception as e:
        print(f"Помилка завантаження розсилок: {e}")
        return {}

def save_broadcasts(broadcasts_dict):
    try:
        supabase.table("bot_state").upsert({
            "key": "broadcasts",
            "value": broadcasts_dict
        }).execute()
    except Exception as e:
        print(f"Помилка збереження розсилок: {e}")

def save_broadcast(bc_data):
    bcs = load_broadcasts()
    bcs[bc_data["id"]] = bc_data
    save_broadcasts(bcs)

def delete_broadcast(bc_id):
    bcs = load_broadcasts()
    if bc_id in bcs:
        del bcs[bc_id]
        save_broadcasts(bcs)

def get_scheduled_broadcasts():
    bcs = load_broadcasts()
    return [bc for bc in bcs.values() if bc.get("status") == "scheduled"]