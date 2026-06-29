import asyncio
from aiogram import Router, types, F
import html
import re

from config import ADMIN_ID
from services.store import load_servers_sync
from services.ssh import execute_command
from keyboards.reply import bot_selection_menu, server_actions_menu, bot_unlink_menu

router = Router()


# --- Вспомогательные функции ---

async def perform_restart(call: types.CallbackQuery, server_key, mode):
    servers = load_servers_sync()
    server = servers.get(server_key)

    if not server:
        return await call.answer("❌ Сервер не найден (возможно удален)", show_alert=True)

    cmd = f"cd {server['path']} && docker compose down && docker compose up -d --build"

    await call.message.edit_text(f"⏳ Рестарт {server['name']}...")

    # МАГИЯ: Запускаем синхронную SSH функцию в отдельном потоке
    status, out, err = await asyncio.to_thread(execute_command, server, cmd)

    is_admin = call.from_user.id == ADMIN_ID
    if status == 0:
        result = "✅ Успешно перезапущен!"
    else:
        result = f"❌ Ошибка:\n<pre>{html.escape(err)}</pre>"
    await call.message.edit_text(
        f"<b>{server['name']}</b>: {result}",
        reply_markup=server_actions_menu(server_key, is_admin=is_admin)
    )


async def perform_full_rebuild(call: types.CallbackQuery, server_key):
    """Полная пересборка без кэша (только для админа).

    Порядок: down -> build --no-cache -> up -d. Сборка может идти несколько
    минут, поэтому SSH-команде даём расширенный таймаут.
    """
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔️ Только для администратора", show_alert=True)

    servers = load_servers_sync()
    server = servers.get(server_key)

    if not server:
        return await call.answer("❌ Сервер не найден (возможно удален)", show_alert=True)

    cmd = (
        f"cd {server['path']} && "
        f"docker compose down && "
        f"docker compose build --no-cache && "
        f"docker compose up -d"
    )

    await call.message.edit_text(
        f"⏳ Полный рестарт <b>{server['name']}</b> без кэша...\n"
        f"<i>Пересборка образов может занять несколько минут, подождите.</i>"
    )

    # Расширенный таймаут (15 минут) — обычного 60с не хватит на --no-cache.
    status, out, err = await asyncio.to_thread(execute_command, server, cmd, 900)

    if status == 0:
        result = "✅ Образы пересобраны без кэша и контейнеры перезапущены!"
    else:
        result = f"❌ Ошибка:\n<pre>{html.escape(err)}</pre>"
    await call.message.edit_text(
        f"<b>{server['name']}</b>: {result}",
        reply_markup=server_actions_menu(server_key, is_admin=True)
    )


async def perform_logs(call: types.CallbackQuery, server_key, bot_name):
    servers = load_servers_sync()
    server = servers.get(server_key)

    if not server:
        return await call.answer("Сервер не найден", show_alert=True)

    cmd = f"cd {server['path']} && docker compose logs --tail=300 {bot_name}"

    await call.message.edit_text(f"⏳ Читаю логи {bot_name}...")

    status, out, err = await asyncio.to_thread(execute_command, server, cmd)
    log_data = out if status == 0 else err

    if len(log_data) > 4000: log_data = log_data[-4000:]  # Обрезаем под лимиты ТГ

    # 1. Вычищаем мусорные цветовые коды терминала (ANSI)
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    clean_log = ansi_escape.sub('', log_data)

    # 2. Обрезаем сырой текст ДО экранирования.
    # Берем с запасом (3000 символов), так как после экранирования текст станет длиннее.
    # Берем с конца, чтобы видеть самые свежие ошибки.
    if len(clean_log) > 3000:
        clean_log = "...\n" + clean_log[-3000:]

    # 3. Делаем текст безопасным для Telegram HTML (< превратится в &lt;)
    safe_log = html.escape(clean_log)

    try:
        # ПЛАН А: Отправляем красиво в тегах <pre>
        await call.message.edit_text(
            f"📋 Логи <b>{server['name']} - {bot_name}</b>:\n<pre>{safe_log}</pre>",
            reply_markup=bot_selection_menu(server, server_key)
        )
    except Exception as e:
        # ПЛАН Б: Если Telegram всё равно отклонил (очень редкий случай),
        # отключаем HTML-разметку (parse_mode=None) и отправляем как сырой текст.
        # Это сработает в 100% случаев.
        raw_text = f"Логи {server['name']} - {bot_name}:\n\n{clean_log}"

        # Еще раз проверяем лимит самого Telegram (4096)
        if len(raw_text) > 4000:
            raw_text = raw_text[-4000:]

        await call.message.edit_text(
            raw_text,
            reply_markup=bot_selection_menu(server, server_key),
            parse_mode=None  # Отключаем HTML парсер для этого сообщения
        )


