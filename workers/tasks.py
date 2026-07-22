import asyncio
from datetime import datetime, timedelta
# pyrefly: ignore [missing-import]
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto

# Импортируем наши настройки и базы
from config import ADMIN_IDS, SKIP_ROWS
from services.db import supabase, last_seen_doctors, save_state, get_admin_settings
from services.sheets import (
    schedule_sheet, 
    get_monday_str, 
    get_col_idx, 
    get_appointment_duration, 
    get_schedule_report,
    get_raw_appointments
)
from services.renderer import generate_schedule_image

import re

force_sync_event = asyncio.Event()
pending_sync_dates = []

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: Парсинг пациента ---
def parse_patient_data(raw_text):
    """
    Извлекает номер телефона из строки пациента и возвращает (имя, телефон).
    Поддерживает украинские форматы: 050..., +380..., 380... с любыми разделителями.
    """
    if not raw_text:
        return "", None
        
    # Ищем последовательность, которая похожа на укр. номер: опциональный +38, опциональные скобки/дефисы, затем 0 и еще минимум 8 цифр
    pattern = r'[\s\(\[-]*?(?:\+?38)?[\s\-\(]*0[\d\s\-\(\)]{8,}\d[\s\)\]]*'
    matches = re.finditer(pattern, raw_text)
    
    for match in matches:
        extracted = match.group(0)
        digits_only = re.sub(r'[^\d]', '', extracted)
        
        # Украинские номера: 10 цифр (050...) или 12 цифр (38050...)
        if len(digits_only) in (10, 12):
            clean_phone = re.sub(r'[^\d\+]', '', extracted)
            
            # Удаляем номер из исходной строки
            clean_name = raw_text.replace(extracted, ' ')
            
            # Убираем двойные пробелы
            clean_name = re.sub(r'\s+', ' ', clean_name).strip()
            
            # Подчищаем висячие символы по краям
            clean_name = clean_name.strip(',.-()[] ')
            
            return clean_name, clean_phone
            
    return raw_text.strip(), None


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

                    raw_patient_info = data[curr_row + 1][col_idx].strip()
                    patient_info, parsed_phone = parse_patient_data(raw_patient_info)
                    
                    time_val = data[curr_row][col_idx - 1].strip()
                    sheet_stage = data[curr_row + 4][col_idx].strip()
                    row_idx_str = str(curr_row + 1)

                    # КРИТИЧНО: doctor_name читаем из ячейки ВРАЧА (row+2), а НЕ пациента.
                    # "Данило" — это врач, не пациент. Не перепутать!
                    doctor_name = data[curr_row + 2][col_idx].strip() or "Не вказано"
                    
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
                                        "phone": parsed_phone if parsed_phone else "Ручний запис",
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
                                    rec = active_db_records[slot_key]
                                    found_ids.add(rec['id'])
                                    
                                    db_doctor = rec.get('doctor', '')
                                    db_stage = rec.get('execution_stage', '')
                                    sync_data = {}
                                    
                                    if doctor_name and doctor_name != "Не вказано" and doctor_name != db_doctor:
                                        sync_data["doctor"] = doctor_name
                                    if sheet_stage and sheet_stage != db_stage:
                                        sync_data["execution_stage"] = sheet_stage
                                        
                                    if sync_data:
                                        supabase.table("appointments").update(sync_data).eq("id", rec['id']).execute()
                                        if "doctor" in sync_data:
                                            active_db_records[slot_key]['doctor'] = sync_data["doctor"]
                                        if "execution_stage" in sync_data:
                                            active_db_records[slot_key]['execution_stage'] = sync_data["execution_stage"]
                                        state_changed = True
                                        print(f"🔄 Оновлено Данило/Калашніков {rec['id']}: {sync_data}", flush=True)
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
                                if slot_key in active_db_records:
                                    rec = active_db_records[slot_key]
                                    found_ids.add(rec['id'])
                                    
                                    db_doctor = rec.get('doctor', '')
                                    db_stage = rec.get('execution_stage', '')
                                    sync_data = {}
                                    
                                    if doctor_name and doctor_name != "Не вказано" and doctor_name != db_doctor:
                                        sync_data["doctor"] = doctor_name
                                    if sheet_stage and sheet_stage != db_stage:
                                        sync_data["execution_stage"] = sheet_stage
                                        
                                    if sync_data:
                                        supabase.table("appointments").update(sync_data).eq("id", rec['id']).execute()
                                        if "doctor" in sync_data:
                                            active_db_records[slot_key]['doctor'] = sync_data["doctor"]
                                        if "execution_stage" in sync_data:
                                            active_db_records[slot_key]['execution_stage'] = sync_data["execution_stage"]
                                        state_changed = True
                                        print(f"🔄 Оновлено Данило/Калашніков {rec['id']}: {sync_data}", flush=True)
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
                                        "phone": parsed_phone if parsed_phone else "Ручний запис",
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
                    # Заявки с сайта не имеют row_idx, их не нужно отменять при синхронизации с Google Sheets
                    if db_record.get('row_idx') is not None:
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
        date_str = today.strftime("%d.%m.%Y")
        
        # 1. Сначала ВСЕГДА отправляем текстовый отчет всем администраторам
        report_text = await get_schedule_report(today)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"☀️ <b>Ранішній звіт</b>\n\n{report_text}", parse_mode="HTML")
            except: pass
            
        # 2. Пытаемся дополнительно отправить картинку(и), разбивая приемы по 6 штук
        try:
            raw_appts = get_raw_appointments(today)
            if raw_appts:
                chunk_size = 6
                chunks = [raw_appts[i:i + chunk_size] for i in range(0, len(raw_appts), chunk_size)]
                
                # Создаем список потоков для каждой страницы
                photo_streams = []
                for index, chunk in enumerate(chunks):
                    page_num = index + 1
                    photo_stream = generate_schedule_image(date_str, chunk, page=page_num, total_pages=len(chunks))
                    photo_streams.append((page_num, photo_stream))
                
                for admin_id in ADMIN_IDS:
                    try:
                        media_group = []
                        for page_num, photo_stream in photo_streams:
                            photo_stream.seek(0) # Сбрасываем указатель
                            photo_file = BufferedInputFile(
                                photo_stream.getvalue(), 
                                filename=f"schedule_{date_str}_p{page_num}.png"
                            )
                            caption_str = f"🖼 Частина {page_num} з {len(chunks)}" if len(chunks) > 1 else f"📅 Візуальний розклад на {date_str}"
                            media_group.append(InputMediaPhoto(media=photo_file, caption=caption_str))
                        
                        if media_group:
                            await bot.send_media_group(chat_id=admin_id, media=media_group)
                    except Exception as ex:
                        print(f"Помилка відправки альбому утреннего отчета для {admin_id}: {ex}")
        except Exception as e:
            print(f"Помилка генерации картинок для утреннего отчета: {e}")

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


