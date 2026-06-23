import asyncio
import logging
from datetime import datetime, timedelta, timezone
from services.checker import check_server
from services.store import load_servers_sync, save_cache
from services.ssh import execute_command

TZ_GMT6 = timezone(timedelta(hours=6))


async def update_statuses_task():
    """Бесконечный цикл обновления статусов"""
    logging.info("⏳ Запущен планировщик проверки серверов...")
    while True:
        try:
            servers = load_servers_sync()
            cache_data = {}

            # Проверяем все сервера параллельно
            tasks = []
            keys = []

            for key, data in servers.items():
                keys.append(key)
                # Вызываем ту функцию, которая возвращает красивый текст
                tasks.append(check_server(key, data))

            # Ждем выполнения всех проверок
            results = await asyncio.gather(*tasks)

            # Собираем словарь { "server_1": "HTML текст статуса", ... }
            for i, result in enumerate(results):
                cache_data[keys[i]] = result

            # Сохраняем в файл
            await save_cache(cache_data)
            logging.info("✅ Кэш статусов обновлен")

        except Exception as e:
            logging.error(f"Ошибка в планировщике: {e}")

        # Спим 300 секунд (5 минут)
        await asyncio.sleep(300)


async def clean_chromium_cache_job():
    """Фоновая задача для очистки кэша Chromium каждый день в 04:00 утра"""
    while True:
        # 1. Высчитываем время до ближайших 04:00
        now = datetime.now(TZ_GMT6)
        target_time = now.replace(hour=4, minute=0, second=0, microsecond=0)

        # Если сейчас уже больше 04:00, планируем на завтрашнее утро
        if now >= target_time:
            target_time += timedelta(days=1)

        # Считаем разницу в секундах
        sleep_seconds = (target_time - now).total_seconds()

        logging.info(
            f"⏳ Очистка кэша запланирована на: {target_time.strftime('%Y-%m-%d %H:%M:%S')} (через {sleep_seconds / 3600:.2f} часов)")

        # 2. Бот "засыпает" в этой конкретной задаче до нужного времени
        await asyncio.sleep(sleep_seconds)

        # 3. Время пришло — выполняем очистку
        logging.info("🧹 Начинаю ночную очистку кэша Chromium на всех серверах...")

        servers = load_servers_sync()

        for key, server in servers.items():
            bots = server.get('bots', ['bot1', 'bot2', 'bot3'])
            path = server.get('path', '/root/servers_admin_bot')

            # Собираем команды в одну цепочку
            commands = [f"cd {path}"]

            for bot_name in bots:
                cmd_stop = f"docker stop {bot_name}"
                cmd_clean = f"find ./sessions/{bot_name} -type d \\( -name 'Cache' -o -name 'Code Cache' -o -name 'GPUCache' -o -name 'CacheStorage' \\) -exec rm -rf {{}} + 2>/dev/null"
                cmd_start = f"docker start {bot_name}"

                bot_sequence = f"( {cmd_stop} ; {cmd_clean} ; {cmd_start} )"
                commands.append(bot_sequence)

            full_cmd = f"cd {path} && " + " ; ".join(commands[1:])

            logging.info(f"Отправляю команду очистки на сервер {server['name']}...")
            status, out, err = await asyncio.to_thread(execute_command, server, full_cmd)

            if status != 0:
                logging.error(f"❌ Ошибка очистки на {server['name']}: {err}")
            else:
                logging.info(f"✅ Сервер {server['name']} успешно очищен.")

        logging.info("🏁 Ночная очистка кэша завершена!")