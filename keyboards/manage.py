"""Клавиатуры раздела «Управление» (сервера/пользователи). Admin-only UI."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.management import EDITABLE_SERVER_FIELDS

SERVERS_PER_PAGE = 8

# Подписи кнопок редактирования полей сервера
_FIELD_LABELS = {
    "name": "✏️ Имя",
    "ip": "🌐 IP",
    "user": "👤 SSH-user",
    "password": "🔑 Пароль",
    "path": "📁 Путь",
    "bots": "🤖 Боты",
}


def manage_root_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🖥 Сервера", callback_data="mng_srv")
    b.button(text="👥 Пользователи", callback_data="mng_usr")
    b.button(text="⬅️ В главное меню", callback_data="back_to_main_menu")
    b.adjust(2, 1)
    return b.as_markup()


def manage_servers_kb(servers: dict, page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    keys = list(servers.keys())
    pages = max(1, (len(keys) + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = keys[page * SERVERS_PER_PAGE:(page + 1) * SERVERS_PER_PAGE]

    for k in chunk:
        b.button(text=f"🖥 {servers[k]['name']}", callback_data=f"mng_srv_pick_{k}")
    b.adjust(1)

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mng_srv_pg_{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mng_srv_pg_{page + 1}"))
        b.row(*nav)

    b.row(InlineKeyboardButton(text="➕ Добавить сервер", callback_data="mng_srv_add"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="mng_root"))
    return b.as_markup()


def server_card_kb(server_key: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for field in EDITABLE_SERVER_FIELDS:
        label = _FIELD_LABELS.get(field, field)
        b.button(text=label, callback_data=f"mng_srv_edit_{field}_{server_key}")
    b.button(text="🔌 Проверить SSH-подключение", callback_data=f"mng_srv_checkssh_{server_key}")
    b.button(text="🗑 Удалить сервер", callback_data=f"mng_srv_askdel_{server_key}")
    b.button(text="⬅️ К списку", callback_data="mng_srv")
    b.adjust(2, 2, 2, 1, 1, 1)
    return b.as_markup()


def manage_users_kb(users: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for u in users:
        b.button(text=f"👤 {u['name']}", callback_data=f"mng_usr_pick_{u['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="mng_usr_add"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="mng_root"))
    return b.as_markup()


def user_card_kb(user_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Изменить имя", callback_data=f"mng_usr_editname_{user_id}")
    b.button(text="🗑 Удалить", callback_data=f"mng_usr_askdel_{user_id}")
    b.button(text="⬅️ К списку", callback_data="mng_usr")
    b.adjust(2, 1)
    return b.as_markup()


def confirm_kb(ok_cb: str, cancel_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, удалить", callback_data=ok_cb)
    b.button(text="❌ Отмена", callback_data=cancel_cb)
    b.adjust(2)
    return b.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="mng_cancel")
    return b.as_markup()