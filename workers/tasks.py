import asyncio
from datetime import datetime, timedelta
# pyrefly: ignore [missing-import]
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импортируем наши настройки и базы
from config import ADMIN_IDS, SKIP_ROWS
from services.db import supabase, last_seen_doctors, save_state, get_admin_settings
from services.sheets import (
    schedule_sheet, 
    get_monday_str, 
    get_col_idx, 
    get_appointment_duration, 
    get_schedule_report
)

force_sync_event = asyncio.Event()
pending_sync_dates = []


# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: Дедупликация ---
def _record_already_exists(date_str: str, row_idx_str: str) -> bool:
    """
    Проверяет, существует ли в БД хотя бы одна запись с данной датой и row_idx.
    Возвращает True, если дубликат уже есть — INSERT нужно пропустить.
    Это КРИТИЧЕСКАЯ защита: без неё бот плодит тысячи копий одной записи.
    """
    try:
        # row_idx в базе данных Supabase имеет тип integer (число).
        # Обязательно преобразуем к int для корректного поиска.
        row_idx_val = int(row_idx_str)
        res = (
            supabase.table("appointments")
            .select("id")
            .eq("date", date_str)
            .eq("row_idx", row_idx_val)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        # При ошибке SELECT — безопасно пропускаем INSERT, чтобы не плодить дубли
        print(f"⚠️ [DEDUP] Ошибка проверки дубликата ({date_str}, row {row_idx_str}): {e}", flush=True)
        return True


# --- 1. МОНИТОРИНГ И СИНХРОНИЗАЦИЯ ---
async def monitor_and_sync_entries(bot: Bot):
    print("🤖 [SYSTEM] Запуск повного циклу моніторингу...", flush=True)
    
    while True:
        try:
            if pending_sync_dates:
                target_str = pending_sync_dates.pop(0)
                try:
                    now = datetime.strptime(target_str, "%d.%m.%Y")
                    print(f"📅 [SYSTEM] Отримано запит на синхронізацію конкретного тижня для дати: {target_str}", flush=True)
                except Exception as e:
                    print(f"⚠️ [SYSTEM] Помилка парсингу дати {target_str}: {e}", flush=True)
                    now = datetime.now() + timedelta(hours=2)
            else:
                now = datetime.now() + timedelta(hours=2)
                
            state_changed = False
            cached_sheets_data = {}
            
            # Находим понедельник целевой недели
            monday_of_week = now - timedelta(days=now.weekday())
            scan_dates = []
            for day_offset in range(7):
                target_date = monday_of_week + timedelta(days=day_offset)
                if target_date.weekday() == 6: continue 
                scan_dates.append(target_date.strftime("%d.%m.%Y"))

            # Запрашиваем из БД записи ТОЛЬКО за сканируемые даты (чтобы не упираться в лимит 1000 строк)
            db_res = (
                supabase.table("appointments")
                .select("*")
                .filter("status", "in", '("confirmed", "pending")')
                .in_("date", scan_dates)
                .execute()
            )

            active_db_records = {}
            for r in db_res.data:
                key = (r['date'], str(r['row_idx']))
                if key not in active_db_records:
                    active_db_records[key] = r

            found_ids = set()

            try:
                available_sheets = [s.title for s in schedule_sheet.worksheets()]
                print(f"📋 Доступні листи в Excel: {available_sheets}", flush=True)
            except Exception as e:
                print(f"❌ [DATABASE ERROR] Не вдалося отримати список листів: {e}", flush=True)
                await asyncio.sleep(60)
                continue

            for day_offset in range(7):
                target_date = monday_of_week + timedelta(days=day_offset)
                if target_date.weekday() == 6: continue 
                
                date_str = target_date.strftime("%d.%m.%Y")
                sheet_name = get_monday_str(target_date) 

                matching_sheet = next((s for s in available_sheets if s.strip() == sheet_name), None)
                if not matching_sheet:
                    continue 

                if matching_sheet not in cached_sheets_data:
                    try:
                        ws = schedule_sheet.worksheet(matching_sheet)
                        cached_sheets_data[matching_sheet] = ws.get_all_values()
                    except Exception as e:
                        print(f"⚠️ [SHEETS ERROR] Помилка читання листа {matching_sheet}: {e}", flush=True)
                        continue
                
                data = cached_sheets_data[matching_sheet]
                col_idx = get_col_idx(target_date) - 1
                curr_row = 4
                slots_to_skip = 0

                while curr_row < len(data):
                    if (curr_row + 1) in SKIP_ROWS:
                        curr_row += 1
                        continue
                    if curr_row + 4 >= len(data): break 
                    
                    if slots_to_skip > 0:
                        slots_to_skip -= 1
                        curr_row += 5
                        continue

                    patient_info = data[curr_row + 1][col_idx].strip()
                    time_val = data[curr_row][col_idx - 1].strip()
                    sheet_stage = data[curr_row + 4][col_idx].strip()
                    row_idx_str = str(curr_row + 1)

                    # КРИТИЧНО: doctor_name читаем из ячейки ВРАЧА (row+2), а НЕ пациента.
                    # "Данило" — это врач, не пациент. Не перепутать!
                    doctor_name = data[curr_row + 2][col_idx].strip() or "Не вказано"

                    # КРИТИЧНО: patient_info (row+1) может содержать номер телефона,
                    # дописанный вручную прямо в ячейку с ФИО. Мы храним его как есть —
                    # не парсим и не разделяем. Телефон в БД пишется ТОЛЬКО если пациент
                    # сам записался через бота (тогда phone != "Ручний запис").
                    
                    slot_key = (date_str, row_idx_str)

                    if patient_info:
                        is_danilo = "данило" in doctor_name.lower()
                        is_kalashnikov = "калашников" in doctor_name.lower()

                        # ================================================================
                        # ВЕТКА А: Врачи — ДАНИЛО или КАЛАШНИКОВ
                        # Действия: ТОЛЬКО уведомление о новой записи + один INSERT.
                        # Синхронизация статусов — ПОЛНОСТЬЮ ЗАПРЕЩЕНА.
                        # ================================================================
                        if is_danilo or is_kalashnikov:
                            doc_identifier = "ДАНИЛО" if is_danilo else "КАЛАШНИКОВ"
                            alert_key = f"alert_{doc_identifier}_{date_str}_{row_idx_str}_{patient_info}"
                            
                            if last_seen_doctors.get(alert_key) != "sent":
                                print(f"🎯 [HIT] Знайдено запис до {doc_identifier}: {patient_info} на {date_str}", flush=True)
                                
                                for admin_id in ADMIN_IDS:
                                    try:
                                        try:
                                            settings = await get_admin_settings(admin_id)
                                            is_enabled = settings.get("track_danilo", True)
                                        except:
                                            is_enabled = True

                                        if is_enabled:
                                            alert_msg = (
                                                f"‼️‼️‼️ <b>НОВИЙ ЗАПИС: {doc_identifier}</b> ‼️‼️‼️\n\n"
                                                f"📅 Дата: <b>{date_str}</b>\n"
                                                f"🕒 Час: <b>{time_val}</b>\n"
                                                f"📝 Пацієнт: <code>{patient_info}</code>\n"
                                                f"👨‍⚕️ Лікар: {doctor_name}"
                                            )
                                            await bot.send_message(admin_id, alert_msg, parse_mode="HTML")
                                    except Exception as e:
                                        print(f"❌ Помилка відправки Telegram ({doc_identifier}): {e}", flush=True)

                                # --- ДЕДУПЛИКАЦИЯ: INSERT только если записи ещё нет ---
                                # Проверяем по (date, row_idx) — не по alert_key!
                                # Это защищает от накопления тысяч дублей при рестарте бота.
                                if slot_key not in active_db_records and not _record_already_exists(date_str, row_idx_str):
                                    new_appt = {
                                        "user_id": 0,
                                        "name": patient_info,
                                        "service": data[curr_row][col_idx].strip(),
                                        "anesthesia": data[curr_row + 3][col_idx].strip(),
                                        "doctor": doctor_name,
                                        "phone": "Ручний запис",
                                        "date": date_str,
                                        "time": time_val,
                                        "row_idx": int(row_idx_str),
                                        "status": "confirmed",
                                        "execution_stage": "Запланировано",
                                    }
                                    res = supabase.table("appointments").insert(new_appt).execute()
                                    if res.data:
                                        inserted_id = res.data[0]['id']
                                        found_ids.add(inserted_id)
                                        active_db_records[slot_key] = res.data[0]
                                        print(f"✅ [{doc_identifier} INSERT] Запис додано: id={inserted_id}, {date_str} {time_val}", flush=True)
                                    state_changed = True
                                elif slot_key in active_db_records:
                                    found_ids.add(active_db_records[slot_key]['id'])
                                else:
                                    print(f"⏭️ [{doc_identifier} SKIP] Дубль проігноровано: {date_str}, row={row_idx_str}", flush=True)
                                    try:
                                        exist_res = (
                                            supabase.table("appointments")
                                            .select("id")
                                            .eq("date", date_str)
                                            .eq("row_idx", int(row_idx_str))
                                            .limit(1)
                                            .execute()
                                        )
                                        if exist_res.data:
                                            found_ids.add(exist_res.data[0]['id'])
                                    except Exception:
                                        pass

                                last_seen_doctors[alert_key] = "sent"
                                state_changed = True
                            else:
                                # Уведомление уже было отправлено ранее.
                                # Просто отмечаем слот как найденный, чтобы не помечать его cancelled.
                                if slot_key in active_db_records:
                                    found_ids.add(active_db_records[slot_key]['id'])
                                else:
                                    try:
                                        exist_res = (
                                            supabase.table("appointments")
                                            .select("id")
                                            .eq("date", date_str)
                                            .eq("row_idx", int(row_idx_str))
                                            .limit(1)
                                            .execute()
                                        )
                                        if exist_res.data:
                                            found_ids.add(exist_res.data[0]['id'])
                                    except Exception:
                                        pass

                        # ================================================================
                        # ВЕТКА Б: Врач — ТЕТЕРНИК (или любой другой)
                        # Действия: полный цикл — INSERT новых + синхронизация статусов.
                        # ================================================================
                        else:
                            if slot_key in active_db_records:
                                rec = active_db_records[slot_key]
                                found_ids.add(rec['id'])
                                
                                db_stage = rec.get('execution_stage', '')
                                db_doctor = rec.get('doctor', '')
                                
                                sync_data = {}
                                
                                if sheet_stage and sheet_stage != db_stage:
                                    should_sync_stage = True
                                    if db_stage == "In_Progress_Notified" and sheet_stage == "Запланировано":
                                        should_sync_stage = False
                                    elif db_stage == "Wait_Finish_Click" and sheet_stage != "Выполенено":
                                        should_sync_stage = False
                                    
                                    if should_sync_stage:
                                        sync_data["execution_stage"] = sheet_stage
                                        active_db_records[slot_key]['execution_stage'] = sheet_stage
                                        
                                if doctor_name and doctor_name != "Не вказано" and doctor_name != db_doctor:
                                    sync_data["doctor"] = doctor_name
                                    active_db_records[slot_key]['doctor'] = doctor_name
                                    
                                if sync_data:
                                    supabase.table("appointments").update(sync_data).eq("id", rec['id']).execute()
                                    
                                    # If only doctor changed, we don't necessarily need to spam notifications, 
                                    # but we process the stage notification if stage was updated
                                    if "execution_stage" in sync_data:
                                        for admin_id in ADMIN_IDS:
                                            try:
                                                settings = await get_admin_settings(admin_id)
                                                if settings.get("sync_notifications", True):
                                                    sync_msg = (
                                                        f"🔄 <b>Синхронізація з таблицею</b>\n\n"
                                                        f"Пацієнт: {patient_info}\nЧас: {time_val}\n"
                                                        f"Статус змінено на: <b>{sheet_stage}</b>"
                                                    )
                                                    await bot.send_message(admin_id, sync_msg, parse_mode="HTML")
                                            except:
                                                pass
                            else:
                                # Новий ручний запис — ДЕДУПЛИКАЦИЯ перед INSERT
                                if not _record_already_exists(date_str, row_idx_str):
                                    new_appt = {
                                        "user_id": 0,
                                        "name": patient_info,
                                        "service": data[curr_row][col_idx].strip(),
                                        "anesthesia": data[curr_row + 3][col_idx].strip(),
                                        "doctor": doctor_name,
                                        "phone": "Ручний запис",
                                        "date": date_str,
                                        "time": time_val,
                                        "row_idx": int(row_idx_str),
                                        "status": "confirmed",
                                        "execution_stage": sheet_stage if sheet_stage else "Запланировано",
                                    }
                                    res = supabase.table("appointments").insert(new_appt).execute()
                                    if res.data:
                                        inserted_id = res.data[0]['id']
                                        found_ids.add(inserted_id)
                                        active_db_records[slot_key] = res.data[0]
                                    state_changed = True
                                else:
                                    print(f"⏭️ [TETERNIK SKIP] Дубль проігноровано: {date_str}, row={row_idx_str}", flush=True)
                                    try:
                                        exist_res = (
                                            supabase.table("appointments")
                                            .select("id")
                                            .eq("date", date_str)
                                            .eq("row_idx", int(row_idx_str))
                                            .limit(1)
                                            .execute()
                                        )
                                        if exist_res.data:
                                            found_ids.add(exist_res.data[0]['id'])
                                    except Exception:
                                        pass

                    curr_row += 5

            # Скасовані записи — отменяем только будущие, не прошлые
            for slot_key, db_record in active_db_records.items():
                if db_record['id'] not in found_ids:
                    appt_date = datetime.strptime(db_record['date'], "%d.%m.%Y")
                    if appt_date.date() >= now.date():
                        supabase.table("appointments").update({"status": "cancelled"}).eq("id", db_record['id']).execute()
                        state_changed = True

            if state_changed:
                save_state(last_seen_doctors)

        except Exception as e:
            print(f"🔥 [CRITICAL ERROR] Помилка в циклі моніторингу: {e}", flush=True)
        
        try:
            await asyncio.wait_for(force_sync_event.wait(), timeout=60.0)
            force_sync_event.clear()
            print("🔔 [SYSTEM] Отримано сигнал примусової синхронізації!", flush=True)
        except asyncio.TimeoutError:
            pass

# --- 2. ЕЖЕДНЕВНЫЙ ОТЧЕТ АДМИНУ ---
async def daily_scheduler(bot: Bot):
    while True:
        now = datetime.now() + timedelta(hours=2)
        target = now.replace(hour=6, minute=0, second=0, microsecond=0)
        
        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        today = datetime.now() + timedelta(hours=2)
        report_text = await get_schedule_report(today)

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"☀️ <b>Ранішній звіт</b>\n\n{report_text}", parse_mode="HTML")
            except: pass

