from aiogram import Router

# Импортируем роутеры из наших файлов
from .menu import router as menu_router
from .actions import router as actions_router
from .admin import router as admin_router
from .manage import router as manage_router  # <-- CRUD серверов/юзеров

# Создаем главный роутер пакета
main_router = Router()

# Собираем их всех вместе.
# admin и manage (специфичные admin-only колбэки) ставим выше actions.
main_router.include_routers(
    menu_router,
    admin_router,
    manage_router,
    actions_router,
)