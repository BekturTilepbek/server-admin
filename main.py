import asyncio
import logging
from aiogram import Dispatcher
from loader import bot
from handlers import main_router
from utils.scheduler import update_statuses_task, clean_chromium_cache_job
from middlewares.auth import AuthMiddleware


# Настройка логирования
def setup_logging():
    # Формат логов
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Пишем в файл
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        filename="bot_log.log",  # Имя файла логов
        filemode="a",
        encoding='utf-8'  # Важно для кириллицы
    )

    # И дублируем в консоль, чтобы видеть глазами при запуске
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format))
    logging.getLogger('').addHandler(console)

    # Затыкаем aiogram (чтобы не писал "Update id handled")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    # Затыкаем paramiko (чтобы не писал про SSH соединение)
    logging.getLogger("paramiko").setLevel(logging.WARNING)

async def main():
    setup_logging()
    logging.info("🚀 Запуск бота на aiogram 3.x...")

    dp = Dispatcher()

    # !!! ВАЖНО: Добавь эти две строки ПЕРЕД dp.include_router !!!
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    dp.include_router(main_router)

    # ЗАПУСК ФОНОВОЙ ЗАДАЧИ
    # create_task запускает функцию параллельно и не блокирует бота
    asyncio.create_task(update_statuses_task())
    asyncio.create_task(clean_chromium_cache_job())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")

