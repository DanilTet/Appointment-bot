import re
import math
from typing import Union
from datetime import datetime, timedelta
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, 
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton
)

# Импортируем настройки, БД, таблицы и состояния
from config import ADMIN_IDS, SERVICE_MAP, USER_SERVICES, SKIP_ROWS, REVERSE_SERVICE_MAP
from services.db import supabase, log_user_visit
from services.sheets import (
    get_available_dates, schedule_sheet, get_monday_str, 
    get_col_idx, is_time_allowed, clear_sheet_slot, get_next_row_idx
)
from tg_bot.states import Appointment, Review

# Создаем роутер (заменитель dp для этого файла)
router = Router()

# --- ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ ---

@router.message(Command("start"))
@router.message(F.text == "📝 Записатися на прийом")
async def cmd_start(message: Message):
    log_user_visit(message.from_user.id)

    photo = FSInputFile("photos/start_message/start_img.png")    
    buttons = [
        [InlineKeyboardButton(text="🗓 Записатись на прийом", callback_data="make_appointment")],
        [InlineKeyboardButton(text="📋 Мої записи", callback_data="my_appointments")],
        [InlineKeyboardButton(text="⭐ Відгуки", callback_data="reviews_menu")],
        [InlineKeyboardButton(text="ℹ️ Інформація та Питання", callback_data="info_menu")],
        [InlineKeyboardButton(text="📸 Наш Instagram", url="https://www.instagram.com/dr.teternik")]
    ]

    if message.from_user.id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="🛠 Адмін-панель", callback_data="admin_panel_menu")])

    welcome_text = (
        "Вітаю! Я - <b>Тетернік Олег</b>, ваш лікар-ендоскопіст.\n"
        "Цей бот допоможе вам швидко обрати зручний час та записатися на прийом (гастроскопія, колоноскопія, бронхоскопія чи консультація).\n\n"
        "📞 <b>Телефон:</b> +380 99 475 09 67\n"
        "📍 <b>Адреса:</b> м. Харків, просп. Героїв Харкова, 195\n\n"
        "Оберіть дію нижче."
    )

    inline_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer_photo(photo=photo, caption=welcome_text, parse_mode="HTML", reply_markup=inline_kb)

@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except: pass

    photo = FSInputFile("photos/start_message/start_img.png")    
    buttons = [
        [InlineKeyboardButton(text="🗓 Записатись на прийом", callback_data="make_appointment")],
        [InlineKeyboardButton(text="📋 Мої записи", callback_data="my_appointments")],
        [InlineKeyboardButton(text="⭐ Відгуки", callback_data="reviews_menu")],
        [InlineKeyboardButton(text="ℹ️ Інформація та Питання", callback_data="info_menu")],
        [InlineKeyboardButton(text="📸 Наш Instagram", url="https://www.instagram.com/dr.teternik")]
    ]

    if callback.from_user.id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="🛠 Адмін-панель", callback_data="admin_panel_menu")])

    welcome_text = (
        "Вітаю! Я - <b>Тетернік Олег</b>, ваш лікар-ендоскопіст.\n"
        "Цей бот допоможе вам швидко обрати зручний час та записатися на прийом (гастроскопія, колоноскопія, бронхоскопія чи консультація).\n\n"
        "📞 <b>Телефон:</b> +380 99 475 09 67\n"
        "📍 <b>Адреса:</b> м. Харків, просп. Героїв Харкова, 195\n\n"
        "Оберіть дію нижче."
    )

    inline_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer_photo(photo=photo, caption=welcome_text, parse_mode="HTML", reply_markup=inline_kb)
    await callback.answer()

@router.callback_query(F.data == "back_to_main_inline")
async def back_to_main_inline(callback: CallbackQuery):
    buttons = [
        [InlineKeyboardButton(text="🗓 Записатись на прийом", callback_data="make_appointment")],
        [InlineKeyboardButton(text="📋 Мої записи", callback_data="my_appointments")],
        [InlineKeyboardButton(text="⭐ Відгуки", callback_data="reviews_menu")],
        [InlineKeyboardButton(text="ℹ️ Інформація та Питання", callback_data="info_menu")],
        [InlineKeyboardButton(text="📸 Наш Instagram", url="https://www.instagram.com/dr.teternik")]
    ]

    if callback.from_user.id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="🛠 Адмін-панель", callback_data="admin_panel_menu")])

    inline_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_reply_markup(reply_markup=inline_kb)
    await callback.answer()

