import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from aiogram.fsm.context import FSMContext

# Импортируем настройки, сервисы и генератор клавиатур
from config import ADMIN_IDS, REVERSE_SERVICE_MAP
from services.db import (
    supabase, update_admin_setting, get_bot_stats,
    get_all_bot_user_ids, load_broadcasts, save_broadcast,
    delete_broadcast, get_scheduled_broadcasts
)
from tg_bot.states import BroadcastState
from services.sheets import (
    get_schedule_report, schedule_sheet, get_monday_str,
    get_col_idx, get_next_row_idx, update_sheet_slots, update_execution_stage,
    get_raw_appointments
)
from services.renderer import generate_schedule_image
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
    
    # 1. Сначала ВСЕГДА отправляем текстовый отчет
    report_text = await get_schedule_report(tomorrow)
    await callback.message.answer(report_text, parse_mode="HTML")
    
    # 2. Пытаемся отправить расписание в виде картинок (одним сообщением-альбомом)
    try:
        raw_appts = get_raw_appointments(tomorrow)
        date_str = tomorrow.strftime("%d.%m.%Y")
        
        if raw_appts:
            chunk_size = 6
            chunks = [raw_appts[i:i + chunk_size] for i in range(0, len(raw_appts), chunk_size)]
            media_group = []
            
            for index, chunk in enumerate(chunks):
                page_num = index + 1
                photo_stream = generate_schedule_image(date_str, chunk, page=page_num, total_pages=len(chunks))
                photo_file = BufferedInputFile(
                    photo_stream.getvalue(), 
                    filename=f"schedule_{date_str}_p{page_num}.png"
                )
                caption_str = f"🖼 Частина {page_num} з {len(chunks)}" if len(chunks) > 1 else f"📅 Візуальний розклад на {date_str}"
                media_group.append(InputMediaPhoto(media=photo_file, caption=caption_str))
                
            if media_group:
                await callback.message.bot.send_media_group(chat_id=callback.message.chat.id, media=media_group)
    except Exception as e:
        print(f"Помилка генерації картинки розкладу: {e}")
        
    await callback.answer()

@router.callback_query(F.data.startswith("show_day:"))
async def handle_show_day_from_monitor(callback: CallbackQuery):
    date_str = callback.data.split(":")[1]
    
    try:
        target_date = datetime.strptime(date_str, "%d.%m.%Y")
        await callback.message.answer(f"Отримую розклад на {date_str}... 🔍")

        # 1. Сначала ВСЕГДА отправляем текстовый отчет
        report_text = await get_schedule_report(target_date)
        await callback.message.answer(report_text, parse_mode="HTML")

        # 2. Пытаемся отправить расписание в виде картинок (одним сообщением-альбомом)
        try:
            raw_appts = get_raw_appointments(target_date)
            if raw_appts:
                chunk_size = 6
                chunks = [raw_appts[i:i + chunk_size] for i in range(0, len(raw_appts), chunk_size)]
                media_group = []
                
                for index, chunk in enumerate(chunks):
                    page_num = index + 1
                    photo_stream = generate_schedule_image(date_str, chunk, page=page_num, total_pages=len(chunks))
                    photo_file = BufferedInputFile(
                        photo_stream.getvalue(), 
                        filename=f"schedule_{date_str}_p{page_num}.png"
                    )
                    caption_str = f"🖼 Частина {page_num} з {len(chunks)}" if len(chunks) > 1 else f"📅 Візуальний розклад на {date_str}"
                    media_group.append(InputMediaPhoto(media=photo_file, caption=caption_str))
                    
                if media_group:
                    await callback.message.bot.send_media_group(chat_id=callback.message.chat.id, media=media_group)
        except Exception as e:
            print(f"Помилка генерації картинки розкладу (show_day): {e}")
            
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)
            
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

    # --- СИНХРОНІЗАЦІЯ АДМІНІВ ---
    if data['status'] != 'pending':
        await callback.answer("Ця заявка вже опрацьована іншим адміністратором!", show_alert=True)
        try: await callback.message.edit_text(callback.message.html_text + "\n\n⚠️ <i>Вже опрацьовано.</i>", parse_mode="HTML", reply_markup=None)
        except: pass
        return
    # -----------------------------

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
        
        supabase.table("appointments").update({"status": "confirmed", "execution_stage": "Запланировано"}).eq("id", appt_id).execute()
        
        admin_name = callback.from_user.full_name
        await callback.message.bot.send_message(data['user_id'], f"✅ Ваш запис на <b>{full_service}</b> підтверджено! Чекаємо на вас.", parse_mode="HTML")
        await callback.message.edit_text(callback.message.html_text + f"\n\n✅ <b>ПІДТВЕРДЖЕНО: {full_service}</b>\nОпрацював: <b>{admin_name}</b>", parse_mode="HTML")
        await callback.answer("Запис підтверджено!")
        
    except Exception as e:
        await callback.answer(f"Помилка Sheets: {e}", show_alert=True)

