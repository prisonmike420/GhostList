#!/usr/bin/env python3
import os
import logging
import asyncio
import json
from aiohttp import web

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def handle_root(request):
    """Обработчик корневого маршрута для проверки работоспособности"""
    return web.Response(text="NullifierCore активен", status=200)

async def start_server():
    """Запуск HTTP-сервера для мониторинга"""
    app = web.Application()
    app.router.add_get('/', handle_root)

    # Получаем порт из переменных окружения или используем 3000 по умолчанию
    port = int(os.environ.get('PORT', 3000))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    logger.info(f"HTTP-сервер запущен на порту {port}")
    return runner

async def stop_server(runner):
    """Остановка HTTP-сервера"""
    await runner.cleanup()
    logger.info("HTTP-сервер остановлен")

if __name__ == "__main__":
    # Если этот скрипт запускается напрямую, запускаем только сервер
    async def run_server():
        runner = await start_server()
        try:
            # Бесконечный цикл для поддержания работы сервера
            while True:
                await asyncio.sleep(3600)  # Проверка каждый час
        except (KeyboardInterrupt, SystemExit):
            await stop_server(runner)

    asyncio.run(run_server())