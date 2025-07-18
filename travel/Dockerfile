# Dockerfile
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt ./

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Создаём папку для сессий и аналитики
RUN mkdir -p /app/sessions

# По умолчанию запускаем бота
CMD ["python", "-u", "travel/travel_channel_manager.py"]