@router.callback_query(F.data.startswith("no:"))
async def admin_reject(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]

    res = supabase.table("appointments").select("*").eq("id", appt_id).execute()
    if not res.data:
        return
        
    data = res.data[0]

    # --- СИНХРОНІЗАЦІЯ АДМІНІВ ---
    if data['status'] != 'pending':
        await callback.answer("Ця заявка вже опрацьована іншим адміністратором!", show_alert=True)
        try: await callback.message.edit_text(callback.message.html_text + "\n\n⚠️ <i>Вже опрацьовано.</i>", parse_mode="HTML", reply_markup=None)
        except: pass
        return
    # -----------------------------
        
    supabase.table("appointments").update({"status": "rejected"}).eq("id", appt_id).execute()
    
    try:
        await callback.message.bot.send_message(data['user_id'], "❌ На жаль, ваш запис було відхилено. Будь ласка, оберіть інший час.")
    except: pass

    admin_name = callback.from_user.full_name
    await callback.message.edit_text(callback.message.html_text + f"\n\n❌ <b>ВІДХИЛЕНО (З СМС пацієнту)</b>\nОпрацював: <b>{admin_name}</b>", parse_mode="HTML")
    await callback.answer("Відхилено")

@router.callback_query(F.data.startswith("del_silent:"))
async def admin_delete_silent(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]

    res = supabase.table("appointments").select("*").eq("id", appt_id).execute()
    if not res.data:
        return
        
    data = res.data[0]

    # --- СИНХРОНІЗАЦІЯ АДМІНІВ ---
    if data['status'] != 'pending':
        await callback.answer("Ця заявка вже опрацьована іншим адміністратором!", show_alert=True)
        try: await callback.message.edit_text(callback.message.html_text + "\n\n⚠️ <i>Вже опрацьовано.</i>", parse_mode="HTML", reply_markup=None)
        except: pass
        return
    # -----------------------------
        
    # Ставимо статус rejected, щоб пропало з Актуальних, але СМС не надсилаємо!
    supabase.table("appointments").update({"status": "rejected"}).eq("id", appt_id).execute()

    admin_name = callback.from_user.full_name
    await callback.message.edit_text(callback.message.html_text + f"\n\n🗑 <b>ВИДАЛЕНО (Без сповіщення)</b>\nОпрацював: <b>{admin_name}</b>", parse_mode="HTML")
    await callback.answer("Видалено тихо")

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

@router.callback_query(F.data.startswith("rev_ok:"))
async def admin_approve_review(callback: CallbackQuery):
    data_parts = callback.data.split(":")
    review_id   = data_parts[1]
    user_id_str = data_parts[2] if len(data_parts) > 2 else "none"
    
    # Парсим user_id — для сайтовых отзывов будет "none"
    user_id = None
    if user_id_str.lower() not in ("none", "null", ""):
        try:
            user_id = int(user_id_str)
        except ValueError:
            user_id = None
            
    # Защита от двойного нажатия
    res = supabase.table("reviews").select("status").eq("id", review_id).execute()
    if res.data and res.data[0]["status"] != "pending":
        await callback.answer("Цей відгук вже опрацьований!", show_alert=True)
        try:
            await callback.message.edit_text(
                callback.message.html_text + "\n\n⚠️ <i>Вже опрацьовано.</i>",
                parse_mode="HTML", reply_markup=None
            )
        except:
            pass
        return
        
    # Меняем статус → approved (отзыв появляется на сайте)
    supabase.table("reviews").update({"status": "approved"}).eq("id", review_id).execute()
    
    # Отправляем ссылку на Google только если пациент из Telegram (есть user_id)
    if user_id:
        google_link = "https://maps.app.goo.gl/syK8CtCDWXQAgRcx7?g_st=ic"
        user_text = (
            "Дякуємо за теплі слова! ❤️\n\n"
            "Будемо дуже вдячні, якщо ви залишите цей відгук на нашій сторінці в Google Maps:\n\n"
            f"👉 {google_link}"
        )
        try:
            await callback.message.bot.send_message(user_id, user_text)
            google_status = "\n✅ <i>Посилання на Google Карти надіслано пацієнту.</i>"
        except Exception:
            google_status = "\n⚠️ <i>Відгук опубліковано, але пацієнт заблокував бота.</i>"
    else:
        # Отзыв с сайта — нет Telegram ID, просто публикуем
        google_status = "\n🌐 <i>Відгук з сайту — посилання на Google не надсилалось.</i>"
        
    admin_name = callback.from_user.full_name
    new_text = (
        callback.message.html_text
        + f"\n\n✅ <b>Опубліковано на сайті.</b>\nОпрацював: <b>{admin_name}</b>{google_status}"
    )
    await callback.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    await callback.answer("Відгук опубліковано!")