@router.callback_query(F.data == "info_menu")
async def show_info_menu(callback: CallbackQuery):
    buttons = [
        [InlineKeyboardButton(text="❓ Часті питання", callback_data="faq_menu")],
        [InlineKeyboardButton(text="🏥 Про лікарню", url="https://maps.app.goo.gl/XpYjaFtw7vAvdJST8?g_st=ic")],
        [InlineKeyboardButton(text="📍 Як нас знайти", url="https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTI5MTUwNDQ4ODkxNTMz?story_media_id=3382227683352410926&igsh=MWZ0cHdybTY4cmtoNQ==")],
        [InlineKeyboardButton(text="🔙 Назад у головне меню", callback_data="back_to_main_inline")]
    ]
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@router.callback_query(F.data == "faq_menu")
async def show_faq_menu(callback: CallbackQuery):
    utm = "?utm_source=telegram_bot&utm_medium=button&utm_campaign=faq_menu"
    buttons = [
        [InlineKeyboardButton(text="❓ Гастроскопія", url=f"https://endo.kh.ua/gastroscopy/{utm}")],
        [InlineKeyboardButton(text="❓ Колоноскопія", url=f"https://endo.kh.ua/colonoscopy/{utm}")],
        [InlineKeyboardButton(text="❓ УЗД", url=f"https://endo.kh.ua/uzd/{utm}")],
        [InlineKeyboardButton(text="❓ Хірургія", url=f"https://endo.kh.ua/surgery/{utm}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="info_menu")]
    ]
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

# ==========================================
# --- ОБРОБКА ВІДГУКІВ (МЕНЮ + ЧИТАННЯ + ЗАПИС) ---
# ==========================================

@router.message(F.text == "⭐️ Відгуки")
@router.callback_query(F.data == "reviews_menu")
async def reviews_menu(event: Union[Message, CallbackQuery], state: FSMContext):
    is_callback = isinstance(event, CallbackQuery)
    msg = event.message if is_callback else event

    if is_callback:
        try: await msg.delete()
        except: pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Залишити відгук", callback_data="leave_review")],
        [InlineKeyboardButton(text="📖 Читати відгуки", callback_data="view_reviews:0")], # Додали :0 для першої сторінки
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])
    
    await msg.answer("⭐️ <b>Відгуки пацієнтів</b>\nОберіть дію нижче:", reply_markup=kb, parse_mode="HTML")
    if is_callback:
        await event.answer()

# --- НОВА ФУНКЦІЯ ПАГІНАЦІЇ ---
@router.callback_query(F.data.startswith("view_reviews"))
async def view_reviews_page(callback: CallbackQuery):
    # Дістаємо номер сторінки з callback_data
    data_parts = callback.data.split(":")
    page = int(data_parts[1]) if len(data_parts) > 1 else 0
    
    per_page = 4 # Кількість відгуків на одну сторінку
    offset = page * per_page
    
    # 1. Рахуємо загальну кількість відгуків
    count_res = supabase.table("reviews").select("id", count="exact").eq("status", "approved").execute()
    total_reviews = count_res.count if count_res.count else 0
    total_pages = math.ceil(total_reviews / per_page)
    
    if total_reviews == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="reviews_menu")]])
        await callback.message.edit_text("Поки що немає відгуків. Будьте першим, хто його залишить! 🌟", reply_markup=kb)
        return

    # 2. Дістаємо порцію відгуків
    res = supabase.table("reviews").select("*").eq("status", "approved").order("id", desc=True).range(offset, offset + per_page - 1).execute()
    
    text = f"⭐️ <b>Відгуки пацієнтів (Сторінка {page + 1} з {total_pages}):</b>\n\n"
    for r in res.data:
        stars_str = "⭐️" * r['stars']
        text += f"👤 <b>{r['user_name']}</b>\nОцінка: {stars_str}\n💬 <i>«{r['text']}»</i>\n───────────────\n"
        
    # 3. Будуємо кнопки навігації
    navigation_buttons = []
    
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_reviews:{page - 1}"))
        
    if offset + per_page < total_reviews:
        navigation_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"view_reviews:{page + 1}"))
        
    kb_list = []
    if navigation_buttons:
        kb_list.append(navigation_buttons)
    kb_list.append([InlineKeyboardButton(text="⬅️ До меню відгуків", callback_data="reviews_menu")])
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    await callback.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback.answer()
# ------------------------------

