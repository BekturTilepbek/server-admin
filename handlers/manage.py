"""
Раздел «Управление»: добавление/редактирование/удаление серверов и
пользователей прямо из чата с ботом (без захода на сервер по SSH).

Строго admin-only. Хендлеры отвечают только за интерфейс и FSM; вся
логика/валидация/сохранение — в services/management.py.
"""
import logging

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_ID
from loader import bot
from services.store import load_servers_sync, load_users_raw
from services.audit import actor_label
from services.management import (
    create_server,
    update_server_field,
    remove_server,
    check_ssh_connection,
    create_user,
    update_user_name,
    remove_user,
    validate_ip,
    parse_bots,
    EDITABLE_SERVER_FIELDS,
    DEFAULT_USER,
    DEFAULT_PATH,
    DEFAULT_BOTS,
)
from keyboards.manage import (
    manage_root_kb,
    manage_servers_kb,
    server_card_kb,
    manage_users_kb,
    user_card_kb,
    confirm_kb,
    cancel_kb,
)

logger = logging.getLogger(__name__)
router = Router()


# ---------------- FSM ----------------

class AddServer(StatesGroup):
    name = State()
    ip = State()
    user = State()
    password = State()
    path = State()
    bots = State()


class EditServer(StatesGroup):
    value = State()


class AddUser(StatesGroup):
    user_id = State()
    name = State()


class EditUser(StatesGroup):
    name = State()


# ---------------- Хелперы ----------------

async def _guard(call: types.CallbackQuery) -> bool:
    """True — доступ разрешён; иначе показывает алерт и возвращает False."""
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔️ Только для администратора.", show_alert=True)
        return False
    return True


def _actor(user: types.User) -> str:
    return actor_label(user.id, user.full_name)


def _server_card_text(server_key: str, server: dict) -> str:
    bots = ", ".join(server.get("bots", DEFAULT_BOTS))
    return (
        f"🖥 <b>{server.get('name', server_key)}</b>\n"
        f"🌐 IP: <code>{server.get('ip', server_key)}</code>\n"
        f"👤 SSH-user: <code>{server.get('user', DEFAULT_USER)}</code>\n"
        f"📁 Путь: <code>{server.get('path', DEFAULT_PATH)}</code>\n"
        f"🤖 Боты: {bots}\n\n"
        f"👇 Что редактируем?"
    )


async def _try_delete(message: types.Message) -> None:
    """Пытается удалить сообщение (например, с паролем). Ошибки глушим."""
    try:
        await message.delete()
    except Exception:
        pass


# ---------------- Корень раздела ----------------