@router.callback_query(F.data.startswith("rev_no:"))
async def admin_reject_review(callback: CallbackQuery):
    review_id = callback.data.split(":")[1]
    
    # Защита от двойного нажатия
    res = supabase.table("reviews").select("status").eq("id", review_id).execute()
    if res.data and res.data[0]["status"] != "pending":
        current_status = res.data[0]["status"]
        status_label = "схвалений" if current_status == "approved" else "відхилений"
        await callback.answer("Цей відгук вже опрацьований!", show_alert=True)
        try:
            await callback.message.edit_text(
                callback.message.html_text + f"\n\n⚠️ <i>Вже опрацьовано (статус: {status_label}).</i>",
                parse_mode="HTML", reply_markup=None
            )
        except:
            pass
        return
        
    # Меняем статус в базе
    supabase.table("reviews").update({"status": "rejected"}).eq("id", review_id).execute()
    
    new_text = callback.message.html_text + "\n\n❌ <i>Відгук приховано.</i>"
    await callback.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    await callback.answer("Відгук відхилено.")

# --- АКТУАЛЬНЫЕ ЗАЯВКИ (INBOX АДМИНА) ---

@router.callback_query(F.data == "admin_pending_menu")
async def show_pending_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    # Берем все записи со статусом pending
    appts = supabase.table("appointments").select("id").eq("status", "pending").execute().data
    reviews = supabase.table("reviews").select("id").eq("status", "pending").execute().data
    
    appts_count = len(appts) if appts else 0
    reviews_count = len(reviews) if reviews else 0
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🗓 Записи на прийом ({appts_count})", callback_data="show_pending_appts")],
        [InlineKeyboardButton(text=f"⭐️ Відгуки ({reviews_count})", callback_data="show_pending_reviews")]
    ])
    
    await callback.message.answer(
        f"📥 <b>Панель модерації</b>\n\n"
        f"Нових записів на прийом: <b>{appts_count}</b>\n"
        f"Нових відгуків: <b>{reviews_count}</b>\n\n"
        f"<i>Оберіть, що хочете переглянути:</i>",
        parse_mode="HTML", 
        reply_markup=kb
    )
    await callback.answer()

@router.callback_query(F.data == "show_pending_appts")
async def show_pending_appts(callback: CallbackQuery):
    appts = supabase.table("appointments").select("*").eq("status", "pending").execute().data
    
    if not appts:
        await callback.message.edit_text("✅ Немає необроблених заявок на прийом.")
        return
        
    await callback.message.delete()
    
    # Выдаем админу каждую заявку с ТРЕМЯ кнопками
    for data in appts:
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Підтвердити", callback_data=f"ok:{data['id']}")],
            [InlineKeyboardButton(text="❌ Відхилити (з СМС)", callback_data=f"no:{data['id']}")],
            [InlineKeyboardButton(text="🗑 Видалити (без СМС)", callback_data=f"del_silent:{data['id']}")]
        ])

        admin_text = (
            f"🕒 <b>{data['time']}</b>\n"
            f"📅 <b>{data['date']}</b>\n"
            f"👤 Пацієнт: <b>{data['name']}</b>\n"
            f"📞 Телефон: {data['phone']}\n"
            f"🩺 Послуга: {data['service']}\n"
            f"💉 Наркоз: {data['anesthesia']}\n"
            f"👨‍⚕️ Лікар: {data['doctor']}\n"
            f"🆔 Заявка №: {data['id']}"
        )
        await callback.message.bot.send_message(callback.from_user.id, admin_text, parse_mode="HTML", reply_markup=admin_kb)

