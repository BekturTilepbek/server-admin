"""
Небольшой хелпер для единообразного форматирования "кто выполнил действие"
в логах. Используется service-функциями, которые принимают actor и логируют
бизнес-события (в отличие от AuthMiddleware, который логирует сырой факт
нажатия кнопки/сообщения для ЛЮБОГО апдейта).
"""


def actor_label(user_id: int, full_name: str | None = None) -> str:
    name = (full_name or "").strip()
    return f"{name} (ID:{user_id})" if name else f"ID:{user_id}"