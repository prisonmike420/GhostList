async def get_user_full_info(user):
    """Получение полной информации о пользователе"""
    try:
        # Получаем полную информацию о пользователе через GetFullUserRequest
        full_user = await client(GetFullUserRequest(user.id))

        # Сборка информации
        user_info = {
            'bio': getattr(full_user.full_user, 'about', None),
            'is_scam': getattr(user, 'scam', False),
            'is_fake': getattr(user, 'fake', False)
        }

        return user_info
    except Exception as e:
        logger.error(f"Ошибка при получении полной информации о пользователе {user.id}: {e}")
        return {
            'bio': None,
            'is_scam': False,
            'is_fake': False
        }

async def get_user_join_date(channel_peer, user_id):
    """Получение даты присоединения пользователя к каналу"""
    try:
        participant = await client(GetParticipantRequest(
            channel=channel_peer,
            participant=user_id
        ))

        # Получаем дату присоединения, если она доступна
        if hasattr(participant.participant, 'date'):
            return participant.participant.date
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении даты присоединения пользователя {user_id}: {e}")
        return None#!/usr/bin/env python3
import os
import logging
import asyncio
import json
import csv
from datetime import datetime
from typing import List, Dict, Union, Optional, Tuple, Any

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Импорт библиотек для Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telethon import TelegramClient
from telethon.tl.types import InputPeerChannel, ChannelParticipantsSearch, ChannelParticipantsAdmins, ChannelParticipantsBots
from telethon.tl.functions.channels import GetParticipantsRequest, GetFullChannelRequest, GetParticipantRequest
from telethon.tl.functions.users import GetFullUserRequest

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

# Константы
DATA_DIR = os.path.join(os.getcwd(), 'data')
CHANNELS_FILE = os.path.join(DATA_DIR, 'channels.json')
SESSION_PATH = os.path.join(DATA_DIR, 'bot_session.session')
ADMIN_IDS = []  # ID администраторов бота
active_downloads = {}  # Для отслеживания активных выгрузок
user_contexts = {}  # Контексты пользователей
MAX_SUBSCRIBERS_PER_REQUEST = 5000  # Максимальное количество подписчиков для выгрузки (для Replit)

# Инициализация клиента MTProto
client = None  # Будет инициализирован позже

# === Функции работы с каналами ===

def load_channels() -> List[Dict]:
    """Загрузка сохраненных каналов из файла"""
    try:
        if os.path.exists(CHANNELS_FILE):
            with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения файла каналов: {e}")
    return []

