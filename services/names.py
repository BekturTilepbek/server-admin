"""
Резолвер отображаемых имён ботов.

Приоритет: имя, полученное с endpoint /api/bot-name (кэшируется сканером)
            -> bot_labels из servers.json (/setname)
            -> технический bot_id.

Кэш живёт в памяти процесса и наполняется фоновым сканером (checker.py),
поэтому клавиатуры/хендлеры читают имена синхронно, без SSH.
"""
import logging

logger = logging.getLogger(__name__)

# {f"{server_key}:{bot_id}": "Имя"}
_NAME_CACHE: dict[str, str] = {}


def _key(server_key: str, bot_id: str) -> str:
    return f"{server_key}:{bot_id}"


def cache_bot_name(server_key: str, bot_id: str, name: str | None) -> None:
    """Кладёт имя в кэш только если оно непустое (вызывается из сканера)."""
    if name and name.strip():
        _NAME_CACHE[_key(server_key, bot_id)] = name.strip()


def get_bot_name(server_key: str, server: dict, bot_id: str) -> str:
    """Синхронный резолвер для клавиатур и хендлеров."""
    cached = _NAME_CACHE.get(_key(server_key, bot_id))
    if cached:
        return cached
    labels = server.get("bot_labels", {})
    return labels.get(bot_id) or bot_id
