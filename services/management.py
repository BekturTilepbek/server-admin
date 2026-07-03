"""
Бизнес-логика управления серверами и пользователями (CRUD).

Хендлеры (handlers/manage.py) только собирают ввод по FSM и вызывают
эти функции, показывая готовый текст. Вся валидация/персистентность здесь.

Каждая функция возвращает кортеж (ok: bool, message: str) с готовым для
Telegram HTML-текстом.
"""
import re
import asyncio
import ipaddress
import logging

from services.store import (
    load_servers_sync,
    load_users_raw,
    save_server,
    delete_server,
    save_users,
)
from services.ssh import execute_command

logger = logging.getLogger(__name__)

DEFAULT_PATH = "/root/servers_admin_bot"
DEFAULT_USER = "root"
DEFAULT_BOTS = ["bot1", "bot2", "bot3"]

# Поля сервера, доступные для редактирования из бота: id -> подпись
EDITABLE_SERVER_FIELDS: dict[str, str] = {
    "name": "Имя",
    "ip": "IP-адрес",
    "user": "SSH-пользователь",
    "password": "SSH-пароль",
    "path": "Путь к проекту",
    "bots": "Список ботов",
}


# ---------------- Валидация / парсинг ----------------

def validate_ip(ip: str) -> str | None:
    ip = (ip or "").strip()
    try:
        ipaddress.ip_address(ip)
        return ip
    except ValueError:
        return None


def parse_bots(raw: str) -> list[str]:
    bots = [b.strip() for b in re.split(r"[,\s]+", raw or "") if b.strip()]
    return bots or list(DEFAULT_BOTS)


def default_ports(bots: list[str]) -> dict[str, int]:
    """botN -> 3000 + N. Нестандартные имена пропускаем (задаются вручную)."""
    ports: dict[str, int] = {}
    for b in bots:
        m = re.fullmatch(r"bot(\d+)", b)
        if m:
            ports[b] = 3000 + int(m.group(1))
    return ports


# ---------------- Сервера ----------------

async def create_server(data: dict, actor: str = "неизвестно") -> tuple[bool, str]:
    servers = load_servers_sync()

    ip = validate_ip(data.get("ip", ""))
    if not ip:
        logger.warning("➕ %s: попытка добавить сервер с некорректным IP: %r", actor, data.get("ip"))
        return False, "❌ Некорректный IP-адрес."
    if ip in servers:
        logger.warning("➕ %s: попытка добавить уже существующий сервер %s", actor, ip)
        return False, f"❌ Сервер с IP <code>{ip}</code> уже существует."

    bots = data.get("bots") or list(DEFAULT_BOTS)
    server_obj: dict = {
        "name": (data.get("name") or ip).strip(),
        "ip": ip,
        "user": (data.get("user") or DEFAULT_USER).strip(),
        "password": data.get("password", ""),
        "path": (data.get("path") or DEFAULT_PATH).strip(),
        "bots": bots,
    }
    ports = default_ports(bots)
    if ports:
        server_obj["bot_ports"] = ports

    await save_server(ip, server_obj)
    logger.info(
        "➕ %s: добавлен новый сервер «%s» (%s), боты: %s",
        actor, server_obj["name"], ip, ", ".join(bots),
    )
    return True, (
        f"✅ Сервер <b>{server_obj['name']}</b> добавлен.\n"
        f"🌐 IP: <code>{ip}</code>\n"
        f"🤖 Боты: {', '.join(bots)}"
    )


async def update_server_field(server_key: str, field: str, value: str, actor: str = "неизвестно") -> tuple[bool, str]:
    servers = load_servers_sync()
    src = servers.get(server_key)
    if not src:
        logger.warning("✏️ %s: попытка изменить несуществующий сервер %s", actor, server_key)
        return False, "❌ Сервер не найден (возможно, уже удалён)."

    server = dict(src)  # копия, чтобы не мутировать кэш
    value = (value or "").strip()

    if field == "ip":
        new_ip = validate_ip(value)
        if not new_ip:
            logger.warning("✏️ %s: некорректный новый IP для сервера %s: %r", actor, server_key, value)
            return False, "❌ Некорректный IP-адрес."
        if new_ip != server_key and new_ip in servers:
            logger.warning("✏️ %s: новый IP %s уже занят (сервер %s)", actor, new_ip, server_key)
            return False, f"❌ Сервер с IP <code>{new_ip}</code> уже есть."
        server["ip"] = new_ip
        # IP — это ключ словаря серверов: пересоздаём запись под новым ключом
        await delete_server(server_key)
        await save_server(new_ip, server)
        logger.info("✏️ %s: IP сервера «%s» изменён: %s 👉 %s", actor, server.get("name", server_key), server_key, new_ip)
        return True, f"✅ IP изменён: <code>{server_key}</code> 👉 <code>{new_ip}</code>"

    if field == "bots":
        bots = parse_bots(value)
        server["bots"] = bots
        ports = default_ports(bots)
        if ports:
            server["bot_ports"] = ports
        await save_server(server_key, server)
        logger.info("✏️ %s: список ботов сервера «%s» обновлён: %s", actor, server.get("name", server_key), ", ".join(bots))
        return True, f"✅ Список ботов обновлён: {', '.join(bots)}"

    if field not in EDITABLE_SERVER_FIELDS:
        logger.warning("✏️ %s: попытка изменить неизвестное поле %r у сервера %s", actor, field, server_key)
        return False, "❌ Неизвестное поле."

    server[field] = value
    await save_server(server_key, server)
    label = EDITABLE_SERVER_FIELDS.get(field, field)

    # Значение пароля в лог НЕ пишем — только сам факт изменения.
    if field == "password":
        logger.info("✏️ %s: пароль сервера «%s» изменён", actor, server.get("name", server_key))
    else:
        logger.info("✏️ %s: поле «%s» сервера «%s» изменено на: %r", actor, label, server.get("name", server_key), value)

    return True, f"✅ Поле «{label}» обновлено."