@router.callback_query(F.data == "show_pending_reviews")
async def show_pending_reviews(callback: CallbackQuery):
    reviews = supabase.table("reviews").select("*").eq("status", "pending").execute().data
    
    if not reviews:
        await callback.message.edit_text("✅ Немає необроблених відгуків.")
        return
        
    await callback.message.delete()
    
    for r in reviews:
        stars_str = "⭐️" * r['stars']
        admin_text = (
            f"📝 <b>Відгук на модерацію!</b>\n"
            f"👤 {r['user_name']}\n"
            f"Оцінка: {stars_str}\n\n"
            f"💬 <i>«{r['text']}»</i>\n\n"
            f"💡 <i>Якщо ви натиснете «✅ Опублікувати», пацієнту автоматично прийде прохання залишити цей відгук на Google Картах.</i>"
        )
        
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опублікувати", callback_data=f"rev_ok:{r['id']}:{r['user_id']}"),
                InlineKeyboardButton(text="❌ Відхилити", callback_data=f"rev_no:{r['id']}")
            ]
        ])
        await callback.message.bot.send_message(callback.from_user.id, admin_text, parse_mode="HTML", reply_markup=admin_kb)

@router.callback_query(F.data == "admin_panel_menu")
async def show_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    buttons = [
        [InlineKeyboardButton(text="📥 Актуальні заявки", callback_data="admin_pending_menu")],
        [InlineKeyboardButton(text="📈 Розклад на завтра", callback_data="admin_tomorrow")],
        [InlineKeyboardButton(text="📢 Розсилка повідомлень", callback_data="admin_broadcast_menu")],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="⚙️ Налаштування", callback_data="admin_settings")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main_inline")]
    ]
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# ==========================================
# --- СИСТЕМА РОЗСИЛОК (BROADCAST SYSTEM) ---
# ==========================================

def build_broadcast_preview(data: dict, user_count: int):
    text = data.get("text", "<i>(Порожній текст)</i>")
    photo_id = data.get("photo_id")
    btn_text = data.get("btn_text")
    btn_url = data.get("btn_url")

    preview = (
        f"📢 <b>ПОПЕРЕДНІЙ ПЕРЕГЛЯД РОЗСИЛКИ</b>\n"
        f"👥 Отримувачів: <b>{user_count}</b> користувачів\n"
        f"🖼 Фото: {'<b>Додано</b>' if photo_id else 'Ні'}\n"
        f"🔗 Кнопка: {'<b>' + btn_text + '</b>' if btn_text else 'Ні'}\n"
        f"────────────────────────\n\n"
        f"<b>Текст повідомлення:</b>\n{text}"
    )

    btn_action_label = "❌ Видалити кнопку" if btn_text else "🔗 Додати кнопку"
    btn_action_cb = "bc_remove_btn" if btn_text else "bc_add_btn"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Надіслати тестове повідомлення собі", callback_data="bc_test_send")],
        [InlineKeyboardButton(text=btn_action_label, callback_data=btn_action_cb)],
        [
            InlineKeyboardButton(text="🚀 Надіслати зараз", callback_data="bc_send_now_ask"),
            InlineKeyboardButton(text="⏰ Запланувати", callback_data="bc_schedule_ask")
        ],
        [InlineKeyboardButton(text="❌ Скасувати розсилку", callback_data="bc_cancel")]
    ])
    return preview, kb


