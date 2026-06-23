from aiogram import Router

# Импортируем роутеры из наших файлов
from .menu import router as menu_router
from .actions import router as actions_router
from .admin import router as admin_router  # <-- Добавили новый файл

# Создаем главный роутер пакета
main_router = Router()

# Собираем их всех вместе
main_router.include_routers(
    menu_router,
    admin_router,   # Важно: порядок иногда имеет значение (admin лучше ставить выше)
    actions_router,
)