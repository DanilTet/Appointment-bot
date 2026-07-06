import asyncio
import os
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import hmac
import hashlib

from config import TOKEN, REVIEW_WEBHOOK_SECRET, ADMIN_IDS
from tg_bot.handlers import patient, admin
from workers.tasks import (
    daily_scheduler,
    monitor_and_sync_entries,
    reminder_scheduler,
    execution_monitor
)

bot = Bot(token=TOKEN)
dp = Dispatcher()

dp.include_router(patient.router)
dp.include_router(admin.router)

async def handle(request):
    index_file = Path(__file__).parent / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type='text/html')
    else:
        return web.Response(text="Файл index.html не знайдено!", status=404)

async def handle_new_review_webhook(request):
    """
    Принимает POST от Supabase Database Webhook при INSERT в таблицу reviews.
    Отправляет врачу уведомление с кнопками Одобрить / Отклонить.
    """
    # 1. Проверка секретного ключа (защита от левых запросов)
    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != REVIEW_WEBHOOK_SECRET:
        return web.Response(status=403, text="Forbidden")
        
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")
        
    # Supabase шлёт {"type": "INSERT", "record": {...}, ...}
    if body.get("type") != "INSERT":
        return web.Response(status=200, text="OK")  # Нас интересует только INSERT
        
    review = body.get("record", {})
    if not review:
        return web.Response(status=400, text="No record")
        
    review_id  = review.get("id")
    user_name  = review.get("user_name") or "Невідомий"
    stars      = int(review.get("stars") or 5)
    text       = review.get("text") or ""
    user_id    = review.get("user_id")  # None — если отзыв с сайта (не из бота)
    
    stars_str  = "⭐️" * stars
    source_tag = "🌐 <b>з сайту</b>" if not user_id else "🤖 <b>з бота</b>"
    
    google_tip = (
        "Якщо ви натиснете «✅ Опублікувати», відгук з'явиться на сайті.\n"
        "<i>(Відгук з сайту — посилання на Google Карти не надсилається автоматично.)</i>"
        if not user_id else
        "Якщо ви натиснете «✅ Опублікувати», пацієнту автоматично надійде прохання залишити відгук на Google Картах."
    )
    
    admin_text = (
        f"📝 <b>Новий відгук на модерацію!</b>\n"
        f"Джерело: {source_tag}\n"
        f"👤 <b>{user_name}</b>\n"
        f"Оцінка: {stars_str}\n\n"
        f"💬 <i>«{text}»</i>\n\n"
        f"💡 {google_tip}"
    )
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Опублікувати",
            callback_data=f"rev_ok:{review_id}:{user_id or 'none'}"
        ),
        InlineKeyboardButton(
            text="❌ Відхилити",
            callback_data=f"rev_no:{review_id}"
        )
    ]])
    
    # Отправляем всем админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=admin_kb)
        except Exception as e:
            print(f"[Webhook] Не вдалося надіслати адміну {admin_id}: {e}")
            
    return web.Response(status=200, text="OK")

async def main():
    port = int(os.environ.get("PORT", 8000)) 
    
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_post("/webhook/new-review", handle_new_review_webhook)  # ← НОВЫЙ МАРШРУТ
    app.router.add_static('/photos/', path=Path(__file__).parent / 'photos', name='photos')

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port).start()

    asyncio.create_task(daily_scheduler(bot))
    asyncio.create_task(monitor_and_sync_entries(bot))
    asyncio.create_task(reminder_scheduler(bot))
    asyncio.create_task(execution_monitor(bot))
    
    await bot.set_my_commands([types.BotCommand(command="start", description="🏠 Головне меню")])

    print("🤖 Бот успешно запущен...")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())