#!/usr/bin/env python3
"""
Однократная миграция: шифрует data/servers.json и data/users.json
в data/servers.json.enc и data/users.json.enc.

Запуск (MASTER_KEY должен быть в окружении или .env):
    python -m scripts.encrypt_data

Если MASTER_KEY ещё нет — сначала сгенерируйте и положите в .env:
    python -c "from services.crypto import generate_key; print(generate_key())"

После успешной миграции УДАЛИТЕ исходные открытые .json вручную, убедившись,
что бот стартует и видит сервера.
"""
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from services.crypto import encrypt_bytes  # noqa: E402

PAIRS = [
    ("data/servers.json", "data/servers.json.enc"),
    ("data/users.json", "data/users.json.enc"),
]


def main():
    if not os.environ.get("MASTER_KEY"):
        print("❌ MASTER_KEY не задан. Добавьте его в .env и повторите.")
        sys.exit(1)

    for src, dst in PAIRS:
        if not os.path.exists(src):
            print(f"⚠️  Пропуск: {src} не найден.")
            continue
        with open(src, "r", encoding="utf-8") as f:
            obj = json.load(f)  # валидируем, что это корректный JSON
        payload = json.dumps(obj, indent=4, ensure_ascii=False).encode("utf-8")
        with open(dst, "wb") as f:
            f.write(encrypt_bytes(payload))
        print(f"✅ {src} -> {dst}")

    print("\nГотово. Проверьте запуск бота, затем удалите открытые .json:")
    print("   rm data/servers.json data/users.json")


if __name__ == "__main__":
    main()
