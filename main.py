import asyncio
import os
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, types

# импортируем наши настройки
from config import TOKEN

# импортируем роутеры
from tg_bot.handlers import patient, admin

# импортируем фоновые задачи
from workers.tasks import (
    daily_scheduler,
    monitor_and_sync_entries,
    reminder_scheduler,
    execution_monitor
)

# инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# регистрация хендлеров
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

async def main():
    # настройка порта для Render
    port = int(os.environ.get("PORT", 8000)) 
    
    app = web.Application()
    app.router.add_get("/", handle)
    
    # раздача статики
    app.router.add_static('/photos/', path=Path(__file__).parent / 'photos', name='photos')

    runner = web.AppRunner(app)
    await runner.setup()
    
    # слушаем порт, который выдал Render
    await web.TCPSite(runner, '0.0.0.0', port).start()

    # фоновые задачи. Передаем объект bot, чтобы функции могли отправлять уведомления
    asyncio.create_task(daily_scheduler(bot))
    asyncio.create_task(monitor_and_sync_entries(bot))
    asyncio.create_task(reminder_scheduler(bot))
    asyncio.create_task(execution_monitor(bot))
    
    await bot.set_my_commands([types.BotCommand(command="start", description="🏠 Головне меню")])

    print("🤖 Бот успешно запущен...")
    
    # запуск бота
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())