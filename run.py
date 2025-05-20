#!/usr/bin/env python3
import os
import sys
import logging
import asyncio
from server import start_server, stop_server

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Создаем постоянную папку для хранения данных на Replit
DATA_DIR = os.path.join(os.getcwd(), 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    logger.info(f"Создана постоянная папка для данных: {DATA_DIR}")

async def run_application():
    """Основная функция для запуска приложения"""
    logger.info('GhostList v1.2.0 "Unique Subscribers" запускается...')

    # Запуск HTTP-сервера
    runner = await start_server()

    try:
        # Импортируем main только здесь
        from main import main as run_bot

        # Запускаем бота и ждем сигнала завершения
        main_task = asyncio.create_task(run_bot())

        # Ждем завершения задачи или внешнего сигнала отмены
        try:
            await main_task
        except asyncio.CancelledError:
            logger.info("Получен сигнал отмены")
            main_task.cancel()
            try:
                await main_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Ошибка при работе бота: {e}")
    finally:
        # Остановка HTTP-сервера при завершении
        await stop_server(runner)

if __name__ == "__main__":
    try:
        # Используем asyncio.run для запуска основной функции
        asyncio.run(run_application())
    except KeyboardInterrupt:
        logger.info("Завершение работы NullifierCore...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)