def save_channels(channels: List[Dict]) -> None:
    """Сохранение каналов в файл"""
    # Убедимся, что папка существует
    if not os.path.exists(os.path.dirname(CHANNELS_FILE)):
        os.makedirs(os.path.dirname(CHANNELS_FILE))

    with open(CHANNELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(channels, f, indent=2, ensure_ascii=False)

async def add_channel(channel_identifier: str) -> Dict[str, Any]:
    """Добавление канала в список"""
    try:
        logger.info(f"Проверка канала: {channel_identifier}")

        # Получаем информацию о канале
        try:
            entity = await client.get_entity(channel_identifier)
            logger.info(f"Получена информация о канале: {entity}")
        except Exception as e:
            logger.error(f"Ошибка получения информации о канале: {e}")
            return {
                "success": False,
                "error": f"Не удалось найти канал. Причина: {str(e)}"
            }

        # Проверяем, что это канал
        if not hasattr(entity, 'megagroup') and not hasattr(entity, 'broadcast'):
            logger.error(f"Найденная сущность не является каналом: {entity}")
            return {"success": False, "error": "Указанный идентификатор не является каналом"}

        logger.info(f"Найден канал: {entity.title} (ID: {entity.id})")

        # Получаем информацию о боте
        me = await client.get_me()
        logger.info(f"Информация о боте: {me.id}")

        # Проверяем права бота в канале
        try:
            # Для более надежной проверки запросим полную информацию о канале
            full_channel = await client(GetFullChannelRequest(channel=entity))

            # Проверим, является ли бот администратором
            try:
                admin_rights = await client(GetParticipantRequest(
                    channel=entity,
                    participant=me.id
                ))

                logger.info(f"Права бота в канале: {admin_rights.participant}")

                # Проходит любой администратор или бот
                participant_type = type(admin_rights.participant).__name__
                is_admin = 'Admin' in participant_type or 'Creator' in participant_type

                if not is_admin:
                    logger.error(f"Бот не имеет прав администратора, тип: {participant_type}")
                    return {
                        "success": False,
                        "error": "Бот не является администратором канала. Добавьте бота администратором канала и попробуйте снова."
                    }
            except Exception as admin_error:
                logger.error(f"Ошибка проверки прав администратора: {admin_error}")
                return {
                    "success": False,
                    "error": f"Не удалось проверить права бота в канале. Убедитесь, что бот добавлен как администратор: {str(admin_error)}"
                }

            # Загружаем текущий список каналов
            channels = load_channels()

            # Проверяем, есть ли уже этот канал в списке
            existing_index = next((i for i, ch in enumerate(channels) 
                                  if str(ch.get('id', '')) == str(entity.id)), -1)

            if existing_index != -1:
                # Обновляем accessHash если отсутствует
                if not channels[existing_index].get('accessHash') and hasattr(entity, 'access_hash'):
                    channels[existing_index]['accessHash'] = str(entity.access_hash)
                    save_channels(channels)
                    logger.info(f"Обновлен accessHash для существующего канала {entity.title}")
                return {"success": False, "error": "Этот канал уже добавлен в список"}

            # Добавляем канал в список с accessHash
            channel_data = {
                "id": str(entity.id),
                "title": entity.title,
                "username": getattr(entity, 'username', None) or 'Приватный канал',
                "accessHash": str(entity.access_hash) if hasattr(entity, 'access_hash') else None,
                "addedAt": datetime.now().isoformat()
            }
            logger.info(f"Добавление канала в список: {channel_data}")

            channels.append(channel_data)

            # Сохраняем обновленный список
            save_channels(channels)

            return {
                "success": True,
                "channel": {
                    "id": str(entity.id),
                    "title": entity.title,
                    "username": getattr(entity, 'username', None) or 'Приватный канал'
                }
            }
        except Exception as e:
            logger.error(f"Ошибка проверки прав или сохранения канала: {e}")
            return {
                "success": False,
                "error": f"Ошибка проверки прав. Проверьте, что бот добавлен как администратор канала: {str(e)}"
            }
    except Exception as e:
        logger.error(f"Ошибка добавления канала: {e}")
        return {
            "success": False,
            "error": f"Не удалось добавить канал. Подробности: {str(e)}"
        }

def remove_channel(channel_id: str) -> Dict[str, Any]:
    """Удаление канала из списка"""
    channels = load_channels()
    initial_length = len(channels)

    filtered_channels = [ch for ch in channels if str(ch.get('id', '')) != str(channel_id)]

    if len(filtered_channels) == initial_length:
        return {"success": False, "error": "Канал не найден в списке"}

    save_channels(filtered_channels)
    return {"success": True}

async def migrate_channels() -> None:
    """Обновление информации о каналах (добавление accessHash)"""
    channels = load_channels()
    updated = False

    for i, channel in enumerate(channels):
        # Пропускаем каналы, у которых уже есть accessHash
        if channel.get('accessHash'):
            continue

        try:
            # Пробуем получить канал по username, если доступен
            username = channel.get('username')
            if username and username != 'Приватный канал':
                logger.info(f"Обновление данных канала {channel['title']} через username...")
                entity = await client.get_entity(f"@{username}")
                if hasattr(entity, 'access_hash'):
                    channels[i]['accessHash'] = str(entity.access_hash)
                    updated = True
                    logger.info(f"Обновлен accessHash для канала {channel['title']}")
        except Exception as e:
            logger.error(f"Не удалось получить accessHash для канала {channel['title']}: {e}")

    if updated:
        save_channels(channels)
        logger.info("Каналы обновлены с accessHash")

async def update_progress_message(update: Update, message_id: int, text: str, 
                                 progress: int, add_cancel_button: bool = False) -> bool:
    """Обновление сообщения с прогрессом"""
    try:
        # Создаем прогресс-бар
        progress_bar_length = 20
        filled = int(progress_bar_length * progress / 100)
        empty = progress_bar_length - filled

        filled_char = '█'
        empty_char = '░'
        progress_bar = filled_char * filled + empty_char * empty

        # Составляем сообщение
        progress_message = f"{text}\n\n[{progress_bar}] {progress}%"

        keyboard = None
        if add_cancel_button:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_download_{message_id}")]
            ])

        # Обновляем сообщение
        await update.callback_query.edit_message_text(
            progress_message, 
            reply_markup=keyboard
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления индикатора прогресса: {e}")
        return False

async def get_channel_subscribers(channel_peer, update: Update, message_id: int) -> List[Dict]:
    """Получение списка подписчиков канала с отображением прогресса"""
    # Создаем объект отслеживания для отмены
    download_tracker = {"cancelled": False, "partial_data": []}
    active_downloads[message_id] = download_tracker

    try:
        # Начальное сообщение
        await update_progress_message(
            update,
            message_id,
            'Запущено получение подписчиков канала. Пожалуйста, ожидайте...',
            0,
            True  # Добавляем кнопку отмены
        )

        # Используем dictionary для хранения уникальных пользователей
        unique_users = {}

        # Получаем информацию о канале
        try:
            full_channel_info = await client(GetFullChannelRequest(channel=channel_peer))
            participants_count = getattr(full_channel_info.full_chat, 'participants_count', 0)

            await update_progress_message(
                update,
                message_id,
                f"Получение подписчиков канала. Примерное количество участников: {participants_count}",
                5,
                True
            )
        except Exception as e:
            logger.error(f"Ошибка при получении информации о канале: {e}")
            await update_progress_message(
                update,
                message_id,
                "Получение подписчиков канала. Не удалось определить размер канала.",
                5,
                True
            )

        # Проверка на отмену
        if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
            await update_progress_message(
                update,
                message_id,
                'Выгрузка отменена пользователем.',
                100,
                False
            )
            if message_id in active_downloads:
                del active_downloads[message_id]
            return []

        # Поиск подписчиков - базовый вариант
        try:
            result = await client(GetParticipantsRequest(
                channel=channel_peer,
                filter=ChannelParticipantsSearch(''),
                offset=0,
                limit=200,
                hash=0
            ))

            if result and result.users:
                for user in result.users:
                    # Проверка на отмену
                    if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                        break

                    user_key = f"id{user.id}"
                    if user_key not in unique_users:
                        try:
                            # Получаем дополнительную информацию
                            full_info = await get_user_full_info(user)
                            join_date = await get_user_join_date(channel_peer, user.id)

                            # Сохраняем поля пользователя
                            user_data = {
                                'id': user.id,
                                'username': getattr(user, 'username', None),
                                'firstName': getattr(user, 'first_name', None),
                                'lastName': getattr(user, 'last_name', None),
                                'phone': getattr(user, 'phone', None),
                                'bot': getattr(user, 'bot', False),
                                'deleted': getattr(user, 'deleted', False),
                                'premium': getattr(user, 'premium', False),
                                'bio': full_info['bio'],
                                'is_scam': full_info['is_scam'],
                                'is_fake': full_info['is_fake'],
                                'join_date': join_date.isoformat() if join_date else None
                            }
                            unique_users[user_key] = user_data

                            if message_id in active_downloads:
                                active_downloads[message_id]["partial_data"].append(user_data)
                        except Exception as user_error:
                            logger.error(f"Ошибка при обработке пользователя {user.id}: {user_error}")
        except Exception as e:
            logger.error(f"Ошибка при получении подписчиков: {e}")

        # Проверка на отмену после цикла
        if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
            await update_progress_message(
                update,
                message_id,
                'Выгрузка отменена пользователем.',
                100,
                False
            )
            if message_id in active_downloads:
                del active_downloads[message_id]
            return []

        # Завершающее сообщение
        await update_progress_message(
            update,
            message_id,
            f"Получение подписчиков завершено! Найдено {len(unique_users)} участников.",
            100,
            False
        )

        # Удаляем из активных загрузок
        if message_id in active_downloads:
            del active_downloads[message_id]

        # Возвращаем список
        return list(unique_users.values())

    except Exception as e:
        logger.error(f"Ошибка при получении подписчиков: {e}")

        try:
            # Сообщаем об ошибке
            await update_progress_message(
                update,
                message_id,
                f"❌ Ошибка при получении подписчиков: {e}",
                100,
                False
            )
        except Exception:
            pass

        # Удаляем из активных загрузок
        if message_id in active_downloads:
            del active_downloads[message_id]

        return []

        # Используем dictionary для хранения уникальных пользователей
        unique_users = {}

        # Для отслеживания прогресса
        total_processed = 0
        current_progress = 5  # Начинаем с 5%

        # Буквы и символы для поиска
        alphabet = 'abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789_'
        special_searches = ['@', '.', ' ', '-', '+', '*']

        # Оценка общего количества запросов
        total_queries = len(alphabet) + len(special_searches) + 1  # +1 для пустого запроса

        # Поиск по пустой строке
        try:
            empty_search = await client(GetParticipantsRequest(
                channel=channel_peer,
                filter=ChannelParticipantsSearch(''),
                offset=0,
                limit=200,
                hash=0
            ))

            if empty_search and empty_search.users:
                before_count = len(unique_users)
                for user in empty_search.users:
                    user_key = f"id{user.id}"
                    if user_key not in unique_users:
                        # Получаем дополнительную информацию о пользователе
                        full_info = await get_user_full_info(user)

                        # Получаем дату присоединения к каналу
                        join_date = await get_user_join_date(channel_peer, user.id)

                        # Сохраняем все поля пользователя
                        unique_users[user_key] = {
                            'id': user.id,
                            'username': getattr(user, 'username', None),
                            'firstName': getattr(user, 'first_name', None),
                            'lastName': getattr(user, 'last_name', None),
                            'phone': getattr(user, 'phone', None),
                            'bot': getattr(user, 'bot', False),
                            'deleted': getattr(user, 'deleted', False),
                            'premium': getattr(user, 'premium', False),
                            # Новые поля
                            'bio': full_info['bio'],
                            'is_scam': full_info['is_scam'],
                            'is_fake': full_info['is_fake'],
                            'status': full_info['status'],
                            'last_active': full_info['last_active'],
                            'language': full_info['language'],
                            'join_date': join_date.isoformat() if join_date else None
                        }

                # Проверка на отмену 
                if active_downloads[message_id]["cancelled"]:
                    await update_progress_message(
                        update,
                        message_id,
                        'Выгрузка отменена пользователем.',
                        100,
                        False
                    )
                    del active_downloads[message_id]
                    return None

                # Обновляем прогресс
                total_processed += 1
                current_progress = 5 + round((total_processed / total_queries) * 90)

                await update_progress_message(
                    update,
                    message_id,
                    f"Получение подписчиков: найдено {len(empty_search.users)} участников по пустому запросу",
                    current_progress,
                    True
                )

            await asyncio.sleep(1)  # Задержка между запросами
        except Exception as e:
            logger.error(f"Ошибка при поиске по пустой строке: {e}")

        # Проверка на отмену
        if active_downloads[message_id]["cancelled"]:
            await update_progress_message(
                update,
                message_id,
                'Выгрузка отменена пользователем.',
                100,
                False
            )
            del active_downloads[message_id]
            return None

        # Поиск по специальным символам
        for search in special_searches:
            try:
                result = await client(GetParticipantsRequest(
                    channel=channel_peer,
                    filter=ChannelParticipantsSearch(search),
                    offset=0,
                    limit=200,
                    hash=0
                ))

                # Проверка на отмену после каждого запроса
                if active_downloads[message_id]["cancelled"]:
                    await update_progress_message(
                        update,
                        message_id,
                        'Выгрузка отменена пользователем.',
                        100,
                        False
                    )
                    del active_downloads[message_id]
                    return None

                if result and result.users:
                    before_count = len(unique_users)
                    for user in result.users:
                        user_key = f"id{user.id}"
                        if user_key not in unique_users:
                            # Получаем дополнительную информацию о пользователе
                            full_info = await get_user_full_info(user)

                            # Получаем дату присоединения к каналу
                            join_date = await get_user_join_date(channel_peer, user.id)

                            # Сохраняем все поля пользователя
                            user_data = {
                                'id': user.id,
                                'username': getattr(user, 'username', None),
                                'firstName': getattr(user, 'first_name', None),
                                'lastName': getattr(user, 'last_name', None),
                                'phone': getattr(user, 'phone', None),
                                'bot': getattr(user, 'bot', False),
                                'deleted': getattr(user, 'deleted', False),
                                'premium': getattr(user, 'premium', False),
                                # Новые поля
                                'bio': full_info['bio'],
                                'is_scam': full_info['is_scam'],
                                'is_fake': full_info['is_fake'],
                                'join_date': join_date.isoformat() if join_date else None
                            }
                            unique_users[user_key] = user_data

                            # Также добавляем в список частичных данных
                            download_tracker["partial_data"].append(user_data)
                    added_count = len(unique_users) - before_count

                    # Обновляем прогресс
                    total_processed += 1
                    current_progress = 5 + round((total_processed / total_queries) * 90)

                    await update_progress_message(
                        update,
                        message_id,
                        f"Получение подписчиков: поиск по \"{search}\" добавил {added_count} новых участников. Всего: {len(unique_users)}",
                        current_progress,
                        True
                    )
                else:
                    # Даже если нет результатов, обновляем прогресс
                    total_processed += 1
                    current_progress = 5 + round((total_processed / total_queries) * 90)

                    await update_progress_message(
                        update,
                        message_id,
                        f"Получение подписчиков: поиск по \"{search}\" не дал результатов. Всего: {len(unique_users)}",
                        current_progress,
                        True
                    )

                await asyncio.sleep(1)  # Задержка между запросами
            except Exception as e:
                logger.error(f"Ошибка при поиске по \"{search}\": {e}")

                # Даже при ошибке обновляем прогресс
                total_processed += 1
                current_progress = 5 + round((total_processed / total_queries) * 90)

                await update_progress_message(
                    update,
                    message_id,
                    f"Получение подписчиков: ошибка при поиске по \"{search}\". Всего: {len(unique_users)}",
                    current_progress,
                    True
                )

        # Основной цикл по алфавиту (подобный код)
        for letter in alphabet:
            try:
                result = await client(GetParticipantsRequest(
                    channel=channel_peer,
                    filter=ChannelParticipantsSearch(letter),
                    offset=0,
                    limit=200,
                    hash=0
                ))

                # Проверка на отмену после каждого запроса
                if download_tracker["cancelled"]:
                    del active_downloads[message_id]
                    return None

                if result and result.users:
                    before_count = len(unique_users)
                    for user in result.users:
                        user_key = f"id{user.id}"
                        if user_key not in unique_users:
                            # Получаем дополнительную информацию о пользователе
                            full_info = await get_user_full_info(user)

                            # Получаем дату присоединения к каналу
                            join_date = await get_user_join_date(channel_peer, user.id)

                            # Сохраняем все поля пользователя
                            unique_users[user_key] = {
                                'id': user.id,
                                'username': getattr(user, 'username', None),
                                'firstName': getattr(user, 'first_name', None),
                                'lastName': getattr(user, 'last_name', None),
                                'phone': getattr(user, 'phone', None),
                                'bot': getattr(user, 'bot', False),
                                'deleted': getattr(user, 'deleted', False),
                                'premium': getattr(user, 'premium', False),
                                # Новые поля
                                'bio': full_info['bio'],
                                'is_scam': full_info['is_scam'],
                                'is_fake': full_info['is_fake'],
                                'status': full_info['status'],
                                'last_active': full_info['last_active'],
                                'language': full_info['language'],
                                'join_date': join_date.isoformat() if join_date else None
                            }
                    added_count = len(unique_users) - before_count

                    # Обновляем прогресс
                    total_processed += 1
                    current_progress = 5 + round((total_processed / total_queries) * 90)

                    await update_progress_message(
                        update,
                        message_id,
                        f"Получение подписчиков: поиск по букве \"{letter}\" добавил {added_count} новых участников. Всего: {len(unique_users)}",
                        current_progress,
                        True
                    )
                else:
                    # Даже если нет результатов, обновляем прогресс
                    total_processed += 1
                    current_progress = 5 + round((total_processed / total_queries) * 90)

                    await update_progress_message(
                        update,
                        message_id,
                        f"Получение подписчиков: поиск по букве \"{letter}\" не дал результатов. Всего: {len(unique_users)}",
                        current_progress,
                        True
                    )

                await asyncio.sleep(1)  # Задержка между запросами
            except Exception as e:
                logger.error(f"Ошибка при поиске по букве \"{letter}\": {e}")

                # Даже при ошибке обновляем прогресс
                total_processed += 1
                current_progress = 5 + round((total_processed / total_queries) * 90)

                await update_progress_message(
                    update,
                    message_id,
                    f"Получение подписчиков: ошибка при поиске по букве \"{letter}\". Всего: {len(unique_users)}",
                    current_progress,
                    True
                )

        # Завершающее сообщение (уже без кнопки отмены)
        await update_progress_message(
            update,
            message_id,
            f"Получение подписчиков завершено! Найдено {len(unique_users)} уникальных участников.",
            100,
            False
        )

        # Удаляем из активных загрузок
        del active_downloads[message_id]

        # Преобразуем словарь в список для возврата
        return list(unique_users.values())
    except Exception as e:
        logger.error(f"Ошибка при получении подписчиков с прогрессом: {e}")

        # Сообщаем об ошибке
        await update_progress_message(
            update,
            message_id,
            f"❌ Ошибка при получении подписчиков: {e}",
            100,
            False
        )

        # Удаляем из активных загрузок
        del active_downloads[message_id]

        return []

async def cancel_download(message_id: int) -> bool:
    """Отмена процесса выгрузки - реальное прерывание процесса"""
    if message_id in active_downloads:
        logger.info(f"Запрос отмены выгрузки для message_id {message_id}")
        active_downloads[message_id]["cancelled"] = True

        # Принудительно завершить все связанные задачи
        cancelled = False
        for task in asyncio.all_tasks():
            task_name = task.get_name()
            if task_name.startswith(f"download_{message_id}"):
                logger.info(f"Отмена задачи с именем {task_name}")
                task.cancel()
                cancelled = True

        # Даже если задача не найдена, устанавливаем флаг отмены
        return True
    return False

async def export_partial_data(update: Update, message_id: int, channel_title: str) -> bool:
    """Экспорт частично полученных данных при отмене выгрузки"""
    if message_id not in active_downloads or not active_downloads[message_id].get("partial_data"):
        return False

    # Получаем собранные данные
    partial_subscribers = active_downloads[message_id]["partial_data"]

    if not partial_subscribers:
        return False

    # Создаем CSV с частичными данными
    csv_result = create_subscribers_csv(partial_subscribers, f"{channel_title}_partial")

    if not csv_result:
        return False

    # Отправляем файл
    with open(csv_result["filePath"], 'rb') as file:
        await update.effective_chat.send_document(
            document=file,
            filename=csv_result["fileName"],
            caption=f"Частичный список ({csv_result['count']} обработанных)"
        )

    # Удаляем файл после отправки
    if os.path.exists(csv_result["filePath"]):
        os.remove(csv_result["filePath"])

    # Сообщаем, что отправили частичные данные
    await update.callback_query.edit_message_text(
        f"✅ Отправлен CSV с {csv_result['count']} пользователями",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
        ])
    )

    return True

