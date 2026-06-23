from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from services.names import get_bot_name

STANDARD_BOTS = ["bot1", "bot2", "bot3"]
SERVERS_PER_PAGE = 8  # пагинация для 30+ серверов


def main_menu(servers: dict, user_id: int, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    keys = list(servers.keys())
    total = len(keys)
    pages = max(1, (total + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE)
    page = max(0, min(page, pages - 1))

    start = page * SERVERS_PER_PAGE
    chunk = keys[start:start + SERVERS_PER_PAGE]

    server_buttons = [
        InlineKeyboardButton(
            text=f"🖥 {servers[k]['name']}",
            callback_data=f"select_server_{k}",
        )
        for k in chunk
    ]
    if server_buttons:
        builder.row(*server_buttons, width=2)

    # Навигация по страницам (только если страниц больше одной)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"menu_page_{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"menu_page_{page + 1}"))
        builder.row(*nav)

    builder.row(InlineKeyboardButton(
        text="📊 Проверить статус всех ботов", callback_data="check_all_now"
    ))

    if user_id == ADMIN_ID:
        builder.row(InlineKeyboardButton(
            text="📑 Логи самого бота", callback_data="get_bot_sys_logs"
        ))

    return builder.as_markup()


def back_to_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к списку", callback_data="back_to_main_menu")
    return builder.as_markup()


def server_actions_menu(server_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Рестарт ВСЕГО сервера", callback_data=f"run_normal_{server_key}")
    builder.button(text="🔄 Рестарт определенного бота", callback_data=f"list_restart_{server_key}")
    builder.button(text="🔌 Отвязать WhatsApp", callback_data=f"list_unlink_{server_key}")
    builder.button(text="📋 Логи", callback_data=f"list_bots_{server_key}")
    builder.button(text="⚙️ Обновить статус (Real-time)", callback_data=f"refresh_{server_key}")
    builder.button(text="🧹 Очистить кэш", callback_data=f"start_clearchat_{server_key}")
    builder.button(text="⬅️ Назад", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def bot_selection_menu(server_data: dict, server_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    bots = server_data.get("bots", STANDARD_BOTS)
    builder.button(text="🖥️ Central-admin", callback_data=f"getlogs_central-admin_{server_key}")
    for b in bots:
        name = get_bot_name(server_key, server_data, b)
        builder.button(text=f"🤖 {name}", callback_data=f"getlogs_{b}_{server_key}")
    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)
    return builder.as_markup()


def bot_restart_menu(server_data: dict, server_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    bots = server_data.get("bots", STANDARD_BOTS)
    builder.button(text="🖥️ Central-admin", callback_data=f"runbot_central-admin_{server_key}")
    for b in bots:
        name = get_bot_name(server_key, server_data, b)
        builder.button(text=f"🔄 {name}", callback_data=f"runbot_{b}_{server_key}")
    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)
    return builder.as_markup()


def bot_unlink_menu(server_data: dict, server_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    bots = server_data.get("bots", STANDARD_BOTS)
    for b in bots:
        name = get_bot_name(server_key, server_data, b)
        builder.button(text=f"🔌 {name} (Отвязать)", callback_data=f"unlink_{b}_{server_key}")
    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)
    return builder.as_markup()
