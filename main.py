import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from aiogram import Dispatcher

from loader import bot
from handlers import main_router
from utils.scheduler import (
    update_statuses_task,
    clean_chromium_cache_job,
    billing_alert_task,
    billing_digest_task,
)
from middlewares.auth import AuthMiddleware

# Держим ссылки на фоновые задачи, чтобы их не собрал GC
_background_tasks: set[asyncio.Task] = set()

# Дефолтный executor asyncio.to_thread() рассчитан как min(32, cpu_count()+4) —
# на VPS с 1-2 vCPU это всего 5-6 потоков. Все SSH-вызовы (execute_command)
# идут через to_thread, а их одновременно может быть 30+ (фоновый скан всех
# серверов) + интерактивные действия оператора. При нехватке потоков команды
# оператора встают в очередь ЗА фоновым сканом и кнопка "зависает" на
# десятки секунд. Расширяем пул явно, отдельно от количества CPU.
SSH_THREAD_POOL_SIZE = 40


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

    # Явно ставим просторный executor ДО первого asyncio.to_thread(), иначе
    # event loop успеет создать дефолтный (узкий) пул сам.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=SSH_THREAD_POOL_SIZE, thread_name_prefix="ssh")
    )
    logging.info("🧵 SSH thread pool расширен до %d потоков", SSH_THREAD_POOL_SIZE)

    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(main_router)

    spawn(update_statuses_task, "update_statuses")
    spawn(clean_chromium_cache_job, "clean_chromium_cache")
    spawn(billing_alert_task, "billing_alert")
    spawn(billing_digest_task, "billing_digest")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")