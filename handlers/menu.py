from aiogram import Router, types, F
from aiogram.filters import Command

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from services.store import load_servers_sync, get_cached_status
from keyboards.reply import main_menu, back_to_main_kb, server_actions_menu
from services import check_all_servers, check_server, clear_cache

# Создаем роутер
router = Router()

class ClearChatState(StatesGroup):
    waiting_for_phone = State()

USER_LAST_PHONE: dict[int, str] = {}

@router.message(Command("start"))
async def start_cmd(message: types.Message):
    servers = load_servers_sync()
    await message.answer("🎛 Панель управления:", reply_markup=main_menu(servers, message.from_user.id))

@router.callback_query(F.data == "back_to_main_menu")
async def back_handler(call: types.CallbackQuery):
    servers = load_servers_sync()
    await call.message.edit_text("🎛 Панель управления:", reply_markup=main_menu(servers, call.from_user.id))


# --- ПРОВЕРКА ВСЕХ НОМЕРОВ (ОНЛАЙН) ---
@router.callback_query(F.data == "check_all_now")
async def check_all_handler(call: types.CallbackQuery):
    await call.message.edit_text("⏳ Сканирую все сервера в реальном времени...", reply_markup=None)
    report = await check_all_servers()

    # Полная замена сообщения + Кнопка Назад
    await call.message.edit_text(report, reply_markup=back_to_main_kb())

@router.callback_query(F.data.startswith('select_server_'))
async def server_menu_handler(call: types.CallbackQuery):
    key = call.data.split('_', 2)[2]
    servers = load_servers_sync()
    server = servers.get(key)

    # 1. Пробуем достать из кэша
    cached_text = await get_cached_status(key)

    if cached_text:
        # Если есть в кэше - показываем сразу!
        text = f"{cached_text}\n\n<i>(Обновляется каждые 5 мин)</i>\n👇 Выберите действие:"
        await call.message.edit_text(text, reply_markup=server_actions_menu(key))
    else:
        # Если кэша нет (бот только включился) - сканируем
        await call.message.edit_text(f"⏳ Сканирую {server['name']}...", reply_markup=None)
        report = await check_server(key, server)
        await call.message.edit_text(f"{report}\n\n👇 Выберите действие:", reply_markup=server_actions_menu(key))


# --- ОБНОВИТЬ ВРУЧНУЮ (Кнопка внутри сервера) ---
@router.callback_query(F.data.startswith('refresh_'))
async def refresh_server_handler(call: types.CallbackQuery):
    key = call.data.split('_', 1)[1]
    servers = load_servers_sync()

    await call.message.edit_text(f"⏳ Обновляю данные...", reply_markup=None)
    report = await check_server(key, servers[key])

    await call.message.edit_text(f"{report}\n\n👇 Выберите действие:", reply_markup=server_actions_menu(key))


# --- ШАГ 1: Нажали "Очистить кэш" ВНУТРИ меню сервера ---
@router.callback_query(F.data.startswith("start_clearchat_"))
async def clearchat_start_direct(call: types.CallbackQuery, state: FSMContext):
    # Достаем server_key прямо из callback_data
    server_key = call.data.replace("start_clearchat_", "")
    await state.update_data(server_key=server_key)

    servers = load_servers_sync()
    server = servers.get(server_key)

    if not server:
        return await call.answer("Сервер не найден!", show_alert=True)

    # Сразу показываем ботов этого сервера
    bots_list = server.get('bots', ["bot1", "bot2", "bot3"])
    bot_labels = server.get('bot_labels', {})

    builder = InlineKeyboardBuilder()
    for bot_id in bots_list:
        display_name = bot_labels.get(bot_id, bot_id)
        builder.button(text=f"🤖 {display_name}", callback_data=f"cc_bot_{bot_id}")

    # Кнопка "Назад" вернет в меню этого же сервера
    builder.button(text="⬅️ Назад", callback_data=f"select_server_{server_key}")
    builder.adjust(1)

    await call.message.edit_text(
        f"🖥 <b>{server['name']}</b>\nВыберите бота для очистки кэша:",
        reply_markup=builder.as_markup()
    )


# --- ШАГ 2: Выбрали бота (Спрашиваем номер) ---
@router.callback_query(F.data.startswith("cc_bot_"))
async def clearchat_ask_phone(call: types.CallbackQuery, state: FSMContext):
    bot_id = call.data.replace("cc_bot_", "")
    await state.update_data(bot_id=bot_id)
    await state.set_state(ClearChatState.waiting_for_phone)

    data = await state.get_data()
    server_key = data.get("server_key")

    bot_id = data.get("bot_id")

    servers = load_servers_sync()
    server = servers.get(server_key)

    bot_labels = server.get('bot_labels', {})
    bot_name = bot_labels.get(bot_id, bot_id)

    builder = InlineKeyboardBuilder()

    last_phone = USER_LAST_PHONE.get(call.from_user.id)
    if last_phone:
        builder.button(text=f"♻️ Использовать: {last_phone}", callback_data="cc_repeat_last")
    builder.button(text="❌ Отмена", callback_data=f"select_server_{server_key}")
    builder.adjust(1)

    await call.message.edit_text(
        f"✅ Бот выбран: <b>{bot_name}</b>\n\n"
        f"📱 Отправьте <b>номер телефона</b> (например: <code>996555123456</code>)"
        f"{' или нажмите кнопку ниже:' if last_phone else ':'}",
        reply_markup=builder.as_markup()
    )


async def process_cache_clear(user_id: int, phone: str, state: FSMContext, msg_to_edit: types.Message):
    data = await state.get_data()
    server_key = data.get("server_key")
    bot_id = data.get("bot_id")

    # 1. Сохраняем номер в кэш для этого пользователя на будущее
    USER_LAST_PHONE[user_id] = phone
    await state.clear()

    # 2. Вызываем логику из db.py
    result_text = await clear_cache(server_key, bot_id, phone)

    # 3. Формируем кнопку Назад
    builder = InlineKeyboardBuilder()
    # Возвращаем пользователя к списку ботов этого сервера
    builder.button(text="⬅️ Назад к ботам", callback_data=f"start_clearchat_{server_key}")

    # 4. Показываем результат и кнопку
    await msg_to_edit.edit_text(result_text, reply_markup=builder.as_markup())


# Пользователь ввел номер текстом
@router.message(ClearChatState.waiting_for_phone)
async def clearchat_execute_text(message: types.Message, state: FSMContext):
    if not message.text or not any(char.isdigit() for char in message.text):
        await state.clear()
        return await message.answer("⚠️ Очистка кэша отменена.")

    # Создаем сообщение со статусом, которое потом будет отредактировано
    status_msg = await message.answer("⏳ Очищаю кэш...")

    # Выдаем задачу в общую функцию
    await process_cache_clear(message.from_user.id, message.text, state, status_msg)


# Пользователь нажал кнопку "♻️ Повторить"
@router.callback_query(F.data == "cc_repeat_last", ClearChatState.waiting_for_phone)
async def clearchat_execute_button(call: types.CallbackQuery, state: FSMContext):
    phone = USER_LAST_PHONE.get(call.from_user.id)

    if not phone:
        return await call.answer("Кэш номера пуст, введите вручную", show_alert=True)

    await call.message.edit_text("⏳ Очищаю кэш...")

    # Выдаем задачу в общую функцию, передавая текущее сообщение для редактирования
    await process_cache_clear(call.from_user.id, phone, state, call.message)