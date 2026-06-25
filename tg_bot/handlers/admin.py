import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery

# Импортируем настройки, сервисы и генератор клавиатур
from config import ADMIN_IDS, REVERSE_SERVICE_MAP
from services.db import supabase, update_admin_setting, get_bot_stats
from services.sheets import (
    get_schedule_report, schedule_sheet, get_monday_str,
    get_col_idx, get_next_row_idx, update_sheet_slots, update_execution_stage
)
from tg_bot.keyboards import get_settings_kb

router = Router()

# --- ХЕНДЛЕРЫ НАСТРОЕК ---

@router.callback_query(F.data == "admin_settings")
async def show_settings(callback: CallbackQuery):
    kb = await get_settings_kb(callback.from_user.id)
    await callback.message.edit_caption(caption="⚙️ <b>Персональні налаштування сповіщень:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.in_(["toggle_sync", "toggle_exec", "toggle_danilo"]))
async def toggle_settings(callback: CallbackQuery):
    # Получаем настройки через клавиатуру (которая сама обратится к БД)
    from services.db import get_admin_settings
    settings = await get_admin_settings(callback.from_user.id)
    
    if callback.data == "toggle_sync":
        current = settings.get("sync_notifications", True)
        await update_admin_setting(callback.from_user.id, "sync_notifications", not current)
    elif callback.data == "toggle_exec":
        current = settings.get("execution_notifications", True)
        await update_admin_setting(callback.from_user.id, "execution_notifications", not current)
    elif callback.data == "toggle_danilo":
        current = settings.get("track_danilo", True)
        await update_admin_setting(callback.from_user.id, "track_danilo", not current)
    
    new_kb = await get_settings_kb(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=new_kb)
    await callback.answer("Налаштування оновлено")

# --- ОТЧЕТЫ И СТАТИСТИКА ---

@router.callback_query(F.data == "admin_tomorrow")
async def admin_show_tomorrow(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ заборонено..", show_alert=True)
        return
    
    tomorrow = datetime.now() + timedelta(days=1) + timedelta(hours=2) # Киевское время
    await callback.message.answer("Отримую дані з таблиці... ⏳")
    report_text = await get_schedule_report(tomorrow)

    await callback.message.answer(report_text, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("show_day:"))
async def handle_show_day_from_monitor(callback: CallbackQuery):
    date_str = callback.data.split(":")[1]
    
    try:
        target_date = datetime.strptime(date_str, "%d.%m.%Y")
        await callback.message.answer(f"Отримую розклад на {date_str}... 🔍")

        report_text = await get_schedule_report(target_date)
        await callback.message.answer(report_text, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)

@router.callback_query(F.data == "admin_stats")
async def show_admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    stats_text = await get_bot_stats()
    await callback.message.answer(stats_text, parse_mode="HTML")
    await callback.answer()

# --- ПОДТВЕРЖДЕНИЕ И ОТКЛОНЕНИЕ ЗАПИСЕЙ ---

@router.callback_query(F.data.startswith("ok:"))
async def admin_approve(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]
    res = supabase.table("appointments").select("*").eq("id", appt_id).execute()

    if not res.data:
        await callback.answer("Помилка: запис не знайдено!", show_alert=True)
        return
    
    data = res.data[0]

    try: 
        dt = datetime.strptime(data['date'], "%d.%m.%Y")
        ws = schedule_sheet.worksheet(get_monday_str(dt))
        col = get_col_idx(dt)
        row = int(data['row_idx'])
        hour = int(data['time'].split(":")[0])

        needs_double = (data['service'] in ["Гастро", "Бронхо", "Ректо"]) and (data['anesthesia'] == "Наркоз ")
        full_service = REVERSE_SERVICE_MAP.get(data['service'], data['service'])
        
        # Запис першого слота
        update_sheet_slots(ws, row, col, data)
        
        if needs_double and hour < 11: # Продовження на другий слот
            next_row = get_next_row_idx(row)
            update_sheet_slots(ws, next_row, col, data)

        # ТУТ РАНЬШЕ БЫЛ log_sheet.append_row() - мы его удалили!
        
        supabase.table("appointments").update({"status": "confirmed", "execution_stage": "Запланировано"}).eq("id", appt_id).execute()
        
        await callback.message.bot.send_message(data['user_id'], f"✅ Ваш запис на <b>{full_service}</b> підтверджено! Чекаємо на вас.", parse_mode="HTML")
        await callback.message.edit_text(callback.message.text + f"\n\n✅ <b>ПІДТВЕРДЖЕНО: {full_service}</b>", parse_mode="HTML")
        await callback.answer("Запис підтверджено!")
        
    except Exception as e:
        await callback.answer(f"Помилка Sheets: {e}", show_alert=True)

@router.callback_query(F.data.startswith("no:"))
async def admin_reject(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]

    res = supabase.table("appointments").select("*").eq("id", appt_id).execute()
    if res.data:
        data = res.data[0]
        supabase.table("appointments").update({"status": "rejected"}).eq("id", appt_id).execute()
        
        try:
            await callback.message.bot.send_message(data['user_id'], "❌ На жаль, ваш запис було відхилено. Будь ласка, оберіть інший час.")
        except: pass

    await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ВІДХИЛЕНО</b>", parse_mode="HTML")
    await callback.answer("Відхилено")

# --- МОНИТОРИНГ ЭТАПОВ ВЫПОЛНЕНИЯ ---

@router.callback_query(F.data.startswith("set_st:"))
async def handle_set_stage(callback: CallbackQuery):
    _, stage, appt_id = callback.data.split(":")
    
    res = supabase.table("appointments").select("*").eq("id", appt_id).execute()
    if not res.data: return
    data = res.data[0]
    
    row_idx = int(data['row_idx'])
    hour = int(data['time'].split(":")[0])
    dt = datetime.strptime(data['date'], "%d.%m.%Y")
    ws = schedule_sheet.worksheet(get_monday_str(dt))
    col = get_col_idx(dt)

    await update_execution_stage(appt_id, stage, ws, row_idx, col)
    
    is_double = (data['service'] in ["Гастро", "Бронхо", "Ректо"]) and ("Наркоз" in data['anesthesia'])
    if is_double and hour < 11:
        next_row = get_next_row_idx(row_idx)
        try: ws.update_cell(next_row + 4, col, stage)
        except: pass

    if stage == "Выполенено":
        await callback.message.edit_text(
            f"{callback.message.text}\n\n✅ <b>Статус змінено: {stage}.</b>\n🗑 <i>Повідомлення видалиться через 5 секунд...</i>", 
            parse_mode="HTML",
            reply_markup=None
        )
        await asyncio.sleep(5)
        try: await callback.message.delete()
        except Exception as e: print(f"Не удалось удалить сообщение: {e}")
            
    else:
        await callback.message.edit_text(f"{callback.message.text}\n\n📍 Статус: <b>{stage}</b>", parse_mode="HTML")

@router.callback_query(F.data.startswith("stop_track:"))
async def handle_stop_tracking(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]

    supabase.table("appointments").update({"execution_stage": "Tracking_Stopped"}).eq("id", appt_id).execute()
    
    await callback.answer("Відслідкування вимкнено")
    await callback.message.edit_text(
        f"{callback.message.text}\n\n🔕 <b>Відслідкування зупинено вручну.</b>\n🗑 <i>Повідомлення видалиться через 5 секунд...</i>", 
        parse_mode="HTML",
        reply_markup=None
    )
    
    await asyncio.sleep(5)
    try: await callback.message.delete()
    except Exception as e: print(f"Не удалось удалить сообщение: {e}")