@router.callback_query(F.data == "leave_review")
async def ask_for_review_stars(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 ⭐️", callback_data="rate_1"),
            InlineKeyboardButton(text="2 ⭐️", callback_data="rate_2"),
            InlineKeyboardButton(text="3 ⭐️", callback_data="rate_3"),
            InlineKeyboardButton(text="4 ⭐️", callback_data="rate_4"),
            InlineKeyboardButton(text="5 ⭐️", callback_data="rate_5")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="reviews_menu")]
    ])
    
    await callback.message.edit_text("Оцініть, будь ласка, ваш візит від 1 до 5 зірок: 👇", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("rate_"))
async def process_star_rating(callback: CallbackQuery, state: FSMContext):
    stars_num = int(callback.data.split("_")[1])
    await state.update_data(stars=stars_num)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Скасувати", callback_data="reviews_menu")]
    ])
    
    stars_str = "⭐️" * stars_num
    await callback.message.edit_text(
        f"Ви обрали оцінку: {stars_str}\n\n"
        "Тепер напишіть, будь ласка, короткий відгук або ваші враження від візиту. "
        "Ваша думка допомагає нам ставати кращими! 👇",
        reply_markup=kb
    )
    await state.set_state(Review.text)
    await callback.answer()

@router.message(Review.text)
async def process_review_text(message: Message, state: FSMContext):
    review_text = message.text
    user_name = message.from_user.first_name
    user_id = message.from_user.id
    username = f" (@{message.from_user.username})" if message.from_user.username else ""
    
    user_data = await state.get_data()
    stars_num = user_data.get("stars", 5) 
    stars_str = "⭐️" * stars_num
    
    # 1. Зберігаємо в базу даних зі статусом 'pending'
    res = supabase.table("reviews").insert({
        "user_id": user_id,
        "user_name": user_name,
        "stars": stars_num,
        "text": review_text,
        "status": "pending"
    }).execute()
    
    review_id = res.data[0]['id']

    # 2. Відправляємо адміну на модерацію (З ДОДАНОЮ ПІДКАЗКОЮ)
    admin_text = (
        f"📝 <b>Новий відгук на модерацію!</b>\n"
        f"👤 {user_name}{username}\n"
        f"Оцінка: {stars_str}\n\n"
        f"💬 <i>«{review_text}»</i>\n\n"
        f"💡 <i>Якщо ви натиснете «✅ Опублікувати», пацієнту автоматично прийде прохання залишити цей відгук на Google Картах.</i>"
    )
    
    # Видалили окрему кнопку Google, але додали user_id у rev_ok
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опублікувати", callback_data=f"rev_ok:{review_id}:{user_id}"),
            InlineKeyboardButton(text="❌ Відхилити", callback_data=f"rev_no:{review_id}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try: await message.bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=admin_kb)
        except: pass
            
    # 3. Хвалимо пацієнта
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ До головного меню", callback_data="back_to_main")]
    ])
    await message.answer("Дякуємо за ваш відгук! Ваша думка дуже важлива для нас. ❤️", reply_markup=kb)
    await state.clear()

# --- ХЕНДЛЕРЫ ОПРОСА (ЗАПИСЬ) ---

@router.callback_query(F.data == "make_appointment")
async def start_survey(callback: CallbackQuery, state: FSMContext):
    try: await callback.message.delete()
    except: pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад до меню", callback_data="back_to_main")]
    ])
    msg = await callback.message.answer("Введіть ваше Прізвище та Ім'я:", reply_markup=kb)
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(Appointment.name)
    await callback.answer()

