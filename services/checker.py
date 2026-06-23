import asyncio
import json
import logging

from services.store import load_servers_sync
from services.ssh import execute_command
from services.names import cache_bot_name, get_bot_name

from loader import bot
from config import GROUP_CHAT_ID

logger = logging.getLogger(__name__)

DEFAULT_BOTS = ["bot1", "bot2", "bot3"]
DEFAULT_PORTS = {"bot1": 3001, "bot2": 3002, "bot3": 3003}

# Глобальный кэш, чтобы не спамить уведомлениями о квоте каждые 5 минут
QUOTA_ALERTS_SENT: set[str] = set()


async def check_bot_status(server: dict, bot_name: str, server_key: str) -> str:
    """
    За один SSH-заход:
      1) читаем свежие логи (ищем insufficient_quota),
      2) дёргаем /api/status бота,
      3) пытаемся получить имя с /api/bot-name (если endpoint есть).
    """
    bot_ports = server.get("bot_ports", DEFAULT_PORTS)
    api_port = bot_ports.get(bot_name)

    # central-admin доступен по имени сервиса внутри docker-сети.
    # Можно переопределить индивидуально на сервер в servers.json.
    admin_url = server.get("central_admin_url", "http://central-admin:8000")

    cmd = (
        f"cd {server['path']} && "
        f"docker compose logs --tail=50 {bot_name} 2>&1 && "
        f"echo '||SEPARATOR||' && "
        f"docker exec {bot_name} node -e "
        f"\"fetch('http://localhost:{api_port}/api/status')"
        f".then(r=>r.text()).then(console.log)"
        f".catch(()=>console.log('{{\\\"error\\\": \\\"curl_failed\\\"}}'))\" && "
        f"echo '||NAMESEP||' && "
        f"docker exec {bot_name} node -e "
        f"\"fetch('{admin_url}/api/bot-name/{bot_name}')"
        f".then(r=>r.json()).then(d=>console.log(d.name||''))"
        f".catch(()=>console.log(''))\""
    )

    exit_code, stdout, stderr = await asyncio.to_thread(execute_command, server, cmd)
    full_output = stdout + stderr

    if exit_code != 0:
        return "❌ Ошибка (контейнер выключен?)"

    # Разбор: логи ||SEPARATOR|| статус ||NAMESEP|| имя
    try:
        head, _, name_part = full_output.partition("||NAMESEP||")
        fetched_name = name_part.strip()
        parts = head.split("||SEPARATOR||")
        recent_logs = parts[0]
        api_response_text = parts[1].strip() if len(parts) > 1 else "{}"
    except Exception:
        return "❓ Ошибка парсинга"

    # Если endpoint вернул имя — кэшируем (пусто => сработает фолбэк на bot_labels)
    cache_bot_name(server_key, bot_name, fetched_name)

    # --- Алерты по квоте OpenAI ---
    alert_key = f"{server['ip']}_{bot_name}"
    if "insufficient_quota" in recent_logs.lower():
        if alert_key not in QUOTA_ALERTS_SENT:
            await _send_quota_alert(server, server_key, bot_name)
            QUOTA_ALERTS_SENT.add(alert_key)
    else:
        QUOTA_ALERTS_SENT.discard(alert_key)

    # --- Статус по API ---
    if "curl_failed" in api_response_text:
        return f"🔴 Ошибка (API недоступен на порту {api_port})"

    try:
        data = json.loads(api_response_text)
    except json.JSONDecodeError:
        return f"❓ Ошибка API (порт {api_port})"

    if data.get("ready", False):
        phone = data.get("phone")
        return f"🟢 {phone}" if phone else "🟢 Работает (номер загружается)"
    return "🔴 Отключен"


async def _send_quota_alert(server: dict, server_key: str, bot_name: str) -> None:
    server_name = server.get("name", server["ip"])
    display_name = get_bot_name(server_key, server, bot_name)
    msg = (
        f"⚠️ <b>Системное уведомление: Лимит API</b>\n\n"
        f"Зафиксирована ошибка <code>insufficient_quota</code>.\n"
        f"Закончились деньги на OpenAI!\n—\n"
        f"🖥 <b>Сервер:</b> {server_name}\n"
        f"🤖 <b>Бот:</b> {display_name}\n—\n"
        f"🚨 <b>Клиенты не получают ответы, срочно пополните баланс!</b>"
    )
    try:
        await bot.send_message(chat_id=GROUP_CHAT_ID, text=msg)
        logger.info("Уведомление о квоте отправлено для %s", display_name)
    except Exception as e:
        logger.error("Не удалось отправить уведомление: %s", e)


async def check_server(server_key: str, server_data: dict) -> str:
    results = []
    bots_list = server_data.get("bots", DEFAULT_BOTS)

    for bot_name in bots_list:
        status = await check_bot_status(server_data, bot_name, server_key)
        display_name = get_bot_name(server_key, server_data, bot_name)
        results.append(f"🤖 <b>{display_name}</b>: {status}")

    header = f"<b>🖥 {server_data['ip']}:</b>"
    return f"{header}\n" + "\n".join(results)


async def check_all_servers() -> str:
    servers = load_servers_sync()
    if not servers:
        return "⚠️ Список серверов пуст. Добавьте сервер через меню."

    keys = list(servers.keys())
    tasks = [check_server(k, servers[k]) for k in keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    reports = []
    for key, res in zip(keys, results):
        if isinstance(res, Exception):
            logger.error("Ошибка проверки сервера %s: %s", key, res)
            reports.append(f"<b>🖥 {servers[key].get('ip', key)}:</b>\n❌ Ошибка опроса")
        else:
            reports.append(res)
    return "\n\n".join(reports)
