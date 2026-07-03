"""
Хранилище серверов/пользователей/кэша.

- servers.json.enc и users.json.enc хранятся ЗАШИФРОВАННЫМИ (Fernet).
- В памяти держим расшифрованную версию с инвалидацией по mtime файла,
  чтобы не читать и не дешифровать диск на каждый хендлер/мидлварь.
- cache.json не шифруется (там только тексты статусов).

Запись на диск — АТОМАРНАЯ: пишем во временный файл, fsync, затем
os.replace() (атомарная замена в рамках одной ФС). Так файл никогда
не останется "недописанным", даже если процесс упадёт посреди записи.

Все операции read-modify-write (save_server/delete_server) выполняются
под блокировкой на файл целиком, чтобы два параллельных редактирования
не затирали друг друга.
"""
import os
import json
import asyncio
from typing import Any

import aiofiles

from services.crypto import encrypt_bytes, decrypt_bytes, InvalidToken

DATA_DIR = "data"
SERVERS_FILE = os.path.join(DATA_DIR, "servers.json.enc")
USERS_FILE = os.path.join(DATA_DIR, "users.json.enc")
CACHE_FILE = os.path.join(DATA_DIR, "cache.json")

os.makedirs(DATA_DIR, exist_ok=True)

# Простой кэш в памяти: {path: (mtime, decoded_obj)}
_mem_cache: dict[str, tuple[float, Any]] = {}
# Блокировки записи на каждый файл, чтобы не словить гонку при параллельных save
_locks: dict[str, asyncio.Lock] = {}


def _lock(path: str) -> asyncio.Lock:
    if path not in _locks:
        _locks[path] = asyncio.Lock()
    return _locks[path]


def _write_bytes_atomic_sync(path: str, data: bytes) -> None:
    """Атомарная запись байтов на диск (синхронно, вызывать через to_thread)."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # атомарно в пределах одной файловой системы


def _load_encrypted(path: str, default: Any) -> Any:
    """Синхронное чтение зашифрованного JSON с кэшем по mtime."""
    if not os.path.exists(path):
        return default
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default

    cached = _mem_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        with open(path, "rb") as f:
            raw = f.read()
        decoded = json.loads(decrypt_bytes(raw).decode("utf-8"))
        _mem_cache[path] = (mtime, decoded)
        return decoded
    except (InvalidToken, ValueError, OSError):
        return default


async def _write_encrypted_nolock(path: str, obj: Any) -> None:
    """Шифрует и атомарно пишет. НЕ берёт блокировку (вызывать под _lock)."""
    payload = json.dumps(obj, indent=4, ensure_ascii=False).encode("utf-8")
    token = encrypt_bytes(payload)
    await asyncio.to_thread(_write_bytes_atomic_sync, path, token)
    # Инвалидируем кэш — при следующем чтении подхватится новый mtime
    _mem_cache.pop(path, None)


async def _save_encrypted(path: str, obj: Any) -> None:
    """Запись целого объекта под блокировкой файла."""
    async with _lock(path):
        await _write_encrypted_nolock(path, obj)


# ---------------- USERS ----------------

def load_users_raw() -> list[dict]:
    return _load_encrypted(USERS_FILE, [])


def get_allowed_ids() -> list[int]:
    return [u["id"] for u in load_users_raw()]


def get_user_name(user_id: int) -> str:
    for u in load_users_raw():
        if u["id"] == user_id:
            return u["name"]
    return "Неизвестный"


async def save_users(users: list[dict]) -> None:
    await _save_encrypted(USERS_FILE, users)


# ---------------- SERVERS ----------------

def load_servers_sync() -> dict:
    return _load_encrypted(SERVERS_FILE, {})


async def save_server(key: str, data: dict) -> dict:
    """Атомарный read-modify-write под блокировкой файла серверов."""
    async with _lock(SERVERS_FILE):
        servers = dict(_load_encrypted(SERVERS_FILE, {}))  # копия, не трогаем кэш
        servers[key] = data
        await _write_encrypted_nolock(SERVERS_FILE, servers)
    return servers


async def delete_server(key: str) -> dict:
    async with _lock(SERVERS_FILE):
        servers = dict(_load_encrypted(SERVERS_FILE, {}))
        servers.pop(key, None)
        await _write_encrypted_nolock(SERVERS_FILE, servers)
    return servers


# ---------------- CACHE (не шифруется) ----------------

async def save_cache(data: dict) -> None:
    async with _lock(CACHE_FILE):
        await asyncio.to_thread(
            _write_bytes_atomic_sync,
            CACHE_FILE,
            json.dumps(data, indent=4, ensure_ascii=False).encode("utf-8"),
        )


async def get_cached_status(server_key: str | None = None):
    """
    Возвращает кэш. Если server_key передан и его нет в кэше — возвращает None
    (чтобы вызывающий код понял: данных нет, нужно сканировать вживую).
    """
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        async with aiofiles.open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.loads(await f.read())
    except (ValueError, OSError):
        return None

    if server_key:
        return cache.get(server_key)  # None, если ключа нет
    return cache