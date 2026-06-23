@echo off
chcp 65001 >nul
echo 📦 Упаковка файлов в архив (без серверов)...
tar.exe -czvf deploy.tar.gz --exclude="data/servers.json" --exclude="data/users.json" handlers keyboards services data middlewares utils main.py loader.py config.py Dockerfile docker-compose.yml requirements.txt

echo.
echo 🚀 Отправка архива на сервер 85.192.24.139...
scp deploy.tar.gz root@85.192.24.139:/root/servers_admin_bot/

echo.
echo ⚙️ Распаковка и перезапуск Docker на сервере...
ssh root@85.192.24.139 "cd /root/servers_admin_bot && tar -xzvf deploy.tar.gz && rm deploy.tar.gz && docker compose up -d --build"

echo.
echo ✅ Деплой успешно завершен!
del deploy.tar.gz
pause