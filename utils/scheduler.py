import asyncio
import logging
from datetime import datetime, timedelta, timezone

from services.checker import check_server
from services.store import load_servers_sync, save_cache
from services.ssh import execute_command
from services.billing import get_all_accounts_billing, format_digest, has_problem

from loader import bot
from config import GROUP_CHAT_ID

logger = logging.getLogger(__name__)

TZ_GMT6 = timezone(timedelta(hours=6))
SCAN_INTERVAL = 300  # 5 минут

# Фоновый скан не должен занимать больше N SSH-соединений одновременно —
# иначе при 30+ серверах он выедает весь thread pool, и кнопки оператора
# (тоже идущие через asyncio.to_thread) встают в очередь за сканом.
# Оставляем запас пула под интерактивные действия.
SCAN_CONCURRENCY = 10

# --- БИЛЛИНГ DO ---
BILLING_DIGEST_HOUR = 10        # ежедневный дайджест в 10:00 по Бишкеку (GMT+6)
BILLING_CHECK_INTERVAL = 1800   # проверка статуса на алерты каждые 30 минут
# Антиспам: метки аккаунтов, по которым уже отправлен алерт о проблеме.
# Сбрасывается, когда аккаунт снова active — чтобы повторно среагировать
# на новую блокировку.
_BILLING_ALERTS_SENT: set[str] = set()


async def update_statuses_task():
    """Бесконечный цикл обновления статусов всех серверов."""
    logger.info("⏳ Запущен планировщик проверки серверов...")
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _guarded_check(key: str, data: dict):
        async with sem:
            return await check_server(key, data)

    while True:
        try:
            servers = load_servers_sync()
            keys = list(servers.keys())
            tasks = [_guarded_check(k, servers[k]) for k in keys]

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


# --------------------- БИЛЛИНГ DIGITALOCEAN ---------------------

async def _send_billing_alerts(accounts: list[dict]) -> None:
    """
    Шлёт алерт по каждому проблемному аккаунту (задолженность или не опросился).
    Антиспам: повторно по тому же аккаунту не шлём, пока долг не исчезнет.
    """
    for acc in accounts:
        label = acc["label"]
        problem = (not acc["ok"]) or (acc["balance_status"] == "debt")

        if not problem:
            _BILLING_ALERTS_SENT.discard(label)
            continue

        if label in _BILLING_ALERTS_SENT:
            continue  # уже предупреждали

        if not acc["ok"]:
            reason = f"не удалось опросить аккаунт ({acc['error']})"
        else:
            from services.billing import _money  # локальный импорт, без цикла
            reason = (
                f"есть задолженность <b>{_money(acc['account_balance'])}</b> — "
                f"списание не прошло, аккаунт может быть заблокирован"
            )

        msg = (
            f"🚨 <b>Биллинг DigitalOcean: внимание!</b>\n\n"
            f"📧 <b>Аккаунт:</b> {label}\n"
            f"⚠️ {reason}\n"
        )
        if acc["ok"]:
            from services.billing import _money
            msg += (
                f"💸 Потрачено за месяц: <b>{_money(acc['month_to_date'])}</b>\n\n"
                f"❗️ Срочно оплатите задолженность в личном кабинете DO, "
                f"чтобы избежать остановки серверов."
            )
        else:
            msg += "\n❗️ Проверьте токен и доступность аккаунта."

        try:
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=msg)
            _BILLING_ALERTS_SENT.add(label)
            logger.info("Биллинг-алерт отправлен для %s", label)
        except Exception as e:
            logger.error("Не удалось отправить биллинг-алерт (%s): %s", label, e)


async def billing_alert_task():
    """Периодическая проверка статусов аккаунтов DO на предмет проблем."""
    logger.info("⏳ Запущен мониторинг биллинга DigitalOcean...")
    while True:
        try:
            accounts = await get_all_accounts_billing()
            if accounts and has_problem(accounts):
                await _send_billing_alerts(accounts)
            elif accounts:
                # всё в норме — чистим антиспам-набор
                _BILLING_ALERTS_SENT.clear()
        except Exception as e:
            logger.exception("Ошибка в мониторинге биллинга: %s", e)

        await asyncio.sleep(BILLING_CHECK_INTERVAL)


async def billing_digest_task():
    """Ежедневный дайджест по биллингу DO в 10:00 по Бишкеку (GMT+6)."""
    while True:
        now = datetime.now(TZ_GMT6)
        target = now.replace(
            hour=BILLING_DIGEST_HOUR, minute=0, second=0, microsecond=0
        )
        if now >= target:
            target += timedelta(days=1)

        sleep_seconds = (target - now).total_seconds()
        logger.info(
            "⏳ Дайджест биллинга запланирован на %s (через %.2f ч)",
            target.strftime("%Y-%m-%d %H:%M:%S"), sleep_seconds / 3600,
        )
        await asyncio.sleep(sleep_seconds)

        try:
            accounts = await get_all_accounts_billing()
            if accounts:
                text = format_digest(accounts)
                await bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
                logger.info("✅ Ежедневный дайджест биллинга отправлен.")
        except Exception as e:
            logger.exception("Ошибка при отправке дайджеста биллинга: %s", e)