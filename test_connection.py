#!/usr/bin/env python3
"""
Тестовый скрипт для проверки подключения к MTProto API Telegram
"""
import os
import asyncio
import logging
from telethon import TelegramClient

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger.info("dotenv не найден, продолжаем без него (используем переменные окружения)")

# Настройки для API
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

# Проверка наличия всех необходимых переменных
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError('Отсутствуют необходимые настройки! Убедитесь, что вы добавили API_ID, API_HASH и TELEGRAM_BOT_TOKEN.')

async def check_connection():
    """Проверка подключения к Telegram MTProto API"""
    logger.info("Проверка подключения к Telegram API...")

    # Инициализация клиента MTProto
    client = TelegramClient('test_session', API_ID, API_HASH)

    try:
        # Запуск клиента
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Успешно подключено к Telegram API!")

        # Получение информации о боте
        me = await client.get_me()
        logger.info(f"Информация о боте: {me.first_name} (@{me.username})")

        # Сохранение сессии
        logger.info("Сессия сохранена")

        return True
    except Exception as e:
        logger.error(f"Ошибка при подключении: {e}")
        return False
    finally:
        # Отключение клиента
        await client.disconnect()

if __name__ == "__main__":
    try:
        result = asyncio.run(check_connection())
        if result:
            logger.info("Тест подключения успешно завершен")
        else:
            logger.error("Тест подключения завершился с ошибкой")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")