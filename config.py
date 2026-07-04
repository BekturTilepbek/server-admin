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


# --- БИЛЛИНГ DIGITALOCEAN ---
# Формат: "почта1:токен1,почта2:токен2,почта3:токен3"
# Почта (метка) и токен разделяются ПЕРВЫМ двоеточием, аккаунты — запятой.
# В email есть '@', но нет ':' и ',', поэтому парсинг однозначный.
# Переменная необязательная — если её нет, фича биллинга просто молчит.
def _parse_do_accounts(raw: str) -> list[tuple[str, str]]:
    accounts: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        label, sep, token = chunk.partition(":")
        label, token = label.strip(), token.strip()
        if not sep or not token:
            raise RuntimeError(
                f"Некорректная запись в DO_ACCOUNTS: '{chunk}'. "
                f"Ожидается формат 'почта:токен'."
            )
        accounts.append((label, token))
    return accounts


DO_ACCOUNTS: list[tuple[str, str]] = _parse_do_accounts(
    os.environ.get("DO_ACCOUNTS", "")
)

# --- РЕЗЕРВНОЕ КОПИРОВАНИЕ webhook_wb ---
# Куда складывать архивы внутри контейнера (том ./backups:/app/backups в docker-compose.yml).
BACKUP_DIR: str = "/app/backups"

# Час запуска бэкапа по Бишкеку (GMT+6), 0-23.
BACKUP_HOUR: int = 3

# Сколько дней хранить архивы (0 = вечно).
BACKUP_RETENTION_DAYS: int = 7