# --- 5. МОНИТОРИНГ ИЗМЕНЕНИЙ СТАТУСА И УВЕДОМЛЕНИЕ ПАЦИЕНТОВ ---
async def status_notification_monitor(bot: Bot):
    print("🤖 [SYSTEM] Запуск моніторингу оновлень статусів в БД...", flush=True)
    while True:
        try:
            today = datetime.now() + timedelta(hours=2)
            today_date = today.date()

            # 0. Новые заявки с сайта (из таблицы site_leads)
            try:
                res_new = supabase.table("site_leads").select("*").eq("notified", False).execute()
                for row in res_new.data:
                    for admin_id in ADMIN_IDS:
                        try:
                            msg = (
                                f"🌐 <b>НОВА ЗАЯВКА З САЙТУ!</b>\n\n"
                                f"👤 Пацієнт: <b>{row.get('name', '—')}</b>\n"
                                f"📞 Телефон: <code>{row.get('phone', '—')}</code>\n"
                                f"🩺 Послуга: {row.get('service', '—')}\n"
                                f"💬 Коментар: {row.get('comment') or '—'}"
                            )
                            await bot.send_message(admin_id, msg, parse_mode="HTML")
                        except Exception as e:
                            print(f"❌ [NOTIFIER] Помилка відправки адміну про нову заявку: {e}", flush=True)
                    
                    # Отмечаем заявку как уведомленную
                    supabase.table("site_leads").update({"notified": True}).eq("id", row["id"]).execute()
            except Exception as e:
                print(f"🔥 [NOTIFIER ERROR] Помилка при перевірці нових заявок з сайту: {e}", flush=True)

            # 1. Записи со статусом "confirmed", но без установленного execution_stage
            # (подтвержденные из веб-панели администрирования)
            res_confirmed = supabase.table("appointments").select("*").eq("status", "confirmed").is_("execution_stage", "null").execute()
            
            for row in res_confirmed.data:
                should_notify = False
                try:
                    appt_date = datetime.strptime(row['date'], "%d.%m.%Y").date()
                    if appt_date >= today_date:
                        should_notify = True
                except Exception as ex:
                    print(f"⚠️ [NOTIFIER] Помилка розбору дати {row.get('date')} для id={row['id']}: {ex}", flush=True)

                user_id = row.get("user_id")
                if should_notify and user_id and user_id != 0:
                    service_db = row.get("service") or ""
                    from config import REVERSE_SERVICE_MAP
                    full_service = REVERSE_SERVICE_MAP.get(service_db, service_db)
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ Ваш запис на <b>{full_service}</b> підтверджено! Чекаємо на вас.",
                            parse_mode="HTML"
                        )
                        print(f"✉️ [NOTIFIER] Надіслано сповіщення про підтвердження: id={row['id']}, user_id={user_id}", flush=True)
                    except Exception as e:
                        print(f"⚠️ [NOTIFIER] Помилка відправки сповіщення про підтвердження для id={row['id']}, user_id={user_id}: {e}", flush=True)
                
                # Обновляем execution_stage
                try:
                    supabase.table("appointments").update({"execution_stage": "Запланировано"}).eq("id", row["id"]).execute()
                except Exception as e:
                    print(f"❌ [NOTIFIER] Помилка оновлення execution_stage для id={row['id']}: {e}", flush=True)

            # 2. Записи со статусом "cancelled", у которых execution_stage не в ['Cancelled_Notified', 'Cancelled_By_User']
            # (отмененные из веб-панели или Sheets, о которых мы еще не уведомили)
            res_cancelled = supabase.table("appointments").select("*").eq("status", "cancelled").execute()
            
            for row in res_cancelled.data:
                stage = row.get("execution_stage")
                if stage not in ["Cancelled_Notified", "Cancelled_By_User"]:
                    should_notify = False
                    try:
                        appt_date = datetime.strptime(row['date'], "%d.%m.%Y").date()
                        if appt_date >= today_date:
                            should_notify = True
                    except Exception as ex:
                        print(f"⚠️ [NOTIFIER] Помилка розбору дати {row.get('date')} для id={row['id']}: {ex}", flush=True)

                    user_id = row.get("user_id")
                    if should_notify and user_id and user_id != 0:
                        try:
                            await bot.send_message(
                                user_id,
                                "❌ На жаль, ваш запис було відхилено. Будь ласка, оберіть інший час."
                            )
                            print(f"✉️ [NOTIFIER] Надіслано сповіщення про скасування: id={row['id']}, user_id={user_id}", flush=True)
                        except Exception as e:
                            print(f"⚠️ [NOTIFIER] Помилка відправки сповіщення про скасування для id={row['id']}, user_id={user_id}: {e}", flush=True)
                    
                    # Обновляем execution_stage
                    try:
                        supabase.table("appointments").update({"execution_stage": "Cancelled_Notified"}).eq("id", row["id"]).execute()
                    except Exception as e:
                        print(f"❌ [NOTIFIER] Помилка оновлення execution_stage для id={row['id']}: {e}", flush=True)
                        
        except Exception as e:
            print(f"🔥 [NOTIFIER ERROR] Помилка в циклі моніторингу статусів: {e}", flush=True)
            
        await asyncio.sleep(15)