@router.callback_query(F.data == "admin_broadcast_menu")
async def admin_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()

    scheduled_count = len(get_scheduled_broadcasts())
    users_count = len(get_all_bot_user_ids(exclude_admins=True))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Створити нову розсилку", callback_data="bc_create")],
        [InlineKeyboardButton(text=f"⏰ Заплановані розсилки ({scheduled_count})", callback_data="bc_list_scheduled")],
        [InlineKeyboardButton(text="📜 Історія розсилок", callback_data="bc_history")],
        [InlineKeyboardButton(text="🔙 Назад до адмін-панелі", callback_data="admin_panel_menu")]
    ])

    await callback.message.edit_text(
        f"📢 <b>Управління розсилками</b>\n\n"
        f"👥 Всього потенційних отримувачів: <b>{users_count}</b> осіб\n"
        f"⏰ Заплановано розсилок: <b>{scheduled_count}</b>\n\n"
        f"Оберіть дію нижче:",
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "bc_create")
async def bc_create_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_for_content)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="bc_cancel")]
    ])

    await callback.message.edit_text(
        "📢 <b>Створення нової розсилки</b>\n\n"
        "Надішліть у чат <b>текст</b> або <b>фото з підписом</b> для розсилки.\n\n"
        "💡 <i>Підтримується HTML-форматування (жирний, курсив, посилання).</i>\n"
        "<i>На наступному кроці ви зможете додавати кнопки та протестувати вигляд!</i>",
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.message(BroadcastState.waiting_for_content)
async def process_broadcast_content(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    photo_id = None
    text = ""

    if message.photo:
        photo_id = message.photo[-1].file_id
        text = message.caption or ""
    else:
        text = message.text or ""

    if not text and not photo_id:
        await message.answer("⚠️ Повідомлення не містить тексту або фото. Спробуйте ще раз або натисніть Скасувати.")
        return

    await state.update_data(text=text, photo_id=photo_id, btn_text=None, btn_url=None)

    users_count = len(get_all_bot_user_ids(exclude_admins=True))
    data = await state.get_data()
    preview_text, kb = build_broadcast_preview(data, users_count)

    await message.answer(preview_text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "bc_test_send")
async def bc_test_send(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    text = data.get("text", "")
    photo_id = data.get("photo_id")
    btn_text = data.get("btn_text")
    btn_url = data.get("btn_url")

    kb = None
    if btn_text and btn_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=btn_text, url=btn_url)]
        ])

    try:
        if photo_id:
            await callback.message.bot.send_photo(
                chat_id=callback.from_user.id,
                photo=photo_id,
                caption=f"🧪 <b>[ТЕСТОВЕ ПОВІДОМЛЕННЯ]</b>\n\n{text}",
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            await callback.message.bot.send_message(
                chat_id=callback.from_user.id,
                text=f"🧪 <b>[ТЕСТОВЕ ПОВІДОМЛЕННЯ]</b>\n\n{text}",
                parse_mode="HTML",
                reply_markup=kb
            )
        await callback.answer("✅ Тестове повідомлення надіслано вам у чат!", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Помилка надсилання тесту: {e}", show_alert=True)


@router.callback_query(F.data == "bc_add_btn")
async def bc_add_btn_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_for_button)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад до перегляду", callback_data="bc_back_to_preview")]
    ])

    await callback.message.edit_text(
        "🔗 <b>Додавання кнопки-посилання</b>\n\n"
        "Введіть назву кнопки та URL-адресу через вертикальну риску <code>|</code>.\n\n"
        "Приклад:\n<code>Записатися на сайт | https://endo.kh.ua</code>",
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.message(BroadcastState.waiting_for_button)
async def process_broadcast_button(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    raw_text = message.text or ""
    if "|" not in raw_text:
        await message.answer("❌ <b>Невірний формат!</b> Використовуйте розділювач <code>|</code>.\nПриклад: <code>Назва | https://link.com</code>", parse_mode="HTML")
        return

    parts = raw_text.split("|", 1)
    btn_text = parts[0].strip()
    btn_url = parts[1].strip()

    if not btn_url.startswith("http://") and not btn_url.startswith("https://"):
        await message.answer("❌ <b>Помилка!</b> Посилання має починатися з http:// або https://", parse_mode="HTML")
        return

    await state.update_data(btn_text=btn_text, btn_url=btn_url)
    await state.set_state(BroadcastState.waiting_for_content)

    users_count = len(get_all_bot_user_ids(exclude_admins=True))
    data = await state.get_data()
    preview_text, kb = build_broadcast_preview(data, users_count)

    await message.answer("✅ Кнопку успішно додано!\n\n" + preview_text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "bc_remove_btn")
async def bc_remove_btn(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(btn_text=None, btn_url=None)

    users_count = len(get_all_bot_user_ids(exclude_admins=True))
    data = await state.get_data()
    preview_text, kb = build_broadcast_preview(data, users_count)

    await callback.message.edit_text(preview_text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Кнопку видалено")


@router.callback_query(F.data == "bc_back_to_preview")
async def bc_back_to_preview(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_for_content)

    users_count = len(get_all_bot_user_ids(exclude_admins=True))
    data = await state.get_data()
    preview_text, kb = build_broadcast_preview(data, users_count)

    await callback.message.edit_text(preview_text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "bc_send_now_ask")
async def bc_send_now_ask(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    users = get_all_bot_user_ids(exclude_admins=True)
    users_count = len(users)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ ТАК, НАДІСЛАТИ ВСІМ ({users_count})", callback_data="bc_send_now_confirm")],
        [InlineKeyboardButton(text="❌ Скасувати (повернутися)", callback_data="bc_back_to_preview")]
    ])

    await callback.message.edit_text(
        f"⚠️ <b>УВАГА! ПІДТВЕРДЖЕННЯ РОЗСИЛКИ</b>\n\n"
        f"Ви збираєтеся надіслати це повідомлення <b>ВСІМ {users_count} користувачам</b> бота!\n\n"
        f"🧪 <i>Переконайтеся, що ви протестували повідомлення (натиснувши 'Надіслати тестове повідомлення собі').</i>\n\n"
        f"Ви дійсно бажаєте розпочати масову розсилку прямо зараз?",
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "bc_send_now_confirm")
async def bc_send_now_confirm(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    await state.clear()

    bc_id = f"bc_{int(datetime.now().timestamp())}"
    bc_data = {
        "id": bc_id,
        "creator_id": callback.from_user.id,
        "text": data.get("text", ""),
        "photo_id": data.get("photo_id"),
        "btn_text": data.get("btn_text"),
        "btn_url": data.get("btn_url"),
        "status": "in_progress",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_broadcast(bc_data)

    await callback.message.edit_text(
        "🚀 <b>Розсилка запущена!</b>\n\n"
        "Повідомлення надсилаються користувачам у фоновому режимі.\n"
        "Після завершення вам прийде підсумковий звіт.",
        parse_mode="HTML"
    )
    await callback.answer()

    from workers.tasks import execute_broadcast_delivery
    stats = await execute_broadcast_delivery(callback.message.bot, bc_data)

    report = (
        f"🎉 <b>Масову розсилку завершено!</b>\n\n"
        f"📊 <b>Результати:</b>\n"
        f"👥 Всього отримувачів: <b>{stats['total']}</b>\n"
        f"✅ Успішно доставлено: <b>{stats['sent']}</b>\n"
        f"❌ Не доставлено (блок / помилка): <b>{stats['failed']}</b>"
    )
    try:
        await callback.message.bot.send_message(callback.from_user.id, report, parse_mode="HTML")
    except:
        pass


@router.callback_query(F.data == "bc_schedule_ask")
async def bc_schedule_ask(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_for_time)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Через 1 годину", callback_data="bc_quick:1h")],
        [InlineKeyboardButton(text="🌅 Завтра о 10:00", callback_data="bc_quick:tomorrow_10")],
        [InlineKeyboardButton(text="🌙 Завтра о 18:00", callback_data="bc_quick:tomorrow_18")],
        [InlineKeyboardButton(text="⬅️ Назад до перегляду", callback_data="bc_back_to_preview")]
    ])

    await callback.message.edit_text(
        "⏰ <b>Планування дати та часу розсилки</b>\n\n"
        "Введіть дату та час у форматі <code>РРРР-ММ-ДД ГГ:ХХ</code>\n"
        "<i>(наприклад, <code>2026-07-25 14:30</code>)</i> або оберіть швидкий варіант нижче:",
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bc_quick:"))
async def bc_quick_time_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    preset = callback.data.split(":")[1]
    now = datetime.now()

    if preset == "1h":
        target_dt = now + timedelta(hours=1)
    elif preset == "tomorrow_10":
        tomorrow = now + timedelta(days=1)
        target_dt = tomorrow.replace(hour=10, minute=0, second=0)
    elif preset == "tomorrow_18":
        tomorrow = now + timedelta(days=1)
        target_dt = tomorrow.replace(hour=18, minute=0, second=0)
    else:
        target_dt = now + timedelta(hours=1)

    scheduled_str = target_dt.strftime("%Y-%m-%d %H:%M")
    await finalize_scheduling(callback.message, state, scheduled_str, callback.from_user.id)
    await callback.answer()


@router.message(BroadcastState.waiting_for_time)
async def process_schedule_time(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    raw_time = (message.text or "").strip()

    try:
        dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer(
            "❌ <b>Невірний формат дати/часу!</b>\n\n"
            "Використовуйте формат <code>РРРР-ММ-ДД ГГ:ХХ</code>\n"
            "Приклад: <code>2026-07-25 14:30</code>",
            parse_mode="HTML"
        )
        return

    if dt <= datetime.now():
        await message.answer("❌ <b>Дата повинна бути в майбутньому!</b> Введіть коректний час.", parse_mode="HTML")
        return

    scheduled_str = dt.strftime("%Y-%m-%d %H:%M")
    await finalize_scheduling(message, state, scheduled_str, message.from_user.id)


async def finalize_scheduling(msg_or_cb_msg, state: FSMContext, scheduled_str: str, admin_id: int):
    data = await state.get_data()
    await state.clear()

    bc_id = f"bc_{int(datetime.now().timestamp())}"
    bc_data = {
        "id": bc_id,
        "creator_id": admin_id,
        "text": data.get("text", ""),
        "photo_id": data.get("photo_id"),
        "btn_text": data.get("btn_text"),
        "btn_url": data.get("btn_url"),
        "status": "scheduled",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scheduled_at": scheduled_str
    }
    save_broadcast(bc_data)

    users_count = len(get_all_bot_user_ids(exclude_admins=True))

    text = (
        f"⏰ <b>Розсилку успішно заплановано!</b>\n\n"
        f"📅 Заплановано на: <b>{scheduled_str}</b>\n"
        f"👥 Кількість отримувачів: <b>{users_count}</b>\n\n"
        f"Ви можете переглянути або скасувати її у розділі «Заплановані розсилки»."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад до меню розсилок", callback_data="admin_broadcast_menu")]
    ])

    if isinstance(msg_or_cb_msg, Message):
        await msg_or_cb_msg.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await msg_or_cb_msg.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "bc_list_scheduled")
async def bc_list_scheduled(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    scheduled = get_scheduled_broadcasts()

    if not scheduled:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_broadcast_menu")]
        ])
        await callback.message.edit_text("⏰ <b>Немає запланованих розсилок.</b>", parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return

    await callback.message.delete()

    for bc in scheduled:
        bc_id = bc['id']
        sch_time = bc.get('scheduled_at', 'Не вказано')
        txt_preview = bc.get('text', '')[:100] + ("..." if len(bc.get('text', '')) > 100 else "")

        msg_text = (
            f"⏰ <b>Запланована розсилка</b>\n"
            f"📅 Час: <b>{sch_time}</b>\n"
            f"💬 Текст: <i>«{txt_preview}»</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Скасувати та видалити", callback_data=f"bc_delete:{bc_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_broadcast_menu")]
        ])
        await callback.message.bot.send_message(callback.from_user.id, msg_text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("bc_delete:"))
