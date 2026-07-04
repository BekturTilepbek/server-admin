"""
Сервис резервного копирования проекта webhook_wb с управляемых серверов.

Структура на сервере (server['path'] указывает на корень проекта, т.е.
саму папку webhook_wb):

    webhook_wb/                  <- server['path']
        central-admin/
        node-bot2/
        node-bot3/
        node-decide/
        sessions/
            bot1/                <- НЕ скачиваем (живая сессия WhatsApp)
            bot2/                <- НЕ скачиваем
            bot3/                <- НЕ скачиваем
        shared/
        static/
        docker-compose.yml
        .env
        ...

Что бэкапируется:
  - Весь server['path'] рекурсивно, КРОМЕ папки sessions/ и её содержимого.
  - Вместо реальной sessions/ в архив добавляется пустая папка sessions/
    (как маркер того, что она существует, без гигабайтов сессий Chromium).

Результат: один zip на сервер в день:
    <IP>_webhook_wb_<YYYY-MM-DD>.zip

Архивы хранятся BACKUP_RETENTION_DAYS дней, затем удаляются автоматически.
Синхронные функции всегда вызываются через asyncio.to_thread(...).
"""
import os
import shutil
import logging
import zipfile
import tempfile
import stat as stat_mod
from datetime import datetime

import paramiko

from config import BACKUP_DIR, BACKUP_RETENTION_DAYS
from services.ssh import CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

# Имя папки сессий относительно server['path'] — не скачиваем содержимое,
# но добавляем как пустую запись в архив.
SESSIONS_SUBDIR = "sessions"


def _open_sftp(server_data: dict) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    """Открывает SSH + SFTP, повторяя логику аутентификации execute_command."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = {
        "hostname": server_data["ip"],
        "username": server_data["user"],
        "timeout": CONNECT_TIMEOUT,
        "banner_timeout": CONNECT_TIMEOUT,
        "auth_timeout": CONNECT_TIMEOUT,
    }

    key_path = server_data.get("key_path")
    if key_path:
        connect_kwargs["key_filename"] = key_path
    else:
        connect_kwargs["password"] = server_data.get("password")
        connect_kwargs["look_for_keys"] = False
        connect_kwargs["allow_agent"] = False

    client.connect(**connect_kwargs)
    sftp = client.open_sftp()
    return client, sftp


def _remote_exists(sftp: paramiko.SFTPClient, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except IOError:
        return False


def _download_tree(
    sftp: paramiko.SFTPClient,
    remote_dir: str,
    local_dir: str,
    skip_dirs: "set[str]",
) -> int:
    """
    Рекурсивно скачивает remote_dir в local_dir, пропуская директории,
    чьё абсолютное имя входит в skip_dirs.
    Возвращает количество скачанных файлов.
    """
    os.makedirs(local_dir, exist_ok=True)
    count = 0
    for entry in sftp.listdir_attr(remote_dir):
        remote_item = f"{remote_dir}/{entry.filename}"
        local_item = os.path.join(local_dir, entry.filename)

        if stat_mod.S_ISDIR(entry.st_mode):
            if remote_item in skip_dirs:
                continue  # пропускаем целиком вместе с содержимым
            count += _download_tree(sftp, remote_item, local_item, skip_dirs)
        else:
            sftp.get(remote_item, local_item)
            count += 1
    return count


def backup_one_server(server_key: str, server_data: dict) -> tuple[bool, str]:
    """
    Синхронно: скачивает весь server['path'] (webhook_wb), кроме sessions/,
    пакует в zip. В архив добавляется пустая папка sessions/.
    Возвращает (успех, сообщение для отчёта).
    ВАЖНО: вызывать через asyncio.to_thread(...).
    """
    ip = server_data.get("ip", server_key)
    remote_root = server_data.get("path", "").rstrip("/")
    if not remote_root:
        return False, "не задан path для сервера"

    sessions_remote = f"{remote_root}/{SESSIONS_SUBDIR}"

    os.makedirs(BACKUP_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_ip = ip.replace(".", "-")
    zip_name = f"{safe_ip}_webhook_wb_{date_str}.zip"
    zip_path = os.path.join(BACKUP_DIR, zip_name)

    client = sftp = tmp_root = None
    try:
        client, sftp = _open_sftp(server_data)

        if not _remote_exists(sftp, remote_root):
            return False, f"папка не найдена: {remote_root}"

        tmp_root = tempfile.mkdtemp(prefix=f"wb_{safe_ip}_")

        # Скачиваем всё, пропуская sessions/ целиком
        files = _download_tree(sftp, remote_root, tmp_root, skip_dirs={sessions_remote})

        # Добавляем пустую папку sessions/ в локальный tmp (попадёт в архив)
        os.makedirs(os.path.join(tmp_root, SESSIONS_SUBDIR), exist_ok=True)

        # Пишем .part, затем атомарно переименовываем
        part_path = zip_path + ".part"
        with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, filenames in os.walk(tmp_root):
                # Пустые папки явно добавляем через ZipInfo
                for d in dirs:
                    full = os.path.join(root, d)
                    arcname = os.path.relpath(full, tmp_root) + "/"
                    if not os.listdir(full):  # добавляем только если реально пустая
                        zf.mkdir(arcname) if hasattr(zf, "mkdir") else zf.writestr(
                            zipfile.ZipInfo(arcname), ""
                        )
                for fn in filenames:
                    full = os.path.join(root, fn)
                    arcname = os.path.relpath(full, tmp_root)
                    zf.write(full, arcname)
        os.replace(part_path, zip_path)

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        return True, f"{zip_name} ({files} файлов, {size_mb:.1f} МБ)"

    except Exception as e:  # noqa: BLE001
        logger.warning("Бэкап webhook_wb для %s не удался: %s", ip, e)
        # Чистим недописанный архив, если он есть
        for p in (zip_path + ".part", zip_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return False, f"ошибка: {e}"
    finally:
        for obj in (sftp, client):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if tmp_root and os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root, ignore_errors=True)


def cleanup_old_backups() -> int:
    """Удаляет архивы старше BACKUP_RETENTION_DAYS дней. Возвращает кол-во удалённых."""
    if BACKUP_RETENTION_DAYS <= 0 or not os.path.isdir(BACKUP_DIR):
        return 0

    cutoff = datetime.now().timestamp() - BACKUP_RETENTION_DAYS * 86400
    removed = 0
    for fn in os.listdir(BACKUP_DIR):
        if not fn.endswith(".zip"):
            continue
        full = os.path.join(BACKUP_DIR, fn)
        try:
            if os.path.getmtime(full) < cutoff:
                os.remove(full)
                removed += 1
        except OSError:
            continue
    return removed