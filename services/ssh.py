"""
SSH-исполнитель команд.

Поддерживает два способа аутентификации (приоритет — ключ):
  1. SSH-ключ: server_data['key_path'] -> путь к приватному ключу.
  2. Пароль:   server_data['password'] (хранится зашифрованным в store,
               расшифровывается вызывающим кодом ДО передачи сюда).

Функция синхронная — всегда вызывать через asyncio.to_thread(...).
"""
import logging
import paramiko

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
EXEC_TIMEOUT = 60  # макс. время выполнения команды, чтобы не зависнуть навечно


def execute_command(
    server_data: dict, command: str, exec_timeout: int = EXEC_TIMEOUT
) -> tuple[int, str, str]:
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

    try:
        client.connect(**connect_kwargs)
        stdin, stdout, stderr = client.exec_command(command, timeout=exec_timeout)
        # settimeout на канал — защита от зависшего read()
        stdout.channel.settimeout(exec_timeout)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        return exit_status, out, err
    except Exception as e:
        logger.warning("SSH error on %s: %s", server_data.get("ip"), e)
        return -1, "", str(e)
    finally:
        client.close()