def create_subscribers_csv(subscribers: List[Dict], channel_title: str) -> Dict[str, Any]:
    """Создание CSV файла с подписчиками"""
    try:
        logger.info(f"Создание CSV файла для {len(subscribers)} подписчиков канала \"{channel_title}\"...")

        # Создаем имя файла на основе названия канала и текущей даты/времени
        timestamp = datetime.now().isoformat().replace(':', '-').replace('.', '-')
        safe_channel_title = ''.join(c if c.isalnum() or c in ['_', '-'] else '_' for c in channel_title.lower())
        file_name = f"subs_{safe_channel_title}_{len(subscribers)}_{timestamp}.csv"

        # Используем папку data для хранения файлов (для Replit)
        file_path = os.path.join(DATA_DIR, file_name)

        # Создаем папку, если не существует
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

        # Создаем CSV файл
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            # Заголовок CSV
            writer.writerow([
                'ID', 'Username', 'First Name', 'Last Name', 'Phone', 'Bot', 'Deleted', 'Premium',
                # Новые поля
                'Bio', 'Scam', 'Fake', 'Join Date'
            ])

            # Данные пользователей
            for user in subscribers:
                writer.writerow([
                    user.get('id', ''),
                    f"@{user.get('username', '')}" if user.get('username') else '',
                    user.get('firstName', ''),
                    user.get('lastName', ''),
                    user.get('phone', ''),
                    'Да' if user.get('bot', False) else 'Нет',
                    'Да' if user.get('deleted', False) else 'Нет',
                    'Да' if user.get('premium', False) else 'Нет',
                    # Новые поля 
                    user.get('bio', '') or '',
                    'Да' if user.get('is_scam', False) else 'Нет',
                    'Да' if user.get('is_fake', False) else 'Нет',
                    user.get('join_date', '') or ''
                ])

        logger.info(f"CSV файл создан: {file_name}")

        return {
            "fileName": file_name,
            "filePath": file_path,
            "count": len(subscribers)
        }
    except Exception as e:
        logger.error(f"Ошибка создания CSV: {e}")
        return None