@router.message(Appointment.name)
async def process_name(message: Message, state: FSMContext):
    data = await state.get_data()
    last_msg_id = data.get("last_msg_id")

    if last_msg_id:
        try: await message.bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
        except: pass
    try: await message.delete()
    except: pass

    await state.update_data(name=message.text, doctor="Тетерник")

    buttons = [[InlineKeyboardButton(text=s, callback_data=f"service_{s}") for s in row] for row in USER_SERVICES]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад до меню", callback_data="back_to_main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(f"Ім'я <b>{message.text}</b>, будь ласка, оберіть вид послуги:", reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(Appointment.service)

@router.callback_query(Appointment.service, F.data.startswith("service_"))
async def process_service_selection(callback: CallbackQuery, state: FSMContext):
    full_service_name = callback.data.replace("service_", "").strip()
    short_service_name = SERVICE_MAP.get(full_service_name, full_service_name)

    await state.update_data(service=short_service_name)

    if short_service_name == "Ректо":
        await state.update_data(anesthesia="Наркоз ")
        await callback.message.edit_text(f"Обрано послугу: <b>{full_service_name}</b> (завжди з седацією)", parse_mode="HTML")
        await show_dates(callback, state)
    
    elif short_service_name in ["Консультация", "УЗД"]:
        await state.update_data(anesthesia="Без наркоза")
        await callback.message.edit_text(f"Обрано послугу: <b>{full_service_name}</b>", parse_mode="HTML")
        await show_dates(callback, state)

    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Так, з наркозом", callback_data="anes_yes")],
            [InlineKeyboardButton(text="❌ Ні, без наркозу", callback_data="anes_no")]
        ])
        await callback.message.edit_text(f"Для послуги <b>{full_service_name}</b> можливий наркоз.\nБажаєте провести процудуру під седацією", reply_markup=kb, parse_mode="HTML")
        await state.set_state(Appointment.anesthesia)

