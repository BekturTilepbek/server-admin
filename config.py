"""
Все секреты загружаются из окружения (.env). В репозиторий этот файл
можно коммитить безопасно — реальных значений тут нет.
"""
import os
from pathlib import Path

# Подхватываем .env, если установлен python-dotenv (в проде можно прокидывать
# переменные через docker compose env_file и без этой зависимости).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Переменная окружения {name} не задана (.env)")
    return val


# --- ОСНОВНЫЕ НАСТРОЙКИ ---
BOT_TOKEN: str = _required("BOT_TOKEN")
ADMIN_ID: int = int(_required("ADMIN_ID"))

# --- УВЕДОМЛЕНИЯ ---
GROUP_CHAT_ID: int = int(_required("GROUP_CHAT_ID"))
# QUOTA_TOPIC_ID = int(os.environ.get("QUOTA_TOPIC_ID", 0)) or None

# MASTER_KEY читается напрямую в services/crypto.py — здесь только проверяем
# наличие, чтобы упасть на старте с понятной ошибкой, а не в рантайме.
_required("MASTER_KEY")
