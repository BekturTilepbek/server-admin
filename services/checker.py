import asyncio
import json
import logging
from services.store import load_servers_sync
from services.ssh import execute_command

# ИМПОРТИРУЕМ БОТА И НАСТРОЙКИ ДЛЯ ОТПРАВКИ
from loader import bot
from config import GROUP_CHAT_ID

DEFAULT_BOTS = ["bot1", "bot2", "bot3"]

# ГЛОБАЛЬНЫЙ КЭШ: чтобы не спамить в группу каждые 5 минут
QUOTA_ALERTS_SENT = set()


async def check_bot_status(server, bot_name):
    """
    Логика:
    1. Проверяем свежие логи на наличие запроса QR-кода.
    2. Если QR не просит -> ищем номер телефона в истории.
    """

    default_ports = {
        "bot1": 3001,
        "bot2": 3002,
        "bot3": 3003
    }

    bot_ports = server.get('bot_ports', default_ports)

    API_PORT = bot_ports.get(bot_name)

    # КОМАНДА SSH (Делаем все за один заход для скорости):
    # 1. tail -n 50: Берем 50 свежих строк (чтобы проверить состояние)
    # 2. echo ...: Разделитель
    # 3. grep ...: Ищем строчку с номером во всей истории (на случай если бот подключен)
    cmd = (
        f"cd {server['path']} && "
        f"docker compose logs --tail=50 {bot_name} 2>&1 && "
        f"echo '||SEPARATOR||' && "
        f"docker exec {bot_name} node -e \"fetch('http://localhost:{API_PORT}/api/status').then(r=>r.text()).then(console.log).catch(()=>console.log('{{\\\"error\\\": \\\"curl_failed\\\"}}'))\""
    )

    exit_code, stdout, stderr = await asyncio.to_thread(execute_command, server, cmd)
    full_output = stdout + stderr

    # Если команда упала (например, нет такой папки или докера)
    if exit_code != 0:
        return "❌ Ошибка (контейнер выключен?)"

    # Разбираем ответ на две части
    try:
        parts = full_output.split("||SEPARATOR||")
        recent_logs = parts[0]  # Свежие логи (тут ищем QR)
        api_response_text = parts[1].strip() if len(parts) > 1 else "{}"
    except:
        return "❓ Ошибка парсинга"

    # 🚨 НОВАЯ ФУНКЦИЯ: АЛЕРТЫ НА КВОТУ
    alert_key = f"{server['ip']}_{bot_name}"  # Уникальный ID для кэша

    if "insufficient_quota" in recent_logs.lower():
        # Если нашли ошибку и еще не отправляли уведомление
        if alert_key not in QUOTA_ALERTS_SENT:
            server_name = server.get('name', server['ip'])
            bot_labels = server.get('bot_labels', {})
            display_name = bot_labels.get(bot_name, bot_name)

            # Формируем красивое сообщение
            msg = (
                f"⚠️ <b>Системное уведомление: Лимит API</b>\n\n"
                f"Зафиксирована ошибка <code>insufficient_quota</code>.\n"
                f"Закончились деньги на OpenAI!\n"
                f"—\n"
                f"🖥 <b>Сервер:</b> {server_name}\n"
                f"🤖 <b>Бот:</b> {display_name}\n"
                f"—\n"
                f"🚨 <b>Клиенты не получают ответы, необходимо срочно пополнить баланс!</b>"
            )

            try:
                # Отправляем в группу и в конкретный топик
                await bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=msg,
                    # message_thread_id=QUOTA_TOPIC_ID
                )
                # Запоминаем, что уже отправили
                QUOTA_ALERTS_SENT.add(alert_key)
                logging.info(f"Уведомление отправлено в группу для {display_name}")
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление: {e}")
    else:
        # Если ошибки квоты больше нет в свежих логах, удаляем из кэша.
        # Это нужно, чтобы бот смог снова предупредить вас в следующем месяце.
        if alert_key in QUOTA_ALERTS_SENT:
            QUOTA_ALERTS_SENT.remove(alert_key)

    # ==========================================
    # 🟢 ПРОВЕРКА СТАТУСА (ПО API)
    # ==========================================

    if "curl_failed" in api_response_text:
        return f"🔴 Ошибка (API недоступен на порту {API_PORT})"

    try:
        data = json.loads(api_response_text)

        is_ready = data.get("ready", False)
        phone = data.get("phone")

        if is_ready:
            if phone:
                return f"🟢 {phone}"
            else:
                return "🟢 Работает (номер загружается)"
        else:
            return "🔴 Отключен"

    except json.JSONDecodeError:
        return f"❓ Ошибка API (порт {API_PORT})"



async def check_server(server_key, server_data):
    results = []
    bots_list = server_data.get('bots', DEFAULT_BOTS)
    # Получаем словарь имен: {"bot1": "Пицца", ...}
    bot_labels = server_data.get('bot_labels', {})

    for bot_name in bots_list:
        status = await check_bot_status(server_data, bot_name)

        # МАГИЯ: Если есть красивое имя — берем его, если нет — оставляем bot1
        display_name = bot_labels[bot_name] if bot_labels[bot_name] else bot_name

        results.append(f"🤖 <b>{display_name}</b>: {status}")

    header = f"<b>🖥 {server_data['ip']}:</b>"
    return f"{header}\n" + "\n".join(results)


async def check_all_servers():
    """
        Проверяет ВСЕ сервера одновременно.
        """
    servers = load_servers_sync()

    if not servers:
        return "⚠️ Список серверов пуст. Добавьте сервер через меню."

    tasks = []
    for key, data in servers.items():
        tasks.append(check_server(key, data))

    reports = await asyncio.gather(*tasks)
    return "\n\n".join(reports)