@router.callback_query(F.data == "back_to_services")
async def process_back_to_services(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    name = user_data.get("name", "Пацієнт")

    buttons = [[InlineKeyboardButton(text=s, callback_data=f"service_{s}") for s in row] for row in USER_SERVICES]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад до меню", callback_data="back_to_main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(f"Ім'я <b>{name}</b>, будь ласка, оберіть вид послуги:", reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(Appointment.service)

@router.callback_query(Appointment.anesthesia, F.data.startswith("anes_"))
async def process_anesthesia_selection(callback: CallbackQuery, state: FSMContext):
    ans_type = "Наркоз " if callback.data == "anes_yes" else "Без наркоза"
    await state.update_data(anesthesia=ans_type)
    await show_dates(callback, state)
    await callback.answer()

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ (ДАТЫ) ---
async def show_dates(event, state: FSMContext):
    is_callback = isinstance(event, CallbackQuery)
    msg = event.message if is_callback else event

    dates = await get_available_dates()

    if not dates:
        text_no_dates = "На жаль, вільних дат немає."
        kb_no_dates = InlineKeyboardMarkup(inline_keyboard=[
             [InlineKeyboardButton(text="⬅️ Назад до послуг", callback_data="back_to_services")]
        ])
        if is_callback: await msg.edit_text(text_no_dates, reply_markup=kb_no_dates)
        else: await msg.answer(text_no_dates, reply_markup=kb_no_dates)
        return

    buttons = [[InlineKeyboardButton(text=d, callback_data=f"date_{d}")] for d in dates]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад до послуг", callback_data="back_to_services")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    user_data = await state.get_data()
    service_short = user_data.get("service", "")
    
    service_display = service_short
    for k, v in SERVICE_MAP.items():
        if v == service_short:
            service_display = k
            break
            
    text = f"Обрано послугу: <b>{service_display}</b>\nОберіть дату:"

    if is_callback: await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else: await msg.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(Appointment.date)

@router.callback_query(F.data.startswith("date_"))
async def process_date_selection(callback: CallbackQuery, state: FSMContext):
    date_val = callback.data.split("_")[1]
    await state.update_data(date=date_val)

    user_data = await state.get_data()
    service = user_data.get("service", "")
    anesthesia = user_data.get("anesthesia", "")

    dt = datetime.strptime(date_val, "%d.%m.%Y")
    ws = schedule_sheet.worksheet(get_monday_str(dt))
    col, data = get_col_idx(dt) - 1, ws.get_all_values()

    buttons = []
    curr_row = 4

    while curr_row < len(data):
        if (curr_row + 1) in SKIP_ROWS or curr_row + 1 >= len(data):
            curr_row += 1
            continue

        time_val = data[curr_row][col-1].strip()
        patient_name = data[curr_row + 1][col].strip()

        if is_time_allowed(service, anesthesia, time_val):
            if not patient_name:
                needs_double = (service in ["Гастро", "Бронхо", "Ректо"]) and (anesthesia == "Наркоз ")

                if needs_double:
                    next_row_idx = curr_row + 5
                    if next_row_idx + 1 < len(data) and (next_row_idx + 1) not in SKIP_ROWS:
                        next_patient = data[next_row_idx + 1][col].strip()
                        next_time = data[next_row_idx][col-1].strip()

                        if not next_patient and int(next_time.split(":")[0]) < 11:
                            buttons.append([InlineKeyboardButton(text=f"🕒 {time_val} (30 хв)", callback_data=f"time_{curr_row + 1}_{time_val}")])
                else:
                    buttons.append([InlineKeyboardButton(text=f"🕒 {time_val}", callback_data=f"time_{curr_row + 1}_{time_val}")])
        
        curr_row += 5

    if not buttons:
        buttons.append([InlineKeyboardButton(text="⬅️ Назад до дат", callback_data="back_to_dates")])
        await callback.message.edit_text(f"На {date_val} немає вільних слотів для: {service}.", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_dates")])
        await callback.message.edit_text(f"Дата: {date_val}\nПослуга: {service}\nОберіть час:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    
    await state.set_state(Appointment.time)

@router.callback_query(F.data == "back_to_dates")
async def process_back_to_dates(callback: CallbackQuery, state: FSMContext):
    await show_dates(callback, state)
    await callback.answer()

@router.callback_query(F.data.startswith("time_"))
async def process_time_selection(callback: CallbackQuery, state: FSMContext):
    _, row, time_val = callback.data.split("_")
    await state.update_data(selected_row=row, selected_time=time_val)

    phone_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поділитися контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await callback.message.answer(
        "Натисніть кнопку <b>'Поділитися контактом'</b> або введіть номер телефону вручну (наприклад, 0991234567):",
        reply_markup=phone_kb,
        parse_mode="HTML"
    )
    await state.set_state(Appointment.phone)

@router.message(Appointment.phone)
async def process_phone(message: Message, state: FSMContext):
    phone = None

    if message.contact:
        phone = message.contact.phone_number
    else:
        clean_phone = re.sub(r"[\s\-\(\)\+]", "", message.text)
        if re.fullmatch(r"(38)?0\d{9}", clean_phone):
            if clean_phone.startswith("0"): phone = f"+38{clean_phone}"
            else: phone = f"+{clean_phone}"
        else:
            await message.answer(
                "❌ <b>Невірний формат номера!</b>\n\nБудь ласка, введіть 10 цифр (наприклад, 0991234567) або натисніть кнопку нижче.",
                parse_mode="HTML"
            )
            return

    user_data = await state.get_data()
    await message.answer("✅ Номер прийнято.", reply_markup=types.ReplyKeyboardRemove())

    res = supabase.table("appointments").insert({
        "user_id": message.from_user.id,
        "name": user_data['name'],
        "service": user_data['service'],
        "anesthesia": user_data['anesthesia'],
        "doctor": user_data['doctor'],
        "phone": phone,
        "date": user_data['date'],
        "time": user_data['selected_time'],
        "row_idx": user_data['selected_row'],
        "status": "pending"
    }).execute()

    appt_id = res.data[0]['id']
    await message.answer("⏳ Ваша заявка відправлена на підтвердження...")

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Підтвердити", callback_data=f"ok:{appt_id}")],
        [InlineKeyboardButton(text="❌ Відхилити (з СМС)", callback_data=f"no:{appt_id}")],
        [InlineKeyboardButton(text="🗑 Видалити (без СМС)", callback_data=f"del_silent:{appt_id}")]
    ])

    admin_text = (
        f"🕒 <b>{user_data['selected_time']}</b>\n"
        f"📅 <b>{user_data['date']}</b>\n"
        f"👤 Пацієнт: <b>{user_data['name']}</b>\n"
        f"📞 Телефон: {phone}\n"
        f"🩺 Послуга: {user_data['service']}\n"
        f"💉 Наркоз: {user_data['anesthesia']}\n"
        f"👨‍⚕️ Лікар: {user_data['doctor']}\n"
        f"🆔 Заявка №: {appt_id}"
    )

    for admin_id in ADMIN_IDS:
        try: await message.bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=admin_kb)
        except: pass
    await state.clear()

# --- МОИ ЗАПИСИ И НАПОМИНАНИЯ ---

@router.callback_query(F.data == "my_appointments")
async def show_my_appointments(callback: CallbackQuery):
    try: await callback.message.delete()
    except: pass

    user_id = callback.from_user.id
    now_dt = datetime.now() + timedelta(hours=2)

    response = supabase.table("appointments").select("*").eq("user_id", user_id).eq("status", "confirmed").execute()
    rows = response.data
    active_found = False

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ До головного меню", callback_data="back_to_main")]
    ])

    for row in rows:
        try:
            appt_dt = datetime.strptime(row['date'], "%d.%m.%Y")
            if appt_dt.date() < now_dt.date(): continue
            active_found = True
        
            text = (
                f"📅 <b>Дата:</b> {row['date']}\n🕒 <b>Час:</b> {row['time']}\n"
                f"👤 <b>Пацієнт:</b> {row['name']}\n👨‍⚕️ <b>Лікар:</b> {row['doctor']}\n"
                f"🩺 <b>Послуга:</b> {row['service']}\n💉 <b>Наркоз:</b> {row['anesthesia']}\n──────────────────"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data=f"rem_cancel:{row['id']}")]])
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"Помилка обробки запису користувача {user_id}: {e}")
    
    if not active_found: await callback.message.answer("У вас немає активних записів на прийом.", reply_markup=back_kb)
    else: await callback.message.answer("Це всі ваші активні записи.", reply_markup=back_kb)
    await callback.answer()