# === Обработчики команд ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start"""
    user_id = update.effective_user.id

    # Добавляем первого пользователя как админа, если список пустой
    if user_id not in ADMIN_IDS:
        if not ADMIN_IDS:
            ADMIN_IDS.append(user_id)
            logger.info(f"Пользователь {user_id} добавлен как первый администратор")

    keyboard = [
        [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
        [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]

    await update.message.reply_text(
        'GhostList v1.2.0 активирован! 🤖\n\n'
        'Я могу помочь вам выгрузить список подписчиков из каналов, где я являюсь администратором.\n\n'
        'Доступные команды:\n'
        '/channels - Показать список добавленных каналов\n'
        '/addchannel - Добавить канал (формат: /addchannel @username или /addchannel -100123456789)\n'
        '/removechannel - Удалить канал из списка\n'
        '/help - Показать справку',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /help"""
    keyboard = [
        [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
        [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
    ]

    await update.message.reply_text(
        '*GhostList v1.2.0 - Помощь*\n\n'
        '*Команды:*\n'
        '/start - Начать работу с ботом\n'
        '/channels - Показать список каналов\n'
        '/addchannel - Добавить канал (формат: /addchannel @username или /addchannel -100123456789)\n'
        '/removechannel - Удалить канал из списка\n'
        '/help - Показать это сообщение\n\n'
        '*Как использовать:*\n'
        '1. Добавьте бота администратором канала\n'
        '2. Добавьте канал в список командой /addchannel\n'
        '3. Используйте команду /channels\n'
        '4. Выберите канал из списка\n'
        '5. Дождитесь выгрузки CSV файла с подписчиками\n\n'
        '*Функции:*\n'
        '• Расширенная информация: биография, дата вступления и др.\n'
        '• Кнопка "Отменить" — полная остановка выгрузки',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /addchannel"""
    user_id = update.effective_user.id

    # Проверка прав администратора
    if user_id not in ADMIN_IDS:
        if not ADMIN_IDS:
            ADMIN_IDS.append(user_id)
            logger.info(f"Пользователь {user_id} добавлен как первый администратор")
        else:
            await update.message.reply_text('У вас нет прав для использования этой команды.')
            return

    if context.args:
        # Если передан идентификатор канала
        channel_identifier = ' '.join(context.args).strip()
        status_message = await update.message.reply_text(f"Добавление канала {channel_identifier}...")
        await process_add_channel(update, status_message.message_id, channel_identifier)
    else:
        # Если идентификатор не передан, просим его ввести
        msg = await update.message.reply_text(
            'Пожалуйста, введите @username или ID канала для добавления.\n\n'
            'Пример: @channelname или -100123456789\n\n'
            'Используйте /cancel для отмены.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )

        # Сохраняем контекст
        user_contexts[update.effective_chat.id] = {
            "action": "add_channel",
            "message_id": msg.message_id
        }

async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /removechannel"""
    user_id = update.effective_user.id

    # Проверка прав администратора
    if user_id not in ADMIN_IDS:
        if not ADMIN_IDS:
            ADMIN_IDS.append(user_id)
        else:
            await update.message.reply_text('У вас нет прав для использования этой команды.')
            return

    await show_remove_channel_menu(update)

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /channels"""
    user_id = update.effective_user.id

    # Проверка прав администратора
    if user_id not in ADMIN_IDS:
        if not ADMIN_IDS:
            ADMIN_IDS.append(user_id)
            logger.info(f"Пользователь {user_id} добавлен как первый администратор")
        else:
            await update.message.reply_text('У вас нет прав для использования этой команды.')
            return

    await show_channels_list(update)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /cancel"""
    chat_id = update.effective_chat.id

    # Проверяем, есть ли активный контекст для пользователя
    if chat_id in user_contexts:
        del user_contexts[chat_id]
        await update.message.reply_text(
            'Операция отменена.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_main")]
            ])
        )
    else:
        await update.message.reply_text('Нет активных операций для отмены.')

# === Обработчики сообщений и колбеков ===

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений"""
    # Пропускаем команды
    if update.message.text.startswith('/'):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Проверяем, есть ли активный контекст для пользователя
    if chat_id in user_contexts:
        context_data = user_contexts[chat_id]

        if context_data["action"] == "add_channel":
            channel_identifier = update.message.text.strip()
            status_message = await update.message.reply_text(f"Добавление канала {channel_identifier}...")

            # Удаляем контекст, так как запрос обрабатывается
            del user_contexts[chat_id]

            # Процесс добавления канала
            await process_add_channel(update, status_message.message_id, channel_identifier)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка колбеков от кнопок"""
    query = update.callback_query
    await query.answer()  # Отвечаем на callback query

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    message_id = query.message.message_id
    data = query.data

    # Проверка прав администратора
    if user_id not in ADMIN_IDS:
        if not ADMIN_IDS:
            ADMIN_IDS.append(user_id)
            logger.info(f"Пользователь {user_id} добавлен как первый администратор")
        else:
            await query.answer("У вас нет прав для использования этой функции.", show_alert=True)
            return

    # Логирование запроса для отладки
    logger.info(f"Получен callback query: {data} от пользователя {user_id}, message_id={message_id}")

    # Обработка различных колбеков
    if data == "back_to_main":
        # Возврат в главное меню
        keyboard = [
            [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("❓ Помощь", callback_data="help")]
        ]

        await query.edit_message_text(
            'GhostList v1.2.0 активирован! 🤖\n\n'
            'Выберите действие из меню ниже:',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "channels_list":
        # Показать список каналов
        await show_channels_list(update)

    elif data == "add_channel":
        # Запрос на добавление канала
        await query.edit_message_text(
            'Пожалуйста, введите @username или ID канала для добавления.\n\n'
            'Пример: @channelname или -100123456789\n\n'
            'Используйте /cancel для отмены.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )

        # Сохраняем контекст
        user_contexts[chat_id] = {
            "action": "add_channel",
            "message_id": message_id
        }

    elif data == "help":
        # Показать справку
        await query.edit_message_text(
            '*NullifierCore v1.2.0 "Unique Subscribers" - Помощь*\n\n'
            '*Команды:*\n'
            '/start - Начать работу с ботом\n'
            '/channels - Показать список добавленных каналов\n'
            '/addchannel - Добавить канал (формат: /addchannel @username или /addchannel -100123456789)\n'
            '/removechannel - Удалить канал из списка\n'
            '/help - Показать это сообщение\n\n'
            '*Как использовать:*\n'
            '1. Добавьте бота администратором канала\n'
            '2. Добавьте канал в список командой /addchannel\n'
            '3. Используйте команду /channels\n'
            '4. Выберите канал из списка\n'
            '5. Дождитесь выгрузки CSV файла с подписчиками\n\n'
            '*Примечание:* Вы можете прервать процесс выгрузки в любой момент, нажав кнопку "Отменить выгрузку"',
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )

    # Обработка выбора канала для получения подписчиков
    elif data.startswith("channel_"):
        channel_id = data.split("_")[1]
        await process_channel_selection(update, channel_id)

    # Обработка удаления канала
    elif data.startswith("remove_"):
        channel_id = data.split("_")[1]

        try:
            result = remove_channel(channel_id)

            if result["success"]:
                await query.edit_message_text(
                    '✅ Канал успешно удален из списка.',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                    ])
                )
            else:
                await query.edit_message_text(
                    f"❌ Ошибка: {result['error']}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                    ])
                )
        except Exception as e:
            logger.error(f"Ошибка при удалении канала: {e}")

            await query.edit_message_text(
                'Произошла ошибка при удалении канала.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )

    # Обработка отмены выгрузки
    elif data.startswith("cancel_download_"):
        target_message_id = int(data.split("_")[2])
        logger.info(f"Запрос на отмену выгрузки, message_id={target_message_id}")

        try:
            cancel_result = await cancel_download(target_message_id)
            logger.info(f"Результат отмены: {cancel_result}")

            # Сообщаем пользователю о результате
            await query.edit_message_text(
                '❌ Выгрузка отменена. Возвращаемся в главное меню...',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )
        except Exception as e:
            logger.error(f"Ошибка при отмене выгрузки: {e}")
            await query.edit_message_text(
                f'Произошла ошибка при отмене выгрузки: {e}',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )

    # Обработка выгрузки частичных данных 
    elif data.startswith("export_partial_"):
        target_message_id = int(data.split("_")[2])

        # Получаем информацию о канале для формирования имени файла
        channel_title = "Channel"
        try:
            # Попытка получить название канала через peer
            if "channel_peer" in active_downloads[target_message_id]:
                channel_peer = active_downloads[target_message_id]["channel_peer"]
                channel_info = await client(GetFullChannelRequest(channel=channel_peer))
                if hasattr(channel_info, 'chats') and channel_info.chats:
                    channel_title = channel_info.chats[0].title
        except Exception as e:
            logger.error(f"Ошибка при получении названия канала: {e}")

        # Выгружаем частичные данные
        success = await export_partial_data(update, target_message_id, channel_title)

        if not success:
            await query.edit_message_text(
                'Нет данных для выгрузки.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Каналы", callback_data="channels_list")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )

# === Вспомогательные функции для обработки UI ===

async def show_channels_list(update: Update, message_id: int = None) -> None:
    """Отображение списка каналов"""
    channels = load_channels()
    query = update.callback_query

    if not channels:
        message = ('Список каналов пуст. Добавьте канал с помощью команды /addchannel.\n\n'
                  'Пример: /addchannel @channelname или /addchannel -100123456789')

        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
        ]

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Создаем инлайн-клавиатуру с каналами
    keyboard = []

    # Добавляем каналы
    for channel in channels:
        keyboard.append([
            InlineKeyboardButton(channel["title"], callback_data=f"channel_{channel['id']}")
        ])

    # Добавляем кнопки управления
    keyboard.append([InlineKeyboardButton("🗑 Удалить канал", callback_data="remove_channel_menu")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")])

    message = 'Выберите канал для выгрузки подписчиков:'

    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_remove_channel_menu(update: Update, message_id: int = None) -> None:
    """Отображение меню удаления каналов"""
    channels = load_channels()

    if update.callback_query:
        # Если вызвано через callback_query
        query = update.callback_query

        if not channels:
            message = 'Список каналов пуст. Нечего удалять.'

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )
            return

        # Создаем инлайн-клавиатуру с каналами для удаления
        keyboard = []
        for channel in channels:
            keyboard.append([
                InlineKeyboardButton(f"🗑 {channel['title']}", callback_data=f"remove_{channel['id']}")
            ])

        # Добавляем кнопку назад
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")])

        message = 'Выберите канал для удаления из списка:'

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # Если вызвано через команду
        if not channels:
            message = 'Список каналов пуст. Нечего удалять.'

            await update.message.reply_text(
                message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )
            return

        # Создаем инлайн-клавиатуру с каналами для удаления
        keyboard = []
        for channel in channels:
            keyboard.append([
                InlineKeyboardButton(f"🗑 {channel['title']}", callback_data=f"remove_{channel['id']}")
            ])

        # Добавляем кнопку назад
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")])

        message = 'Выберите канал для удаления из списка:'

        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def process_add_channel(update: Update, message_id: int, channel_identifier: str) -> None:
    """Обработка добавления канала"""
    global client  # Перемещаем global в начало функции
    try:
        # Проверяем, запущен ли клиент
        if not client or not client.is_connected():
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    'Подключение к Telegram API...'
                )
            else:
                await update.message.reply_text(
                    'Подключение к Telegram API...'
                )

            # Здесь должна быть логика повторного подключения клиента
            client = TelegramClient('bot_session', API_ID, API_HASH)
            await client.start(bot_token=BOT_TOKEN)

        # Добавляем канал
        result = await add_channel(channel_identifier)

        if result["success"]:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    f"✅ Канал \"{result['channel']['title']}\" успешно добавлен в список!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                    ])
                )
            else:
                await update.message.reply_text(
                    f"✅ Канал \"{result['channel']['title']}\" успешно добавлен в список!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                    ])
                )
        else:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    f"❌ Ошибка: {result['error']}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                    ])
                )
            else:
                await update.message.reply_text(
                    f"❌ Ошибка: {result['error']}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                    ])
                )
    except Exception as e:
        logger.error(f"Ошибка при добавлении канала: {e}")

        if update.callback_query:
            await update.callback_query.edit_message_text(
                'Произошла ошибка при добавлении канала. Проверьте корректность ID/username канала.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )
        else:
            await update.message.reply_text(
                'Произошла ошибка при добавлении канала. Проверьте корректность ID/username канала.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )

async def process_channel_selection(update: Update, channel_id: str) -> None:
    """Обработка выбора канала для получения подписчиков"""
    query = update.callback_query

    try:
        # Обновляем сообщение
        await query.edit_message_text(
            'Получение списка подписчиков... Это может занять некоторое время. '
            'Собираем информацию о пользователях (биография, дата присоединения и др.).'
        )

        # Получаем информацию о канале из нашего списка
        channels = load_channels()
        channel_info = next((ch for ch in channels if ch["id"] == channel_id), None)

        if not channel_info:
            await query.edit_message_text(
                'Канал не найден в списке. Попробуйте добавить его снова.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                ])
            )
            return

        # Если у канала нет accessHash, пробуем его обновить
        if not channel_info.get("accessHash"):
            try:
                # Пробуем получить канал по username, если доступен
                username = channel_info.get("username")
                if username and username != 'Приватный канал':
                    await query.edit_message_text(
                        f"Получение дополнительной информации о канале через username..."
                    )

                    entity = await client.get_entity(f"@{username}")
                    if hasattr(entity, 'access_hash'):
                        channel_info["accessHash"] = str(entity.access_hash)

                        # Обновляем канал в нашем списке
                        all_channels = load_channels()
                        idx = next((i for i, ch in enumerate(all_channels) if ch["id"] == channel_id), -1)
                        if idx != -1:
                            all_channels[idx]["accessHash"] = channel_info["accessHash"]
                            save_channels(all_channels)
                else:
                    # Если не можем получить accessHash
                    await query.edit_message_text(
                        'Не хватает данных о канале. Пожалуйста, добавьте канал заново через /addchannel',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                        ])
                    )
                    return
            except Exception as e:
                logger.error(f"Не удалось получить accessHash для канала: {e}")
                await query.edit_message_text(
                    'Не удалось получить доступ к каналу. Пожалуйста, добавьте его снова через /addchannel.',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                    ])
                )
                return

        # Создаем правильный InputPeerChannel
        channel_entity = None
        if channel_info.get("accessHash"):
            channel_entity = InputPeerChannel(
                channel_id=int(channel_info["id"]),
                access_hash=int(channel_info["accessHash"])
            )
        else:
            # Запрашиваем повторное добавление канала
            await query.edit_message_text(
                'Недостаточно данных для доступа к каналу. Пожалуйста, добавьте канал заново через /addchannel',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                ])
            )
            return

        # Получаем заголовок канала
        channel_title = channel_info.get("title", "Канал")

        # Получаем подписчиков с отображением прогресса
        subscribers = await get_channel_subscribers(channel_entity, update, query.message.message_id)

        # Проверяем, была ли отменена выгрузка
        if subscribers is None:
            return  # Функция get_channel_subscribers уже обновила сообщение

        if not subscribers:
            await query.edit_message_text(
                f"Не удалось получить подписчиков канала \"{channel_title}\" или канал пуст.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                ])
            )
            return

        # Создаем CSV файл
        await query.edit_message_text(
            f"Создание CSV файла с {len(subscribers)} подписчиками..."
        )

        csv_result = create_subscribers_csv(subscribers, channel_title)

        if not csv_result:
            await query.edit_message_text(
                'Ошибка при создании CSV файла.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                ])
            )
            return

        # Отправляем файл
        await query.edit_message_text(
            f"Отправка файла с {csv_result['count']} подписчиками..."
        )

        with open(csv_result["filePath"], 'rb') as file:
            await update.effective_chat.send_document(
                document=file,
                filename=csv_result["fileName"],
                caption=f"Список подписчиков канала \"{channel_title}\" ({csv_result['count']})\n"
                        f"Включена информация: биография, дата вступления и метки scam/fake."
            )

        # Удаляем файл после отправки
        if os.path.exists(csv_result["filePath"]):
            os.remove(csv_result["filePath"])

        # Обновляем сообщение
        await query.edit_message_text(
            f"✅ Выгрузка подписчиков канала \"{channel_title}\" завершена!\n\n"
            f"Всего уникальных подписчиков: {csv_result['count']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке выбора канала: {e}")

        await query.edit_message_text(
            'Произошла ошибка при получении подписчиков. Попробуйте позже.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
            ])
        )

# === Запуск бота ===

async def main() -> None:
    """Запуск бота"""
    global client

    logger.info('GhostList v1.2.0 запускается...')

    # Создаем папку для данных, если она не существует
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        logger.info(f"Создана папка для данных: {DATA_DIR}")

    # Инициализация клиента MTProto
    try:
        client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
        await client.start(bot_token=BOT_TOKEN)
        logger.info('Клиент MTProto запущен')

        # Мигрируем существующие каналы для добавления accessHash
        await migrate_channels()
    except Exception as e:
        logger.error(f"Ошибка при запуске клиента MTProto: {e}")
        raise

    # Инициализация бота
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("addchannel", add_channel_command))
    application.add_handler(CommandHandler("removechannel", remove_channel_command))
    application.add_handler(CommandHandler("channels", channels_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Обработчик колбеков от кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Настраиваем команды для меню бота
    bot_commands = [
        ("start", "Начать работу с ботом"),
        ("channels", "Показать список добавленных каналов"),
        ("addchannel", "Добавить канал"),
        ("removechannel", "Удалить канал из списка"),
        ("help", "Показать справку"),
        ("cancel", "Отменить текущую операцию")
    ]

    # Устанавливаем команды для BotFather
    try:
        await application.bot.set_my_commands(bot_commands)
        logger.info("Команды бота установлены в меню")
    except Exception as e:
        logger.error(f"Ошибка при установке команд бота: {e}")

    # Запуск бота в режиме long polling
    logger.info("Запуск бота...")

    # Запускаем бота и ждем его завершения
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    logger.info("GhostList готов к работе!")

    # Важно: ожидаем сигнал остановки вместо завершения функции
    # Это позволяет боту работать бесконечно
    stop_signal = asyncio.Future()

    try:
        # Ожидаем отмены или завершения бота
        await stop_signal
    except asyncio.CancelledError:
        # Корректная остановка приложения
        logger.info("Получен сигнал остановки...")

    finally:
        # Остановка приложения
        await application.stop()
        # Закрываем клиент MTProto
        await client.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение работы бота...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")