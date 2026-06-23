import asyncio
import logging
from datetime import datetime, timedelta, timezone

from services.checker import check_server
from services.store import load_servers_sync, save_cache
from services.ssh import execute_command

logger = logging.getLogger(__name__)

TZ_GMT6 = timezone(timedelta(hours=6))
SCAN_INTERVAL = 300  # 5 минут


async def update_statuses_task():
    """Бесконечный цикл обновления статусов всех серверов."""
    logger.info("⏳ Запущен планировщик проверки серверов...")
    while True:
        try:
            servers = load_servers_sync()
            keys = list(servers.keys())
            tasks = [check_server(k, servers[k]) for k in keys]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            cache_data = {}
            for key, res in zip(keys, results):
                if isinstance(res, Exception):
                    logger.error("Ошибка проверки %s: %s", key, res)
                    cache_data[key] = f"<b>🖥 {servers[key].get('ip', key)}:</b>\n❌ Ошибка опроса"
                else:
                    cache_data[key] = res

            await save_cache(cache_data)
            logger.info("✅ Кэш статусов обновлён (%d серверов)", len(keys))

        except Exception as e:
            logger.exception("Ошибка в планировщике: %s", e)

        await asyncio.sleep(SCAN_INTERVAL)


async def clean_chromium_cache_job():
    """Ночная очистка кэша Chromium каждый день в 04:00 по Бишкеку (GMT+6)."""
    while True:
        now = datetime.now(TZ_GMT6)
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        sleep_seconds = (target - now).total_seconds()
        logger.info(
            "⏳ Очистка кэша запланирована на %s (через %.2f ч)",
            target.strftime("%Y-%m-%d %H:%M:%S"), sleep_seconds / 3600,
        )
        await asyncio.sleep(sleep_seconds)

        logger.info("🧹 Начинаю ночную очистку кэша Chromium...")
        servers = load_servers_sync()

        for key, server in servers.items():
            bots = server.get("bots", ["bot1", "bot2", "bot3"])
            path = server.get("path", "/root/servers_admin_bot")

            sequences = []
            for bot_name in bots:
                stop = f"docker compose stop {bot_name}"
                clean = (
                    f"find ./sessions/{bot_name} -type d "
                    f"\\( -name 'Cache' -o -name 'Code Cache' "
                    f"-o -name 'GPUCache' -o -name 'CacheStorage' \\) "
                    f"-exec rm -rf {{}} + 2>/dev/null"
                )
                start = f"docker compose start {bot_name}"
                sequences.append(f"( {stop} ; {clean} ; {start} )")

            full_cmd = f"cd {path} && " + " ; ".join(sequences)

            logger.info("Отправляю команду очистки на %s...", server.get("name", key))
            status, out, err = await asyncio.to_thread(execute_command, server, full_cmd)
            if status != 0:
                logger.error("❌ Ошибка очистки на %s: %s", server.get("name", key), err)
            else:
                logger.info("✅ Сервер %s очищен.", server.get("name", key))

        logger.info("🏁 Ночная очистка завершена.")