# --- 3. ПЛАНИРОВЩИК НАПОМИНАНИЙ ПАЦИЕНТАМ ---
async def reminder_scheduler(bot: Bot):
    while True:
        try:
            now = datetime.now() + timedelta(hours=2)
            response = supabase.table("appointments").select("*").eq("status", "confirmed").execute()
            rows = response.data

            for row in rows:
                user_id = row.get('user_id')
                if not user_id or user_id == 0:
                    continue

                appt_dt = datetime.strptime(f"{row['date']} {row['time']}", "%d.%m.%Y %H:%M")
                diff = appt_dt - now

                msg_base = f"⏰ Нагадування про прийом:\n📅 Дата: {row['date']}\n🕒 Час: {row['time']}\n\nВи будете?"

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Так, буду", callback_data=f"rem_yes:{row['id']}"),
                     InlineKeyboardButton(text="❌ Ні, не зможу", callback_data=f"rem_no:{row['id']}")]
                ])

                if timedelta(hours=20) < diff <= timedelta(hours=24) and not row['remind_day_sent']:
                    await bot.send_message(user_id, f"Нагадування про прийом:\n{msg_base}", reply_markup=kb)
                    supabase.table("appointments").update({"remind_day_sent": True}).eq("id", row['id']).execute()

                elif now.hour == 6 and appt_dt.date() == now.date() and not row['remind_morning_sent']:
                    await bot.send_message(user_id, f"☀️ Доброго ранку! Сьогодні чекаємо на вас.\n{msg_base}", reply_markup=kb)
                    supabase.table("appointments").update({"remind_morning_sent": True}).eq("id", row['id']).execute()
                
                elif timedelta(minutes=50) < diff < timedelta(minutes=70) and not row['remind_hour_sent']:
                    await bot.send_message(user_id, f"⚡️ Через годину ваш прийом!\n{msg_base}", reply_markup=kb)    
                    supabase.table("appointments").update({"remind_hour_sent": True}).eq("id", row['id']).execute()

        except Exception as e:
            print(f"Помилка планувальника: {e}")
        
        await asyncio.sleep(600)