async def bc_delete_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    bc_id = callback.data.split(":")[1]
    delete_broadcast(bc_id)

    await callback.message.edit_text("✅ Заплановану розсилку скасовано та видалено.", parse_mode="HTML")
    await callback.answer("Видалено")


@router.callback_query(F.data == "bc_history")
async def bc_history(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    bcs = load_broadcasts()
    completed = [bc for bc in bcs.values() if bc.get("status") == "completed"]

    if not completed:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_broadcast_menu")]
        ])
        await callback.message.edit_text("📜 <b>Історія розсилок порожня.</b>", parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return

    completed.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    text = "📜 <b>Останні проведені розсилки:</b>\n\n"
    for bc in completed[:5]:
        stats = bc.get("stats", {})
        txt_snip = bc.get("text", "")[:60] + "..."
        sent_at = stats.get("completed_at", bc.get("created_at", ""))

        text += (
            f"🗓 <b>{sent_at}</b>\n"
            f"💬 <i>«{txt_snip}»</i>\n"
            f"👥 Всього: {stats.get('total', 0)} | ✅ Доставлено: {stats.get('sent', 0)} | ❌ Помилок: {stats.get('failed', 0)}\n"
            f"───────────────\n"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_broadcast_menu")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "bc_cancel")
async def bc_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await admin_broadcast_menu(callback, state)