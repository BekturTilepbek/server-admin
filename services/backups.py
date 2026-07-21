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

Что бэкапируется — ОДИН zip на сервер в день:

    <IP>_backup_<YYYY-MM-DD>.zip
        webhook_wb/              <- весь проект, кроме sessions/*
            sessions/            <- пустая (без живых сессий ботов)
            ...
        moidb_<YYYY-MM-DD>.dump  <- дамп PostgreSQL (pg_restore -F c)

Архив хранится BACKUP_RETENTION_DAYS дней, затем удаляется автоматически.
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

from config import BACKUP_DIR, BACKUP_RETENTION_DAYS, DB_CONTAINER, DB_USER, DB_NAME
from services.ssh import CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

# Имя папки сессий относительно server['path'] — не скачиваем содержимое,
# но добавляем как пустую запись в архив.
SESSIONS_SUBDIR = "sessions"

# Имя папки внутри zip, куда кладём весь проект (соответствует названию
# папки на сервере — так привычнее ориентироваться после распаковки).
ARC_PROJECT_DIR = "webhook_wb"


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


def _exec_on_client(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    """
    Выполняет команду на УЖЕ открытом SSH-клиенте (без нового подключения) —
    используется для pg_dump/docker cp/cleanup рядом со скачиванием файлов
    через тот же клиент, чтобы не плодить лишние SSH-соединения.
    """
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    stdout.channel.settimeout(timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    return exit_status, out, err


def _dump_database_into(
    client: paramiko.SSHClient,
    sftp: paramiko.SFTPClient,
    local_dump_path: str,
    safe_ip: str,
    date_str: str,
) -> tuple[bool, str]:
    """
    Делает pg_dump БД внутри контейнера DB_CONTAINER, копирует дамп из
    контейнера на хост сервера (docker cp), скачивает его локально в
    local_dump_path через уже открытый sftp, затем чистит временный файл
    на сервере (и в контейнере, и на хосте). Возвращает (успех, сообщение).
    """
    remote_tmp = f"/tmp/{DB_NAME}_{safe_ip}_{date_str}.dump"

    dump_cmd = (
        f"docker exec {DB_CONTAINER} pg_dump -U {DB_USER} -d {DB_NAME} -F c -f {remote_tmp} && "
        f"docker cp {DB_CONTAINER}:{remote_tmp} {remote_tmp} && "
        f"docker exec {DB_CONTAINER} rm -f {remote_tmp}"
    )

    status, out, err = _exec_on_client(client, dump_cmd)
    if status != 0:
        return False, f"pg_dump не удался: {err.strip() or out.strip()}"

    try:
        sftp.get(remote_tmp, local_dump_path)
    finally:
        # Чистим временный файл на хосте сервера в любом случае
        _exec_on_client(client, f"rm -f {remote_tmp}")

    size_mb = os.path.getsize(local_dump_path) / (1024 * 1024)
    return True, f"{os.path.basename(local_dump_path)} ({size_mb:.1f} МБ)"


def backup_one_server(server_key: str, server_data: dict) -> tuple[bool, str]:
    """
    Синхронно: скачивает весь server['path'] (webhook_wb, кроме sessions/)
    и дамп БД, пакует ОБА в ОДИН zip:

        <IP>_backup_<дата>.zip
            webhook_wb/...
            moidb_<дата>.dump

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
    zip_name = f"{safe_ip}_backup_{date_str}.zip"
    zip_path = os.path.join(BACKUP_DIR, zip_name)

    client = sftp = tmp_root = None
    try:
        client, sftp = _open_sftp(server_data)

        if not _remote_exists(sftp, remote_root):
            return False, f"папка не найдена: {remote_root}"

        tmp_root = tempfile.mkdtemp(prefix=f"bk_{safe_ip}_")
        project_local = os.path.join(tmp_root, ARC_PROJECT_DIR)

        # 1. Скачиваем весь проект, пропуская sessions/ целиком
        files = _download_tree(sftp, remote_root, project_local, skip_dirs={sessions_remote})

        # Добавляем пустую папку sessions/ внутри webhook_wb/ (попадёт в архив)
        os.makedirs(os.path.join(project_local, SESSIONS_SUBDIR), exist_ok=True)

        # 2. Дамп БД — файлом рядом с webhook_wb/ внутри того же tmp_root,
        # чтобы попасть в тот же итоговый zip.
        dump_filename = f"{DB_NAME}_{date_str}.dump"
        dump_local_path = os.path.join(tmp_root, dump_filename)
        try:
            db_ok, db_msg = _dump_database_into(client, sftp, dump_local_path, safe_ip, date_str)
        except Exception as e:  # noqa: BLE001 — не роняем файловый бэкап из-за проблем с БД
            logger.warning("Дамп БД для %s не удался: %s", ip, e)
            db_ok, db_msg = False, f"ошибка: {e}"

        # 3. Пакуем всё (webhook_wb/ + дамп, если он получился) в один zip
        part_path = zip_path + ".part"
        with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, filenames in os.walk(tmp_root):
                for d in dirs:
                    full = os.path.join(root, d)
                    if not os.listdir(full):  # пустые папки добавляем явной записью
                        arcname = os.path.relpath(full, tmp_root) + "/"
                        zf.mkdir(arcname) if hasattr(zf, "mkdir") else zf.writestr(
                            zipfile.ZipInfo(arcname), ""
                        )
                for fn in filenames:
                    full = os.path.join(root, fn)
                    arcname = os.path.relpath(full, tmp_root)
                    zf.write(full, arcname)
        os.replace(part_path, zip_path)

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        icon = "✅" if db_ok else "❌"
        combined = (
            f"{zip_name} ({files} файлов проекта, {size_mb:.1f} МБ)\n"
            f"   🗄 БД: {icon} {db_msg}"
        )
        return True, combined

    except Exception as e:  # noqa: BLE001 — не роняем весь батч из-за одного сервера
        logger.warning("Бэкап для %s не удался: %s", ip, e)
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
    """Удаляет .zip архивы старше BACKUP_RETENTION_DAYS дней. Возвращает кол-во удалённых."""
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