# --- 4. МОНИТОРИНГ ЭТАПОВ ПРИЕМА (только Тетерник) ---
async def execution_monitor(bot: Bot):
    while True:
        try:
            now = datetime.now() + timedelta(hours=2)
            today_str = now.strftime("%d.%m.%Y")
            
            # Запрос строго по врачу "Тетерник" — Данило сюда не попадает никогда
            res = supabase.table("appointments").select("*")\
                .eq("date", today_str)\
                .eq("doctor", "Тетерник")\
                .eq("status", "confirmed")\
                .neq("execution_stage", "Выполенено").execute()
            
            for row in res.data:
                if row['execution_stage'] in ["Tracking_Stopped", "Выполенено"]:
                    continue

                appt_id = row['id']
                appt_time = datetime.strptime(f"{row['date']} {row['time']}", "%d.%m.%Y %H:%M")
                duration = get_appointment_duration(row['service'], row['anesthesia'])
                finish_time = appt_time + timedelta(minutes=duration)
                stop_btn = [InlineKeyboardButton(text="🔕 Зупинити відслідкування", callback_data=f"stop_track:{appt_id}")]

                if now >= (appt_time - timedelta(minutes=5)) and row['execution_stage'] == "Запланировано":
                    kb_buttons = [
                        [InlineKeyboardButton(text="🚀 Виконується прийом", callback_data=f"set_st:Выполняется:{appt_id}")],
                        stop_btn
                    ]
                    if row['anesthesia'] and "Наркоз" in row['anesthesia']:
                        kb_buttons.insert(0, [InlineKeyboardButton(text="💉 Чи у анестезіолога", callback_data=f"set_st:У анестезиолога:{appt_id}")])
                    
                    for admin_id in ADMIN_IDS:
                        settings = await get_admin_settings(admin_id)
                        if settings.get("execution_notifications", True):
                            try:
                                await bot.send_message(
                                    admin_id, 
                                    f"🔔 <b>Час прийому! (Тетерник)</b>\n👤 Пацієнт: {row['name']}\n🕒 Початок: {row['time']}\n🩺 Послуга: {row['service']}\n\nОберіть статус:", 
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons), 
                                    parse_mode="HTML"
                                )
                            except: pass
                    supabase.table("appointments").update({"execution_stage": "In_Progress_Notified"}).eq("id", appt_id).execute()

                if now >= finish_time and row['execution_stage'] in ["Выполняется", "У анестезиолога", "In_Progress_Notified"]:
                    for admin_id in ADMIN_IDS:
                        settings = await get_admin_settings(admin_id)
                        if settings.get("execution_notifications", True):
                            try:
                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="✅ Відмітити виконаним", callback_data=f"set_st:Выполенено:{appt_id}")],
                                    stop_btn
                                ])
                                await bot.send_message(
                                    admin_id, 
                                    f"🏁 <b>Час вийшов!</b>\nПотрібно відмітити виконання запису Тетерника.\n👤 Пацієнт: {row['name']}\n🕒 Початок був о: {row['time']}", 
                                    reply_markup=kb, 
                                    parse_mode="HTML"
                                )
                            except: pass 
                    supabase.table("appointments").update({"execution_stage": "Wait_Finish_Click"}).eq("id", appt_id).execute()

        except Exception as e:
            print(f"Error in execution_monitor: {e}")
        await asyncio.sleep(30)
