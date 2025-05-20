#!/bin/bash

# Простой установщик для NullifierCore
echo "Установка зависимостей..."
pip install python-telegram-bot==20.3 telethon==1.30.3 aiohttp==3.8.5 python-dotenv==1.0.0

echo "Создание папки для данных..."
mkdir -p data

echo "Установка завершена. Запустите бота командой: python run.py"