import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

# Импортируем нужные константы из конфига и клиент БД
from config import SKIP_ROWS, SCHEDULE_SHEET_ID
from services.db import supabase

# --- Настройка Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
schedule_sheet = client.open_by_key(SCHEDULE_SHEET_ID)


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ТАБЛИЦ ---

def get_monday_str(dt):
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%d.%m")

def get_col_idx(dt):
    return 3 + (dt.weekday() * 2) 

def get_next_row_idx(current_row):
    next_row = current_row + 5
    if next_row in SKIP_ROWS:
        return next_row + 1
    return next_row

def clear_sheet_slot(ws, row, col):
    start_cell = gspread.utils.rowcol_to_a1(row, col)
    end_cell = gspread.utils.rowcol_to_a1(row + 4, col)
    range_label = f"{start_cell}:{end_cell}"
    ws.batch_clear([range_label])

def update_sheet_slots(ws, start_row, col, data, execution_stage="Запланировано"):
    values = [
        [data['service']],                       # row
        [f"{data['name']} ({data['phone']})"],   # row + 1
        [data['doctor']],                        # row + 2
        [data['anesthesia']],                    # row + 3
        [execution_stage]                        # row + 4
    ]
    
    start_cell = gspread.utils.rowcol_to_a1(start_row, col)
    end_cell = gspread.utils.rowcol_to_a1(start_row + 4, col)
    range_label = f"{start_cell}:{end_cell}"

    ws.update(range_label, values)

async def update_execution_stage(appt_id, stage, ws, row, col):
    try:
        ws.update_cell(row + 4, col, stage)
        # Обращаемся к Supabase, который импортировали сверху
        supabase.table("appointments").update({"execution_stage": stage}).eq("id", appt_id).execute()
        return True
    except Exception as e:
        print(f"Помилка оновлення етапу: {e}")
        return False

def get_appointment_duration(service, anesthesia):
    service = service.strip()
    anesthesia = anesthesia.strip() if anesthesia else ""

    if service == "Гастро + колоно" or service == "Колоно":
        return 60
    
    if "Наркоз " in anesthesia:
        return 30
    
    return 15

# Перевірка обмежень по часу
def is_time_allowed(service, anesthesia, time_str):
    try:
        hour = int(time_str.split(":")[0])

        if hour >= 15:
            return False
        
        if service in ["Гастро + колоно", "Колоно"]:
            return hour in [11, 12, 13, 14]
        
        #if service == "Ерхпг ":
            #return time_str in ["13:00", "14:00"]
        
        morning_services = ["Гастро", "Бронхо", "Ректо", "Консультация", "УЗД"]
        if service in morning_services:
            return hour < 11
        
        return False
    except:
        return False

# --- БИЗНЕС-ЛОГИКА ЧТЕНИЯ ТАБЛИЦ ---

async def get_schedule_report(target_date):
    date_str = target_date.strftime("%d.%m.%Y")
    sheet_name = get_monday_str(target_date)

    try:
        ws = schedule_sheet.worksheet(sheet_name)
        col_idx = get_col_idx(target_date) - 1
        data = ws.get_all_values()

    except Exception as e:
        return f"Помилка доступу до таблиці({date_str}): {e}"
    
    report_lines = [f"Розклад на {date_str}:\n"]
    has_appointments = False
    curr_row = 4

    while curr_row < len(data):
        if (curr_row + 1) in SKIP_ROWS:
            curr_row += 1
            continue
        
        if curr_row + 1 >= len(data): break
        patient_info = data[curr_row + 1][col_idx].strip()

        if patient_info:
            has_appointments = True
            time_val = data[curr_row][col_idx - 1]
            service = data[curr_row][col_idx]
            doctor = data[curr_row + 2][col_idx]
            anesthesia = data[curr_row + 3][col_idx]

            report_lines.append(
                f"🕒 <b>{time_val}</b>\n"
                f"👤 Пацієнт: {patient_info}\n"
                f"🩺 Послуга: {service}\n"
                f"👨‍⚕️ Лікар: {doctor}\n"
                f"💉 Наркоз: {anesthesia}\n"
                f"──────────────────"
            )  
        curr_row += 5

    if not has_appointments:
        report_lines.append(f"Вільних записів немає на {date_str}. День вільний.")
        
    return "\n".join(report_lines)

def get_raw_appointments(target_date):
    """
    Получает структурированный список приемов на указанную дату из Google Таблицы.
    Возвращает список словарей. Если приемов нет или произошла ошибка, возвращает пустой список.
    """
    date_str = target_date.strftime("%d.%m.%Y")
    sheet_name = get_monday_str(target_date)

    try:
        ws = schedule_sheet.worksheet(sheet_name)
        col_idx = get_col_idx(target_date) - 1
        data = ws.get_all_values()
    except Exception as e:
        print(f"Помилка доступу до таблиці при отриманні сирих даних ({date_str}): {e}")
        return []
    
    appointments = []
    curr_row = 4

    while curr_row < len(data):
        if (curr_row + 1) in SKIP_ROWS:
            curr_row += 1
            continue
        
        if curr_row + 1 >= len(data): 
            break
            
        patient_info = data[curr_row + 1][col_idx].strip()

        if patient_info:
            time_val = data[curr_row][col_idx - 1].strip()
            service = data[curr_row][col_idx].strip()
            doctor = data[curr_row + 2][col_idx].strip()
            anesthesia = data[curr_row + 3][col_idx].strip()

            appointments.append({
                "time": time_val,
                "patient": patient_info,
                "service": service,
                "doctor": doctor,
                "anesthesia": anesthesia
            })
            
        curr_row += 5

    return appointments

async def get_available_dates(days_to_check=10):
    available_dates = []
    now = datetime.now() + timedelta(hours=2) # Киевское время
    cached_sheets = {}

    for i in range(days_to_check):
        target_date = now + timedelta(days=i)
        if target_date.weekday() >= 5: continue 
        
        sheet_name = get_monday_str(target_date)
        date_str = target_date.strftime("%d.%m.%Y")

        if sheet_name not in cached_sheets:
            try:
                ws = schedule_sheet.worksheet(sheet_name)
                cached_sheets[sheet_name] = ws.get_all_values()
            except: continue
        
        data = cached_sheets[sheet_name]
        col = get_col_idx(target_date) - 1
        has_free = False
        curr_row = 4
        while curr_row < len(data):
            if (curr_row + 1) in SKIP_ROWS:
                curr_row += 1
                continue
            if curr_row + 1 < len(data) and col < len(data[curr_row]):
                if not data[curr_row + 1][col].strip():
                    has_free = True
                    break
            curr_row += 5
        if has_free:
            available_dates.append(date_str)
    return available_dates[:6]