import json
import os
import aiofiles # pip install aiofiles (для асинхронной записи)

SERVERS_FILE = 'data/servers.json'
CACHE_FILE = 'data/cache.json'
USERS_FILE = 'data/users.json'

# Убедимся, что папка data существует
if not os.path.exists('data'):
    os.makedirs('data')

def load_users_raw():
    """Читает файл users.json как есть"""
    if not os.path.exists(USERS_FILE):
        return []
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def get_allowed_ids():
    """Возвращает просто список ID для быстрой проверки доступа"""
    users = load_users_raw()
    # Собираем все id в кучу
    return [u['id'] for u in users]

def get_user_name(user_id):
    """Ищет имя юзера по ID"""
    users = load_users_raw()
    for u in users:
        if u['id'] == user_id:
            return u['name']
    return "Неизвестный"

def load_servers_sync():
    """Синхронная загрузка для старта бота"""
    if not os.path.exists(SERVERS_FILE): return {}
    try:
        with open(SERVERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

async def save_cache(data):
    """Сохраняет результаты проверки"""
    async with aiofiles.open(CACHE_FILE, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(data, indent=4, ensure_ascii=False))

async def get_cached_status(server_key=None):
    """Читает кэш. Если server_key передан - возвращает статус конкретного сервера"""
    if not os.path.exists(CACHE_FILE): return None
    try:
        async with aiofiles.open(CACHE_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            cache = json.loads(content)
            if server_key:
                return cache.get(server_key, "⏳ Данных пока нет")
            return cache
    except: return None

async def save_server(key, data):
    """Добавляет новый сервер"""
    servers = load_servers_sync()
    servers[key] = data
    async with aiofiles.open(SERVERS_FILE, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(servers, indent=4, ensure_ascii=False))
    return servers