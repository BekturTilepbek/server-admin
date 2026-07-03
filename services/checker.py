import asyncio
import json
import logging
import re

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

# Ручной запуск "Проверить статус всех ботов" тоже не должен занимать
# больше N SSH-соединений одновременно (см. SCAN_CONCURRENCY в scheduler.py) —
# иначе при 30+ серверах он выедает весь thread pool разом.
_MANUAL_CHECK_CONCURRENCY = 10

BOT_MARKER_PREFIX = "===BOT_START:"
BOT_MARKER_SUFFIX = "==="
_BOT_MARKER_RE = re.compile(
    re.escape(BOT_MARKER_PREFIX) + r"(.+?)" + re.escape(BOT_MARKER_SUFFIX)
)

# Каждый бот последовательно отрабатывает logs+status+name внутри ОДНОЙ SSH-
# сессии. Базовый EXEC_TIMEOUT + запас на каждого следующего бота.
PER_BOT_TIMEOUT = 25
BASE_TIMEOUT = 20
MAX_TIMEOUT = 240


def _build_bot_segment(server: dict, bot_name: str) -> str:
    """Команда для одного бота: логи + /api/status + /api/bot-name.
    Обёрнута в подшелл и маркер начала, чтобы падение одного бота
    (например, контейнер выключен) не срывало опрос остальных ботов —
    сегменты между собой соединяются через ';', а не '&&'.
    """
    bot_ports = server.get("bot_ports", DEFAULT_PORTS)
    api_port = bot_ports.get(bot_name)
    admin_url = server.get("central_admin_url", "http://central-admin:8000")

    return (
        f"( echo '{BOT_MARKER_PREFIX}{bot_name}{BOT_MARKER_SUFFIX}' && "
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
        f".catch(()=>console.log(''))\" )"
    )


async def _fetch_combined_bot_output(server: dict, bots_list: list[str]) -> tuple[bool, dict[str, str]]:
    """
    Одно SSH-соединение на сервер: опрашивает всех ботов последовательно
    внутри сессии вместо отдельного connect/exec/close на каждого бота.
    Резко сокращает число SSH-хендшейков (самая CPU-затратная часть
    paramiko) при 30+ серверах x 3 бота.

    Возвращает (ssh_ok, {bot_name: raw_output}).
    """
    segments = [_build_bot_segment(server, b) for b in bots_list]
    full_cmd = f"cd {server['path']} && " + " ; ".join(segments)

    exec_timeout = min(MAX_TIMEOUT, BASE_TIMEOUT + PER_BOT_TIMEOUT * len(bots_list))

    exit_code, stdout, stderr = await asyncio.to_thread(
        execute_command, server, full_cmd, exec_timeout
    )
    full_output = stdout + stderr

    if exit_code == -1 and not full_output.strip():
        # SSH-соединение вообще не установилось (недоступен, неверный пароль и т.п.)
        return False, {}

    # Разбираем вывод по маркерам ===BOT_START:name===
    chunks: dict[str, str] = {}
    matches = list(_BOT_MARKER_RE.finditer(full_output))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_output)
        chunks[name] = full_output[start:end]

    return True, chunks


def _parse_bot_chunk(raw: str | None) -> tuple[str, str, str]:
    """Возвращает (recent_logs, api_response_text, fetched_name) из сырого куска."""
    if not raw:
        return "", "{}", ""
    try:
        head, _, name_part = raw.partition("||NAMESEP||")
        fetched_name = name_part.strip()
        parts = head.split("||SEPARATOR||")
        recent_logs = parts[0]
        api_response_text = parts[1].strip() if len(parts) > 1 else "{}"
        return recent_logs, api_response_text, fetched_name
    except Exception:
        return "", "{}", ""


async def _resolve_bot_status(
    server: dict, server_key: str, bot_name: str, raw_chunk: str | None
) -> str:
    if raw_chunk is None:
        return "❌ Ошибка (контейнер выключен?)"

    recent_logs, api_response_text, fetched_name = _parse_bot_chunk(raw_chunk)
    if not recent_logs and api_response_text == "{}" and not fetched_name:
        return "❓ Ошибка парсинга"

    bot_ports = server.get("bot_ports", DEFAULT_PORTS)
    api_port = bot_ports.get(bot_name)

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


async def check_bot_status(server: dict, bot_name: str, server_key: str) -> str:
    """
    Точечная проверка ОДНОГО бота (отдельное SSH-соединение). Используется,
    когда нужен статус конкретного бота без опроса всего сервера — основной
    путь опроса теперь идёт через check_server()/_fetch_combined_bot_output(),
    который опрашивает всех ботов сервера за одно SSH-соединение.
    """
    cmd = f"cd {server['path']} && " + _build_bot_segment(server, bot_name)
    exit_code, stdout, stderr = await asyncio.to_thread(execute_command, server, cmd)
    full_output = stdout + stderr

    if exit_code != 0:
        return "❌ Ошибка (контейнер выключен?)"

    matches = list(_BOT_MARKER_RE.finditer(full_output))
    raw = full_output[matches[0].end():] if matches else full_output
    return await _resolve_bot_status(server, server_key, bot_name, raw)


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
    bots_list = server_data.get("bots", DEFAULT_BOTS)
    header = f"<b>🖥 {server_data['ip']}:</b>"

    if not bots_list:
        return f"{header}\n(нет ботов на сервере)"

    ssh_ok, chunks = await _fetch_combined_bot_output(server_data, bots_list)

    results = []
    for bot_name in bots_list:
        display_name = get_bot_name(server_key, server_data, bot_name)
        if not ssh_ok:
            status = "❌ Ошибка (SSH недоступен)"
        else:
            status = await _resolve_bot_status(
                server_data, server_key, bot_name, chunks.get(bot_name)
            )
        results.append(f"🤖 <b>{display_name}</b>: {status}")

    return f"{header}\n" + "\n".join(results)


async def check_all_servers() -> str:
    servers = load_servers_sync()
    if not servers:
        return "⚠️ Список серверов пуст. Добавьте сервер через меню."

    sem = asyncio.Semaphore(_MANUAL_CHECK_CONCURRENCY)

    async def _guarded(key: str, data: dict):
        async with sem:
            return await check_server(key, data)

    keys = list(servers.keys())
    tasks = [_guarded(k, servers[k]) for k in keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    reports = []
    for key, res in zip(keys, results):
        if isinstance(res, Exception):
            logger.error("Ошибка проверки сервера %s: %s", key, res)
            reports.append(f"<b>🖥 {servers[key].get('ip', key)}:</b>\n❌ Ошибка опроса")
        else:
            reports.append(res)
    return "\n\n".join(reports)