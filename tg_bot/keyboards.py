from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)
from services.db import get_admin_settings

# --- СТАТИЧНЫЕ КЛАВИАТУРЫ ---

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Записатися на прийом")],
        [KeyboardButton(text="⭐️ Залишити відгук")]
    ],
    resize_keyboard=True,
    persistent=True
)

# --- ДИНАМИЧЕСКИЕ КЛАВИАТУРЫ ---

async def get_settings_kb(admin_id):
    """Генерирует клавиатуру настроек в зависимости от текущих стейтов админа в БД"""
    settings = await get_admin_settings(admin_id)
    
    # Вытягиваем значения (по умолчанию True)
    sync_on = settings.get("sync_notifications", True)
    exec_on = settings.get("execution_notifications", True)
    danilo_on = settings.get("track_danilo", True)
    
    # Меняем эмодзи в зависимости от статуса
    sync_emoji = "🔔" if sync_on else "🔕"
    exec_emoji = "⏳" if exec_on else "❌"
    danilo_emoji = "🎯" if danilo_on else "⚪"
    
    buttons = [
        [InlineKeyboardButton(text=f"{sync_emoji} Повідомлення синхронізації", callback_data="toggle_sync")],
        [InlineKeyboardButton(text=f"{exec_emoji} Нагадування про етапи", callback_data="toggle_exec")],
        [InlineKeyboardButton(text=f"{danilo_emoji} Відслідковувати Данило", callback_data="toggle_danilo")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)