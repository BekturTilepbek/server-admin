# 1. Берем за основу легкий Python 3.11
FROM python:3.11-slim

# 2. Рабочая папка внутри сервера будет /app
WORKDIR /app

# 3. Копируем список библиотек внутрь
COPY requirements.txt .

# 4. Устанавливаем библиотеки
RUN pip install --no-cache-dir -r requirements.txt

# 5. Копируем весь остальной код бота внутрь
COPY . .

# 6. Команда для запуска
CMD ["python", "main.py"]