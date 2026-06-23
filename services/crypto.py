"""
Шифрование секретов "at rest" через Fernet (симметричное, AES-128 CBC + HMAC).
Мастер-ключ берётся ТОЛЬКО из переменной окружения MASTER_KEY (.env) и
никогда не пишется на диск.
"""
import os
from functools import lru_cache
from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "encrypt_bytes",
    "decrypt_bytes",
    "encrypt_str",
    "decrypt_str",
    "generate_key",
    "InvalidToken",
]


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ.get("MASTER_KEY")
    if not key:
        raise RuntimeError(
            "MASTER_KEY не задан в окружении. Добавьте его в .env "
            "(сгенерировать: python -c \"from services.crypto import generate_key; print(generate_key())\")"
        )
    return Fernet(key.encode())


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet().decrypt(token)


def encrypt_str(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("utf-8")


def decrypt_str(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def generate_key() -> str:
    """Однократно: сгенерировать новый мастер-ключ для .env."""
    return Fernet.generate_key().decode("utf-8")