@router.callback_query(F.data.startswith("rem_yes:"))
async def process_confirm_presence(callback: CallbackQuery):
    await callback.message.edit_text("✅ <b>Чудово! До зустрічі.</b>", parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("rem_no:"))
async def process_decline_presence(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Відмінити прийом", callback_data=f"rem_cancel:{appt_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"rem_back:{appt_id}")]
    ])
    await callback.message.edit_text("Що ви бажаєте зробити?", reply_markup=kb)

@router.callback_query(F.data.startswith("rem_cancel:"))
async def process_cancel_appointment(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]
    res = supabase.table("appointments").select("*").eq("id", appt_id).execute()
    data = res.data[0] if res.data else None

    if data:
        supabase.table("appointments").update({"status": "cancelled", "execution_stage": "Cancelled_By_User"}).eq("id", appt_id).execute()
        
        try:
            dt = datetime.strptime(data['date'], "%d.%m.%Y")
            ws = schedule_sheet.worksheet(get_monday_str(dt))
            col = get_col_idx(dt)
            r_idx = int(data['row_idx'])

            clear_sheet_slot(ws, r_idx, col)
            is_double = (data['service'] in ["Гастро", "Бронхо", "Ректо"]) and (data['anesthesia'] == "Наркоз ")
            hour = int(data['time'].split(":")[0])

            if is_double and hour < 11:
                next_row = get_next_row_idx(r_idx)
                clear_sheet_slot(ws, next_row, col)

            admin_msg = (
                f"‼️ <b>ВІДМІНА ПРИЙОМУ!</b>\n\n🩺 Послуга: <b>{data['service']}</b>\n💉 Наркоз: {data['anesthesia']}\n"
                f"👤 Пацієнт: {data['name']}\n📞 Телефон: {data['phone']}\n📅 Дата: {data['date']} {data['time']}"
            )
            for admin_id in ADMIN_IDS:
                try: await callback.message.bot.send_message(admin_id, admin_msg, parse_mode="HTML")
                except: pass

        except Exception as e: print(f"Помилка очищення таблиці: {e}")

    await callback.message.edit_text("❌ Прийом скасовано. Ми видалили ваш запис із розкладу.")
    await callback.answer()

@router.callback_query(F.data.startswith("rem_back:"))
async def process_rem_back(callback: CallbackQuery):
    appt_id = callback.data.split(":")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Так, буду", callback_data=f"rem_yes:{appt_id}"),
         InlineKeyboardButton(text="❌ Ні, не зможу", callback_data=f"rem_no:{appt_id}")]
    ])
    await callback.message.edit_text("Ви будете на прийомі?", reply_markup=kb)