# --- 6. ШЕДУЛЕР ТА ВИКОНАННЯ РОЗСИЛОК (BROADCASTS) ---
async def execute_broadcast_delivery(bot: Bot, broadcast_data: dict, target_user_ids: list = None) -> dict:
    """
    Виконує масову розсилку повідомлення заданому списку користувачів або всім користувачам бота.
    """
    from services.db import get_all_bot_user_ids, save_broadcast
    
    if target_user_ids is None:
        target_user_ids = get_all_bot_user_ids(exclude_admins=True)
        
    text = broadcast_data.get("text", "")
    photo_id = broadcast_data.get("photo_id")
    btn_text = broadcast_data.get("btn_text")
    btn_url = broadcast_data.get("btn_url")
    
    kb = None
    if btn_text and btn_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=btn_text, url=btn_url)]
        ])
        
    sent_count = 0
    failed_count = 0
    total = len(target_user_ids)
    
    for uid in target_user_ids:
        try:
            if photo_id:
                await bot.send_photo(chat_id=uid, photo=photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
            else:
                await bot.send_message(chat_id=uid, text=text, parse_mode="HTML", reply_markup=kb)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            print(f"⚠️ [BROADCAST] Не вдалося надіслати {uid}: {e}", flush=True)
            
        await asyncio.sleep(0.05)  # Ліміт Telegram: ~20 пов/сек
        
    stats = {
        "total": total,
        "sent": sent_count,
        "failed": failed_count,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    broadcast_data["status"] = "completed"
    broadcast_data["stats"] = stats
    save_broadcast(broadcast_data)
    
    return stats


async def broadcast_scheduler(bot: Bot):
    """
    Фонова задача перевірки запланованих розсилок.
    """
    from services.db import get_scheduled_broadcasts
    print("🤖 [BROADCAST] Запуск шедулера розсилок...", flush=True)
    
    while True:
        try:
            scheduled_bcs = get_scheduled_broadcasts()
            now = datetime.now()
            
            for bc in scheduled_bcs:
                scheduled_time_str = bc.get("scheduled_at")
                if not scheduled_time_str:
                    continue
                    
                try:
                    sch_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%d %H:%M")
                except Exception:
                    try:
                        sch_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue
                        
                if sch_dt <= now:
                    print(f"📢 [BROADCAST] Запуск запланованої розсилки {bc.get('id')}...", flush=True)
                    stats = await execute_broadcast_delivery(bot, bc)
                    
                    # Сповіщаємо адмінів про завершення
                    report = (
                        f"✅ <b>Запланована розсилка завершена!</b>\n\n"
                        f"📊 Статистика:\n"
                        f"👥 Всього отримувачів: <b>{stats['total']}</b>\n"
                        f"✅ Успішно доставлено: <b>{stats['sent']}</b>\n"
                        f"❌ Не доставлено (помилка/блок): <b>{stats['failed']}</b>"
                    )
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id, report, parse_mode="HTML")
                        except Exception:
                            pass
        except Exception as e:
            print(f"🔥 [BROADCAST ERROR] Ошибка в шедулере рассылок: {e}", flush=True)
            
        await asyncio.sleep(30)