async def remove_server(server_key: str, actor: str = "неизвестно") -> tuple[bool, str]:
    servers = load_servers_sync()
    server = servers.get(server_key)
    if not server:
        logger.warning("🗑 %s: попытка удалить несуществующий сервер %s", actor, server_key)
        return False, "❌ Сервер не найден (возможно, уже удалён)."
    await delete_server(server_key)
    logger.warning("🗑 %s: сервер «%s» (%s) УДАЛЁН", actor, server.get("name", server_key), server_key)
    return True, f"🗑 Сервер <b>{server.get('name', server_key)}</b> удалён."


async def check_ssh_connection(server_key: str, actor: str = "неизвестно") -> tuple[bool, str]:
    """
    Лёгкая проверка SSH: подключается и выполняет безобидную команду
    (hostname + версия docker), не трогая контейнеры и данные сервера.
    """
    server = load_servers_sync().get(server_key)
    if not server:
        logger.warning("🔌 %s: проверка SSH — сервер %s не найден", actor, server_key)
        return False, "❌ Сервер не найден (возможно, уже удалён)."

    logger.info("🔌 %s: проверяет SSH-подключение к «%s» (%s)", actor, server.get("name", server_key), server.get("ip", server_key))

    cmd = "echo __SSH_OK__ && hostname && docker --version 2>&1"
    status, out, err = await asyncio.to_thread(execute_command, server, cmd)

    if status == 0 and "__SSH_OK__" in out:
        info = out.replace("__SSH_OK__", "").strip()
        logger.info("✅ %s: SSH-подключение к «%s» успешно", actor, server.get("name", server_key))
        return True, (
            f"✅ <b>SSH-подключение успешно!</b>\n"
            f"🖥 {server.get('name', server_key)} (<code>{server.get('ip', server_key)}</code>)\n"
            f"<pre>{info}</pre>"
        )

    detail = (err or out or "нет ответа от сервера").strip()
    logger.error("❌ %s: SSH-подключение к «%s» не удалось: %s", actor, server.get("name", server_key), detail)
    return False, (
        f"❌ <b>Не удалось подключиться</b>\n"
        f"🖥 {server.get('name', server_key)} (<code>{server.get('ip', server_key)}</code>)\n"
        f"<pre>{detail}</pre>\n"
        f"Проверьте IP, SSH-пользователя и пароль."
    )


# ---------------- Пользователи ----------------

async def create_user(user_id: int, name: str, actor: str = "неизвестно") -> tuple[bool, str]:
    users = [dict(u) for u in load_users_raw()]  # копия
    if any(u["id"] == user_id for u in users):
        logger.warning("👥 %s: попытка добавить уже существующего пользователя %s", actor, user_id)
        return False, f"❌ Пользователь с ID <code>{user_id}</code> уже существует."
    users.append({"id": user_id, "name": (name or str(user_id)).strip()})
    await save_users(users)
    logger.info("👥 %s: добавлен пользователь «%s» (ID:%s)", actor, name, user_id)
    return True, f"✅ Пользователь <b>{name}</b> (<code>{user_id}</code>) добавлен."


async def update_user_name(user_id: int, name: str, actor: str = "неизвестно") -> tuple[bool, str]:
    users = [dict(u) for u in load_users_raw()]
    found = False
    old_name = None
    for u in users:
        if u["id"] == user_id:
            old_name = u["name"]
            u["name"] = (name or str(user_id)).strip()
            found = True
            break
    if not found:
        logger.warning("👥 %s: попытка переименовать несуществующего пользователя %s", actor, user_id)
        return False, f"❌ Пользователь <code>{user_id}</code> не найден."
    await save_users(users)
    logger.info("👥 %s: пользователь ID:%s переименован: «%s» 👉 «%s»", actor, user_id, old_name, name)
    return True, f"✅ Имя обновлено: <code>{user_id}</code> 👉 <b>{name}</b>"


async def remove_user(user_id: int, actor: str = "неизвестно") -> tuple[bool, str]:
    users = [dict(u) for u in load_users_raw()]
    removed = next((u for u in users if u["id"] == user_id), None)
    new_users = [u for u in users if u["id"] != user_id]
    if len(new_users) == len(users):
        logger.warning("🗑 %s: попытка удалить несуществующего пользователя %s", actor, user_id)
        return False, f"❌ Пользователь <code>{user_id}</code> не найден."
    await save_users(new_users)
    logger.warning("🗑 %s: пользователь «%s» (ID:%s) УДАЛЁН", actor, removed.get("name") if removed else "?", user_id)
    return True, f"🗑 Пользователь <code>{user_id}</code> удалён."