@router.callback_query(F.data == "mng_root")
async def manage_root(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    await state.clear()
    await call.message.edit_text(
        "⚙️ <b>Управление</b>\nВыберите раздел:",
        reply_markup=manage_root_kb(),
    )


@router.callback_query(F.data == "mng_cancel")
async def manage_cancel(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    await state.clear()
    await call.message.edit_text(
        "⚙️ <b>Управление</b>\nДействие отменено. Выберите раздел:",
        reply_markup=manage_root_kb(),
    )


# ================= СЕРВЕРА =================

@router.callback_query(F.data == "mng_srv")
async def srv_list(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    await state.clear()
    servers = load_servers_sync()
    logger.info("⚙️ %s: открыл список серверов (управление)", _actor(call.from_user))
    text = "🖥 <b>Сервера</b> ({}):\nВыберите сервер или добавьте новый.".format(len(servers))
    await call.message.edit_text(text, reply_markup=manage_servers_kb(servers, page=0))


@router.callback_query(F.data.startswith("mng_srv_pg_"))
async def srv_list_page(call: types.CallbackQuery):
    if not await _guard(call):
        return
    try:
        page = int(call.data.rsplit("_", 1)[1])
    except ValueError:
        page = 0
    servers = load_servers_sync()
    await call.message.edit_text(
        "🖥 <b>Сервера</b> ({}):".format(len(servers)),
        reply_markup=manage_servers_kb(servers, page=page),
    )


@router.callback_query(F.data.startswith("mng_srv_pick_"))
async def srv_card(call: types.CallbackQuery):
    if not await _guard(call):
        return
    server_key = call.data.split("_", 3)[3]
    server = load_servers_sync().get(server_key)
    if not server:
        return await call.answer("Сервер не найден", show_alert=True)
    await call.message.edit_text(
        _server_card_text(server_key, server),
        reply_markup=server_card_kb(server_key),
    )


# ---- Добавление сервера (FSM) ----

@router.callback_query(F.data == "mng_srv_add")
async def srv_add_start(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    logger.info("➕ %s: начал добавление нового сервера", _actor(call.from_user))
    await state.clear()
    await state.set_state(AddServer.name)
    await call.message.edit_text(
        "➕ <b>Новый сервер</b>\n\nШаг 1/6. Введите <b>имя</b> сервера "
        "(например: <code>Пиццерия-1</code>):",
        reply_markup=cancel_kb(),
    )


@router.message(AddServer.name)
async def srv_add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddServer.ip)
    await message.answer(
        "Шаг 2/6. Введите <b>IP-адрес</b> (например: <code>137.184.14.83</code>):",
        reply_markup=cancel_kb(),
    )


@router.message(AddServer.ip)
async def srv_add_ip(message: types.Message, state: FSMContext):
    ip = validate_ip(message.text)
    if not ip:
        return await message.answer("❌ Некорректный IP. Попробуйте ещё раз:", reply_markup=cancel_kb())
    if ip in load_servers_sync():
        return await message.answer(
            f"❌ Сервер с IP <code>{ip}</code> уже есть. Введите другой:",
            reply_markup=cancel_kb(),
        )
    await state.update_data(ip=ip)
    await state.set_state(AddServer.user)
    await message.answer(
        f"Шаг 3/6. Введите <b>SSH-пользователя</b> "
        f"(или <code>-</code> для <code>{DEFAULT_USER}</code>):",
        reply_markup=cancel_kb(),
    )


@router.message(AddServer.user)
async def srv_add_user(message: types.Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(user=DEFAULT_USER if val == "-" else val)
    await state.set_state(AddServer.password)
    await message.answer(
        "Шаг 4/6. Введите <b>SSH-пароль</b>.\n"
        "🔒 <i>Сообщение с паролем я удалю сразу после получения.</i>",
        reply_markup=cancel_kb(),
    )


@router.message(AddServer.password)
async def srv_add_password(message: types.Message, state: FSMContext):
    await state.update_data(password=message.text)
    await _try_delete(message)  # чтобы пароль не висел в истории чата
    await state.set_state(AddServer.path)
    await message.answer(
        f"🔑 Пароль принят (сообщение удалено).\n\n"
        f"Шаг 5/6. Введите <b>путь к проекту</b> "
        f"(или <code>-</code> для <code>{DEFAULT_PATH}</code>):",
        reply_markup=cancel_kb(),
    )


@router.message(AddServer.path)
async def srv_add_path(message: types.Message, state: FSMContext):
    val = message.text.strip()
    await state.update_data(path=DEFAULT_PATH if val == "-" else val)
    await state.set_state(AddServer.bots)
    await message.answer(
        "Шаг 6/6. Введите <b>список ботов</b> через запятую "
        f"(или <code>-</code> для <code>{', '.join(DEFAULT_BOTS)}</code>):",
        reply_markup=cancel_kb(),
    )


@router.message(AddServer.bots)
async def srv_add_bots(message: types.Message, state: FSMContext):
    val = message.text.strip()
    bots = list(DEFAULT_BOTS) if val == "-" else parse_bots(val)
    data = await state.get_data()
    data["bots"] = bots
    await state.clear()

    ok, text = await create_server(data, actor=_actor(message.from_user))
    servers = load_servers_sync()

    if ok:
        # Сервер создан — сразу показываем карточку, чтобы можно было
        # в один клик проверить SSH-подключение.
        new_key = data.get("ip")
        server = servers.get(new_key)
        if server:
            await message.answer(
                text + "\n\n👇 Можете сразу проверить SSH-подключение:",
                reply_markup=server_card_kb(new_key),
            )
            return

    await message.answer(text, reply_markup=manage_servers_kb(servers, page=0))


# ---- Редактирование поля сервера ----

@router.callback_query(F.data.startswith("mng_srv_edit_"))
async def srv_edit_start(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    # mng_srv_edit_{field}_{ip}
    _, _, _, field, server_key = call.data.split("_", 4)
    server = load_servers_sync().get(server_key)
    if not server:
        return await call.answer("Сервер не найден", show_alert=True)

    await state.update_data(server_key=server_key, field=field)
    await state.set_state(EditServer.value)

    label = EDITABLE_SERVER_FIELDS.get(field, field)
    if field == "password":
        current = "••••••••"
        hint = "\n🔒 <i>Сообщение с новым паролем я удалю.</i>"
    elif field == "bots":
        current = ", ".join(server.get("bots", DEFAULT_BOTS))
        hint = "\n<i>Список через запятую.</i>"
    else:
        current = str(server.get(field, "—"))
        hint = ""

    await call.message.edit_text(
        f"✏️ Редактирование поля «<b>{label}</b>»\n"
        f"Текущее значение: <code>{current}</code>\n\n"
        f"Введите новое значение:{hint}",
        reply_markup=cancel_kb(),
    )


@router.message(EditServer.value)
async def srv_edit_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    server_key = data.get("server_key")
    field = data.get("field")
    value = message.text
    await state.clear()

    if field == "password":
        await _try_delete(message)

    ok, text = await update_server_field(server_key, field, value, actor=_actor(message.from_user))

    # Ключ мог смениться (если меняли IP) — берём актуальный
    new_key = value.strip() if (ok and field == "ip") else server_key
    server = load_servers_sync().get(new_key)
    if server:
        await message.answer(text, reply_markup=server_card_kb(new_key))
    else:
        await message.answer(text, reply_markup=manage_servers_kb(load_servers_sync()))


# ---- Проверка SSH-подключения ----

@router.callback_query(F.data.startswith("mng_srv_checkssh_"))
async def srv_check_ssh(call: types.CallbackQuery):
    if not await _guard(call):
        return
    server_key = call.data.split("_", 3)[3]
    server = load_servers_sync().get(server_key)
    if not server:
        return await call.answer("Сервер не найден", show_alert=True)

    await call.message.edit_text(
        f"⏳ Проверяю SSH-подключение к <b>{server.get('name', server_key)}</b>...",
        reply_markup=None,
    )
    ok, text = await check_ssh_connection(server_key, actor=_actor(call.from_user))
    await call.message.edit_text(text, reply_markup=server_card_kb(server_key))


# ---- Удаление сервера ----

@router.callback_query(F.data.startswith("mng_srv_askdel_"))
async def srv_del_ask(call: types.CallbackQuery):
    if not await _guard(call):
        return
    server_key = call.data.split("_", 3)[3]
    server = load_servers_sync().get(server_key)
    if not server:
        return await call.answer("Сервер не найден", show_alert=True)
    logger.info("🗑 %s: запросил удаление сервера «%s» (ожидает подтверждения)", _actor(call.from_user), server.get("name", server_key))
    await call.message.edit_text(
        f"🗑 Удалить сервер <b>{server.get('name', server_key)}</b> "
        f"(<code>{server_key}</code>)?\n\n<i>Действие необратимо.</i>",
        reply_markup=confirm_kb(f"mng_srv_delok_{server_key}", f"mng_srv_pick_{server_key}"),
    )


@router.callback_query(F.data.startswith("mng_srv_delok_"))
async def srv_del_ok(call: types.CallbackQuery):
    if not await _guard(call):
        return
    server_key = call.data.split("_", 3)[3]
    ok, text = await remove_server(server_key, actor=_actor(call.from_user))
    await call.message.edit_text(text, reply_markup=manage_servers_kb(load_servers_sync()))


# ================= ПОЛЬЗОВАТЕЛИ =================

@router.callback_query(F.data == "mng_usr")
async def usr_list(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    await state.clear()
    users = load_users_raw()
    logger.info("⚙️ %s: открыл список пользователей (управление)", _actor(call.from_user))
    await call.message.edit_text(
        f"👥 <b>Пользователи</b> ({len(users)}):\nВыберите или добавьте нового.",
        reply_markup=manage_users_kb(users),
    )


@router.callback_query(F.data.startswith("mng_usr_pick_"))
async def usr_card(call: types.CallbackQuery):
    if not await _guard(call):
        return
    uid = int(call.data.split("_", 3)[3])
    user = next((u for u in load_users_raw() if u["id"] == uid), None)
    if not user:
        return await call.answer("Пользователь не найден", show_alert=True)
    await call.message.edit_text(
        f"👤 <b>{user['name']}</b>\n🆔 <code>{uid}</code>\n\n👇 Действие:",
        reply_markup=user_card_kb(uid),
    )


# ---- Добавление пользователя ----

@router.callback_query(F.data == "mng_usr_add")
async def usr_add_start(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    await state.clear()
    await state.set_state(AddUser.user_id)
    await call.message.edit_text(
        "➕ <b>Новый пользователь</b>\n\nШаг 1/2. Введите <b>Telegram ID</b> "
        "(только цифры):",
        reply_markup=cancel_kb(),
    )


@router.message(AddUser.user_id)
async def usr_add_id(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit():
        return await message.answer("❌ ID должен быть числом. Попробуйте ещё раз:", reply_markup=cancel_kb())
    uid = int(raw)
    if any(u["id"] == uid for u in load_users_raw()):
        return await message.answer(
            f"❌ Пользователь <code>{uid}</code> уже существует. Введите другой ID:",
            reply_markup=cancel_kb(),
        )
    await state.update_data(user_id=uid)
    await state.set_state(AddUser.name)
    await message.answer(
        "Шаг 2/2. Введите <b>имя</b> пользователя:",
        reply_markup=cancel_kb(),
    )


@router.message(AddUser.name)
async def usr_add_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("user_id")
    await state.clear()
    ok, text = await create_user(uid, message.text, actor=_actor(message.from_user))
    await message.answer(text, reply_markup=manage_users_kb(load_users_raw()))


# ---- Изменение имени ----

@router.callback_query(F.data.startswith("mng_usr_editname_"))
async def usr_edit_start(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    uid = int(call.data.split("_", 3)[3])
    user = next((u for u in load_users_raw() if u["id"] == uid), None)
    if not user:
        return await call.answer("Пользователь не найден", show_alert=True)
    await state.update_data(user_id=uid)
    await state.set_state(EditUser.name)
    await call.message.edit_text(
        f"✏️ Текущее имя: <b>{user['name']}</b>\nВведите новое имя:",
        reply_markup=cancel_kb(),
    )


@router.message(EditUser.name)
async def usr_edit_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("user_id")
    await state.clear()
    ok, text = await update_user_name(uid, message.text, actor=_actor(message.from_user))
    user = next((u for u in load_users_raw() if u["id"] == uid), None)
    if user:
        await message.answer(text, reply_markup=user_card_kb(uid))
    else:
        await message.answer(text, reply_markup=manage_users_kb(load_users_raw()))


# ---- Удаление пользователя ----

@router.callback_query(F.data.startswith("mng_usr_askdel_"))
async def usr_del_ask(call: types.CallbackQuery):
    if not await _guard(call):
        return
    uid = int(call.data.split("_", 3)[3])
    user = next((u for u in load_users_raw() if u["id"] == uid), None)
    if not user:
        return await call.answer("Пользователь не найден", show_alert=True)
    logger.info("🗑 %s: запросил удаление пользователя «%s» (ожидает подтверждения)", _actor(call.from_user), user["name"])
    await call.message.edit_text(
        f"🗑 Удалить пользователя <b>{user['name']}</b> (<code>{uid}</code>)?",
        reply_markup=confirm_kb(f"mng_usr_delok_{uid}", f"mng_usr_pick_{uid}"),
    )


@router.callback_query(F.data.startswith("mng_usr_delok_"))
async def usr_del_ok(call: types.CallbackQuery):
    if not await _guard(call):
        return
    uid = int(call.data.split("_", 3)[3])
    ok, text = await remove_user(uid, actor=_actor(call.from_user))
    await call.message.edit_text(text, reply_markup=manage_users_kb(load_users_raw()))