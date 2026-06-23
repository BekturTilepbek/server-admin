import asyncio
import logging

from aiogram import Dispatcher

from loader import bot
from handlers import main_router
from utils.scheduler import update_statuses_task, clean_chromium_cache_job
from middlewares.auth import AuthMiddleware

# Держим ссылки на фоновые задачи, чтобы их не собрал GC
_background_tasks: set[asyncio.Task] = set()


def setup_logging():
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        filename="bot_log.log",
        filemode="a",
        encoding="utf-8",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format))
    logging.getLogger("").addHandler(console)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("paramiko").setLevel(logging.WARNING)


async def supervise(coro_factory, name: str):
    """Перезапускает фоновую задачу, если она упала с исключением."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Задача '%s' упала. Перезапуск через 10с.", name)
            await asyncio.sleep(10)


def spawn(coro_factory, name: str):
    task = asyncio.create_task(supervise(coro_factory, name))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def main():
    setup_logging()
    logging.info("🚀 Запуск бота на aiogram 3.x...")

    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(main_router)

    spawn(update_statuses_task, "update_statuses")
    spawn(clean_chromium_cache_job, "clean_chromium_cache")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