async def perform_unlink(call: types.CallbackQuery, server_key, bot_name):
    servers = load_servers_sync()
    server = servers.get(server_key)

    if not server:
        return await call.answer("❌ Сервер не найден", show_alert=True)

    await call.message.edit_text(
        f"⏳ <b>{bot_name}</b>: Отвязываю WhatsApp...\n"
        f"1️⃣ Останавливаю бота...\n"
        f"2️⃣ Удаляю сессию...\n"
        f"3️⃣ Запускаю заново..."
    )

    # Логика: переходим в папку -> стопаем бота -> удаляем всё внутри папки сессии -> стартуем бота
    cmd = f"cd {server['path']} && docker compose stop {bot_name} && rm -rf sessions/{bot_name}/* && docker compose start {bot_name}"

    status, out, err = await asyncio.to_thread(execute_command, server, cmd)

    if status == 0:
        result = "✅ <b>WhatsApp успешно отвязан!</b>\nБот запущен с чистой сессией. Откройте 📋 Логи, чтобы отсканировать новый QR-код."
    else:
        result = f"❌ Ошибка при отвязке:\n<pre>{err}</pre>"

    await call.message.edit_text(
        result,
        reply_markup=server_actions_menu(server_key, is_admin=call.from_user.id == ADMIN_ID)
    )

# --- Хендлеры ---

@router.callback_query(F.data.startswith('list_bots_'))
async def list_bots_handler(call: types.CallbackQuery):
    key = call.data.split('_', 2)[2]

    servers = load_servers_sync()
    server = servers.get(key)

    if not server:
        return await call.answer("Сервер не найден", show_alert=True)

    await call.message.edit_text(
        f"<b>{server['name']}</b> - Выберите бота:",
        reply_markup=bot_selection_menu(server, key)
    )

@router.callback_query(F.data.startswith('list_unlink_'))
async def list_unlink_handler(call: types.CallbackQuery):
    key = call.data.split('_', 2)[2]
    servers = load_servers_sync()
    server = servers.get(key)

    if not server:
        return await call.answer("Сервер не найден", show_alert=True)

    await call.message.edit_text(
        f"<b>{server['name']}</b>\n⚠️ Выберите бота для ОТВЯЗКИ WhatsApp.\n"
        f"<i>Текущая сессия будет безвозвратно удалена!</i>",
        reply_markup=bot_unlink_menu(server, key)
    )


# Обработка действий
@router.callback_query(F.data.func(
    lambda data: data.startswith('run_') or data.startswith('unlink_')
    or 'getlogs_' in data))
async def execute_handler(call: types.CallbackQuery):
    if 'getlogs_' in call.data:
        parts = call.data.split('_', 2)
        await perform_logs(call, parts[2], parts[1])

    elif call.data.startswith('run_nocache_'):
        server_key = call.data.replace('run_nocache_', '')
        await perform_full_rebuild(call, server_key)

    elif call.data.startswith('run_normal_'):
        parts = call.data.split('_', 2)
        await perform_restart(call, parts[2], "normal")

    elif call.data.startswith('unlink_'):
        # Формат: unlink_bot1_137.184.14.83
        parts = call.data.split('_', 2)
        await perform_unlink(call, parts[2], parts[1])