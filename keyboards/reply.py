from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import ADMIN_ID

STANDARD_BOTS = ["bot1", "bot2", "bot3"]

def main_menu(servers, user_id):
    builder = InlineKeyboardBuilder()

    server_buttons = []
    for key, data in servers.items():
        server_buttons.append(
            InlineKeyboardButton(
                text=f"🖥 {data['name']}",
                callback_data=f"select_server_{key}"
            )
        )

    if server_buttons:
        builder.row(*server_buttons, width=2)

    builder.row(InlineKeyboardButton(text="📊 Проверить статус всех ботов", callback_data="check_all_now"))

    # СЕКРЕТНАЯ КНОПКА (Видит только Админ)
    if user_id == ADMIN_ID:
        builder.row(InlineKeyboardButton(text="📑 Логи самого бота", callback_data="get_bot_sys_logs"))

    return builder.as_markup()

def back_to_main_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к списку", callback_data="back_to_main_menu")
    return builder.as_markup()

def server_actions_menu(server_key):
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

def bot_selection_menu(server_data, server_key):
    builder = InlineKeyboardBuilder()
    bots = server_data.get('bots', ["bot1", "bot2", "bot3"])
    # Достаем красивые имена
    labels = server_data.get('bot_labels', {})

    builder.button(text="🖥️ Central-admin", callback_data=f"getlogs_central-admin_{server_key}")

    for b in bots:
        # Если есть имя - используем, иначе техническое
        name = labels.get(b, b)
        builder.button(text=f"🤖 {name}", callback_data=f"getlogs_{b}_{server_key}")
    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)
    return builder.as_markup()


def bot_restart_menu(server_data, server_key):
    builder = InlineKeyboardBuilder()
    bots = server_data.get('bots', ["bot1", "bot2", "bot3"])
    labels = server_data.get('bot_labels', {})

    # Добавляем центральную админку, так как её тоже бывает полезно перезапустить
    builder.button(text="🖥️ Central-admin", callback_data=f"runbot_central-admin_{server_key}")

    for b in bots:
        name = labels.get(b, b)
        builder.button(text=f"🔄 {name}", callback_data=f"runbot_{b}_{server_key}")

    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)
    return builder.as_markup()


def bot_unlink_menu(server_data, server_key):
    builder = InlineKeyboardBuilder()
    bots = server_data.get('bots', ["bot1", "bot2", "bot3"])
    labels = server_data.get('bot_labels', {})

    for b in bots:
        name = labels.get(b, b)
        builder.button(text=f"🔌 {name} (Отвязать)", callback_data=f"unlink_{b}_{server_key}")

    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)
    return builder.as_markup()