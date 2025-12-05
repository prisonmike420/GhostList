#!/usr/bin/env python3
"""
GhostList v2.0.0 — Telegram-бот для парсинга подписчиков каналов с Supabase
"""
import os
import logging
import asyncio
import json
import csv
from datetime import datetime
from typing import List, Dict, Any, Set

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
from telethon.tl.types import InputPeerChannel, ChannelParticipantsSearch, ChannelParticipantsRecent, ChannelParticipantsAdmins
from telethon.tl.functions.channels import GetParticipantsRequest, GetFullChannelRequest, GetParticipantRequest
from telethon.tl.functions.users import GetFullUserRequest

# Импорт клиента Supabase
import supabase_client as db

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

# Загрузка списка разрешённых пользователей из переменной окружения
ALLOWED_USER_IDS_STR = os.environ.get('ALLOWED_USER_IDS', '')
ALLOWED_USER_IDS: List[int] = []
if ALLOWED_USER_IDS_STR:
    try:
        ALLOWED_USER_IDS = [int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(',') if uid.strip()]
        logger.info(f"Загружено {len(ALLOWED_USER_IDS)} разрешённых пользователей")
    except ValueError as e:
        logger.error(f"Ошибка при парсинге ALLOWED_USER_IDS: {e}")
        raise ValueError('ALLOWED_USER_IDS должен содержать ID пользователей через запятую (например: 123456789,987654321)')

# Проверка наличия всех необходимых переменных
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError('Отсутствуют необходимые настройки! Убедитесь, что вы добавили API_ID, API_HASH и TELEGRAM_BOT_TOKEN.')

if not ALLOWED_USER_IDS:
    logger.warning('⚠️ ALLOWED_USER_IDS не установлен! Бот будет доступен всем пользователям.')

# Константы
DATA_DIR = os.path.join(os.getcwd(), 'data')
CHANNELS_FILE = os.path.join(DATA_DIR, 'channels.json')
SESSION_PATH = os.path.join(DATA_DIR, 'bot_session.session')
active_downloads: Dict[int, Dict] = {}  # Для отслеживания активных выгрузок
user_contexts: Dict[int, Dict] = {}  # Контексты пользователей

# Инициализация клиента MTProto
client = None  # Будет инициализирован позже


# === Вспомогательные функции ===

def is_user_allowed(user_id: int) -> bool:
    """Проверка, разрешён ли доступ пользователю"""
    # Если список разрешённых пользователей пуст, разрешаем всем
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


async def get_user_full_info(user) -> Dict[str, Any]:
    """Получение полной информации о пользователе"""
    try:
        full_user = await client(GetFullUserRequest(user.id))
        return {
            'bio': getattr(full_user.full_user, 'about', None),
            'is_scam': getattr(user, 'scam', False),
            'is_fake': getattr(user, 'fake', False)
        }
    except Exception as e:
        logger.error(f"Ошибка при получении полной информации о пользователе {user.id}: {e}")
        return {'bio': None, 'is_scam': False, 'is_fake': False}


async def get_user_join_date(channel_peer, user_id):
    """Получение даты присоединения пользователя к каналу"""
    try:
        participant = await client(GetParticipantRequest(
            channel=channel_peer,
            participant=user_id
        ))
        if hasattr(participant.participant, 'date'):
            return participant.participant.date
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении даты присоединения пользователя {user_id}: {e}")
        return None


async def get_user_full_info_by_id(user_id: int) -> Dict[str, Any]:
    """Получение полной информации о пользователе по ID"""
    try:
        full_user = await client(GetFullUserRequest(user_id))
        return {
            'bio': getattr(full_user.full_user, 'about', None),
            'is_scam': False,  # Эти поля уже есть в базовом User
            'is_fake': False
        }
    except Exception as e:
        logger.error(f"Ошибка при получении полной информации о пользователе {user_id}: {e}")
        return {'bio': None, 'is_scam': False, 'is_fake': False}


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
            return {"success": False, "error": f"Не удалось найти канал. Причина: {str(e)}"}

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
            await client(GetFullChannelRequest(channel=entity))

            # Проверяем, является ли бот администратором
            admin_rights = await client(GetParticipantRequest(
                channel=entity,
                participant=me.id
            ))

            logger.info(f"Права бота в канале: {admin_rights.participant}")
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
        existing_index = next((i for i, ch in enumerate(channels) if str(ch.get('id', '')) == str(entity.id)), -1)

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
        logger.error(f"Ошибка добавления канала: {e}")
        return {"success": False, "error": f"Не удалось добавить канал. Подробности: {str(e)}"}


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
        if channel.get('accessHash'):
            continue

        try:
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


# === Функции прогресса и выгрузки ===

async def update_progress_message(update: Update, message_id: int, text: str,
                                 progress: int, add_cancel_button: bool = False) -> bool:
    """Обновление сообщения с прогрессом"""
    try:
        progress_bar_length = 20
        filled = int(progress_bar_length * progress / 100)
        empty = progress_bar_length - filled

        progress_bar = '█' * filled + '░' * empty
        progress_message = f"{text}\n\n[{progress_bar}] {progress}%"

        keyboard = None
        if add_cancel_button:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_download_{message_id}")]
            ])

        await update.callback_query.edit_message_text(progress_message, reply_markup=keyboard)
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления индикатора прогресса: {e}")
        return False


async def get_channel_subscribers(channel_peer, update: Update, message_id: int, extended_mode: bool = False) -> List[Dict]:
    """Получение списка подписчиков канала с отображением прогресса
    
    Args:
        channel_peer: Объект канала
        update: Telegram Update
        message_id: ID сообщения для обновления прогресса
        extended_mode: Если True, получаем bio и дату подписки (медленнее)
    """
    download_tracker = {"cancelled": False, "partial_data": []}
    active_downloads[message_id] = download_tracker

    try:
        mode_text = "расширенный режим (bio + дата)" if extended_mode else "быстрый режим"
        await update_progress_message(update, message_id, 
            f'Запущено получение подписчиков канала ({mode_text}). Пожалуйста, ожидайте...', 0, True)

        unique_users = {}
        participants_count = 0

        # Получаем информацию о канале
        try:
            full_channel_info = await client(GetFullChannelRequest(channel=channel_peer))
            participants_count = getattr(full_channel_info.full_chat, 'participants_count', 0)

            await update_progress_message(update, message_id,
                f"Получение подписчиков канала. Примерное количество: {participants_count}\nРежим: {mode_text}",
                5, True)
        except Exception as e:
            logger.error(f"Ошибка при получении информации о канале: {e}")
            await update_progress_message(update, message_id,
                f"Получение подписчиков канала. Режим: {mode_text}",
                5, True)

        # Проверка на отмену
        if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
            await update_progress_message(update, message_id, 'Выгрузка отменена пользователем.', 100, False)
            del active_downloads[message_id]
            return []

        last_update_time = datetime.now()
        
        # ============ МЕТОД 1: iter_participants ============
        logger.info("Метод 1: Получение через iter_participants...")
        try:
            user_count = 0
            # Убран aggressive=True - deprecated и больше не работает
            async for user in client.iter_participants(channel_peer):
                if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                    break
                    
                user_key = f"id{user.id}"
                if user_key not in unique_users:
                    try:
                        user_status = "Unknown"
                        if hasattr(user, 'status'):
                            status = user.status
                            if status:
                                status_name = type(status).__name__
                                if status_name == 'UserStatusOnline':
                                    user_status = 'Online'
                                elif status_name == 'UserStatusOffline':
                                    user_status = 'Offline'
                                elif status_name == 'UserStatusRecently':
                                    user_status = 'Recently'
                                elif status_name == 'UserStatusLastWeek':
                                    user_status = 'Last Week'
                                elif status_name == 'UserStatusLastMonth':
                                    user_status = 'Last Month'
                                elif status_name == 'UserStatusEmpty':
                                    user_status = 'Empty'
                                else:
                                    user_status = status_name
                        
                        user_data = {
                            'id': user.id,
                            'username': getattr(user, 'username', None),
                            'firstName': getattr(user, 'first_name', None),
                            'lastName': getattr(user, 'last_name', None),
                            'phone': getattr(user, 'phone', None),
                            'bot': getattr(user, 'bot', False),
                            'deleted': getattr(user, 'deleted', False),
                            'premium': getattr(user, 'premium', False),
                            'verified': getattr(user, 'verified', False),
                            'restricted': getattr(user, 'restricted', False),
                            'lang_code': getattr(user, 'lang_code', None),
                            'status': user_status,
                            'bio': None,
                            'is_scam': getattr(user, 'scam', False),
                            'is_fake': getattr(user, 'fake', False),
                            'join_date': None,
                            '_user_obj': user if extended_mode else None  # Сохраняем для расширенного режима
                        }
                        unique_users[user_key] = user_data
                        if message_id in active_downloads:
                            active_downloads[message_id]["partial_data"].append(user_data)
                        user_count += 1
                        
                        if user_count % 500 == 0 or (datetime.now() - last_update_time).total_seconds() > 3:
                            progress = min(30, int(user_count / max(participants_count, 1) * 30))
                            await update_progress_message(update, message_id,
                                f"Метод 1: Получено {len(unique_users)} пользователей...\n"
                                f"Всего в канале: {participants_count}",
                                progress, True)
                            last_update_time = datetime.now()
                    except Exception as e:
                        logger.error(f"Ошибка обработки пользователя: {e}")
            
            logger.info(f"После iter_participants: {len(unique_users)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка iter_participants: {e}")
        
        # ============ МЕТОД 2: ChannelParticipantsRecent ============
        logger.info("Метод 2: Получение недавних участников...")
        try:
            offset = 0
            limit = 200
            while True:
                if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                    break
                    
                participants = await client(GetParticipantsRequest(
                    channel=channel_peer,
                    filter=ChannelParticipantsRecent(),
                    offset=offset,
                    limit=limit,
                    hash=0
                ))
                
                if not participants.users:
                    break
                    
                for user in participants.users:
                    user_key = f"id{user.id}"
                    if user_key not in unique_users:
                        user_status = "Unknown"
                        if hasattr(user, 'status') and user.status:
                            status_name = type(user.status).__name__
                            status_map = {
                                'UserStatusOnline': 'Online',
                                'UserStatusOffline': 'Offline', 
                                'UserStatusRecently': 'Recently',
                                'UserStatusLastWeek': 'Last Week',
                                'UserStatusLastMonth': 'Last Month',
                                'UserStatusEmpty': 'Empty'
                            }
                            user_status = status_map.get(status_name, status_name)
                        
                        user_data = {
                            'id': user.id,
                            'username': getattr(user, 'username', None),
                            'firstName': getattr(user, 'first_name', None),
                            'lastName': getattr(user, 'last_name', None),
                            'phone': getattr(user, 'phone', None),
                            'bot': getattr(user, 'bot', False),
                            'deleted': getattr(user, 'deleted', False),
                            'premium': getattr(user, 'premium', False),
                            'verified': getattr(user, 'verified', False),
                            'restricted': getattr(user, 'restricted', False),
                            'lang_code': getattr(user, 'lang_code', None),
                            'status': user_status,
                            'bio': None,
                            'is_scam': getattr(user, 'scam', False),
                            'is_fake': getattr(user, 'fake', False),
                            'join_date': None,
                            '_user_obj': user if extended_mode else None
                        }
                        unique_users[user_key] = user_data
                        if message_id in active_downloads:
                            active_downloads[message_id]["partial_data"].append(user_data)
                
                offset += len(participants.users)
                if len(participants.users) < limit:
                    break
                await asyncio.sleep(0.3)
                
            logger.info(f"После ChannelParticipantsRecent: {len(unique_users)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка ChannelParticipantsRecent: {e}")
        
        # ============ МЕТОД 3: Расширенный алфавитный поиск ============
        logger.info("Метод 3: Расширенный алфавитный поиск...")
        
        # Расширенный набор поисковых запросов
        single_chars = [''] + list('abcdefghijklmnopqrstuvwxyz') + list('абвгдеёжзийклмнопрстуфхцчшщъыьэюя') + list('0123456789')
        
        # Двухбуквенные комбинации
        common_ru = 'аеиокнсмтвпрдлб'
        common_en = 'aeiosmtnrldcbpg'
        two_letter = []
        for c1 in common_ru:
            for c2 in 'аеиоуыя':
                two_letter.append(c1 + c2)
        for c1 in common_en:
            for c2 in 'aeiouy':
                two_letter.append(c1 + c2)
        
        # Трёхбуквенные популярные комбинации
        three_letter = ['ale', 'and', 'ann', 'ant', 'max', 'mar', 'mic', 'nik', 'ole', 'ser', 'vic', 'vol',
                        'але', 'ана', 'анд', 'анн', 'ант', 'вик', 'вла', 'дан', 'дми', 'ека', 'ива', 'иль',
                        'кир', 'мак', 'мар', 'мих', 'ник', 'оле', 'пав', 'сер', 'тат', 'юли', 'юри']
        
        # Числовые комбинации (00-99)
        number_combos = [f"{i:02d}" for i in range(100)]
        
        # Специальные символы и emoji
        special_chars = list('._-@#$%&*!?+')
        emoji_chars = ['😀', '😎', '🔥', '❤', '👍', '🎉', '💪', '🌟', '✨', '🎸', '🎮', '💻']
        
        search_queries = single_chars + two_letter + three_letter + number_combos + special_chars + emoji_chars
        total_searches = len(search_queries)
        
        await update_progress_message(update, message_id,
            f"Метод 3: Расширенный поиск ({total_searches} запросов)...\n"
            f"Уже найдено: {len(unique_users)} пользователей",
            35, True)
        
        try:
            for search_idx, search_char in enumerate(search_queries):
                if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                    break
                
                offset = 0
                limit = 200
                
                while True:
                    if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                        break
                    
                    try:
                        participants = await client(GetParticipantsRequest(
                            channel=channel_peer,
                            filter=ChannelParticipantsSearch(search_char),
                            offset=offset,
                            limit=limit,
                            hash=0
                        ))
                        
                        if not participants.users:
                            break
                        
                        for user in participants.users:
                            user_key = f"id{user.id}"
                            if user_key not in unique_users:
                                user_status = "Unknown"
                                if hasattr(user, 'status') and user.status:
                                    status_name = type(user.status).__name__
                                    status_map = {
                                        'UserStatusOnline': 'Online',
                                        'UserStatusOffline': 'Offline', 
                                        'UserStatusRecently': 'Recently',
                                        'UserStatusLastWeek': 'Last Week',
                                        'UserStatusLastMonth': 'Last Month',
                                        'UserStatusEmpty': 'Empty'
                                    }
                                    user_status = status_map.get(status_name, status_name)

                                user_data = {
                                    'id': user.id,
                                    'username': getattr(user, 'username', None),
                                    'firstName': getattr(user, 'first_name', None),
                                    'lastName': getattr(user, 'last_name', None),
                                    'phone': getattr(user, 'phone', None),
                                    'bot': getattr(user, 'bot', False),
                                    'deleted': getattr(user, 'deleted', False),
                                    'premium': getattr(user, 'premium', False),
                                    'verified': getattr(user, 'verified', False),
                                    'restricted': getattr(user, 'restricted', False),
                                    'lang_code': getattr(user, 'lang_code', None),
                                    'status': user_status,
                                    'bio': None,
                                    'is_scam': getattr(user, 'scam', False),
                                    'is_fake': getattr(user, 'fake', False),
                                    'join_date': None,
                                    '_user_obj': user if extended_mode else None
                                }
                                unique_users[user_key] = user_data
                                if message_id in active_downloads:
                                    active_downloads[message_id]["partial_data"].append(user_data)
                        
                        offset += len(participants.users)
                        if len(participants.users) < limit:
                            break
                        await asyncio.sleep(0.25)
                        
                    except Exception as e:
                        logger.error(f"Ошибка при поиске '{search_char}': {e}")
                        break
                
                if (datetime.now() - last_update_time).total_seconds() > 2:
                    search_progress = 35 + int((search_idx + 1) / total_searches * 45)
                    await update_progress_message(update, message_id,
                        f"Метод 3: Найдено {len(unique_users)} пользователей\n"
                        f"Поиск: {search_idx + 1}/{total_searches}\n"
                        f"Всего в канале: {participants_count}",
                        min(80, search_progress), True)
                    last_update_time = datetime.now()
                
                await asyncio.sleep(0.15)
                
        except Exception as e:
            logger.error(f"Ошибка при алфавитном поиске: {e}")

        # Проверка на отмену
        if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
            await update_progress_message(update, message_id, 'Выгрузка отменена пользователем.', 100, False)
            del active_downloads[message_id]
            return []

        logger.info(f"Расширенный поиск завершен. Найдено {len(unique_users)} уникальных пользователей.")
        
        # ============ РАСШИРЕННЫЙ РЕЖИМ: Получение bio и даты подписки ============
        if extended_mode and unique_users:
            await update_progress_message(update, message_id,
                f"Расширенный режим: Получение bio и даты подписки...\n"
                f"Пользователей: {len(unique_users)}",
                82, True)
            
            processed = 0
            total_to_process = len(unique_users)
            
            for user_key, user_data in unique_users.items():
                if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                    break
                
                # Пропускаем ботов и удалённые аккаунты
                if user_data.get('bot') or user_data.get('deleted'):
                    processed += 1
                    continue
                
                try:
                    # Получаем bio
                    full_info = await get_user_full_info_by_id(user_data['id'])
                    if full_info:
                        user_data['bio'] = full_info.get('bio')
                    
                    # Получаем дату подписки
                    join_date = await get_user_join_date(channel_peer, user_data['id'])
                    if join_date:
                        user_data['join_date'] = join_date.isoformat() if hasattr(join_date, 'isoformat') else str(join_date)
                    
                    await asyncio.sleep(0.5)  # Rate limiting
                    
                except Exception as e:
                    logger.error(f"Ошибка получения расширенной информации для {user_data['id']}: {e}")
                
                processed += 1
                
                # Обновляем прогресс каждые 50 пользователей
                if processed % 50 == 0 or (datetime.now() - last_update_time).total_seconds() > 5:
                    ext_progress = 82 + int(processed / total_to_process * 17)
                    await update_progress_message(update, message_id,
                        f"Расширенный режим: {processed}/{total_to_process}\n"
                        f"Получение bio и даты подписки...",
                        min(99, ext_progress), True)
                    last_update_time = datetime.now()
            
            # Удаляем временное поле _user_obj
            for user_data in unique_users.values():
                user_data.pop('_user_obj', None)

        # Завершающее сообщение
        await update_progress_message(update, message_id,
            f"Получение подписчиков завершено! Найдено {len(unique_users)} участников.",
            100, False)

        if message_id in active_downloads:
            del active_downloads[message_id]

        return list(unique_users.values())

    except Exception as e:
        logger.error(f"Ошибка при получении подписчиков: {e}")

        try:
            await update_progress_message(update, message_id,
                f"❌ Ошибка при получении подписчиков: {e}", 100, False)
        except Exception:
            pass

        if message_id in active_downloads:
            del active_downloads[message_id]

        return []


async def cancel_download(message_id: int) -> bool:
    """Отмена процесса выгрузки"""
    if message_id in active_downloads:
        logger.info(f"Запрос отмены выгрузки для message_id {message_id}")
        active_downloads[message_id]["cancelled"] = True

        for task in asyncio.all_tasks():
            task_name = task.get_name()
            if task_name.startswith(f"download_{message_id}"):
                logger.info(f"Отмена задачи с именем {task_name}")
                task.cancel()

        return True
    return False


async def get_channel_subscribers_turbo(channel_peer, channel_id: int, update: Update, message_id: int) -> Dict[str, Any]:
    """Турбо-режим: только iter_participants, сохранение в Supabase
    
    Returns:
        Dict с ключами: new_count, db_count, channel_count
    """
    download_tracker = {"cancelled": False}
    active_downloads[message_id] = download_tracker
    
    try:
        # Получаем информацию о канале
        participants_count = 0
        try:
            full_channel_info = await client(GetFullChannelRequest(channel=channel_peer))
            participants_count = getattr(full_channel_info.full_chat, 'participants_count', 0)
        except Exception as e:
            logger.error(f"Ошибка получения информации о канале: {e}")
        
        # Получаем существующие ID из БД
        existing_ids = db.get_existing_ids(channel_id)
        db_count_before = len(existing_ids)
        
        await update_progress_message(update, message_id,
            f"⚡ Турбо-режим\n\n"
            f"📊 В канале: {participants_count}\n"
            f"💾 В базе: {db_count_before}\n\n"
            f"Получение подписчиков...",
            10, True)
        
        # Собираем новых пользователей
        new_users = []
        user_count = 0
        last_update_time = datetime.now()
        
        async for user in client.iter_participants(channel_peer):
            if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                break
            
            # Проверяем, есть ли уже в БД
            if user.id in existing_ids:
                user_count += 1
                continue
            
            # Получаем статус
            user_status = "Unknown"
            if hasattr(user, 'status') and user.status:
                status_name = type(user.status).__name__
                status_map = {
                    'UserStatusOnline': 'Online',
                    'UserStatusOffline': 'Offline',
                    'UserStatusRecently': 'Recently',
                    'UserStatusLastWeek': 'Last Week',
                    'UserStatusLastMonth': 'Last Month',
                    'UserStatusEmpty': 'Empty'
                }
                user_status = status_map.get(status_name, status_name)
            
            user_data = {
                'id': user.id,
                'username': getattr(user, 'username', None),
                'firstName': getattr(user, 'first_name', None),
                'lastName': getattr(user, 'last_name', None),
                'phone': getattr(user, 'phone', None),
                'bot': getattr(user, 'bot', False),
                'status': user_status,
                'bio': None,
                'join_date': None
            }
            new_users.append(user_data)
            user_count += 1
            
            # Сохраняем пачками по 500
            if len(new_users) >= 500:
                await update_progress_message(update, message_id,
                    f"⚡ Турбо-режим\n\n"
                    f"📊 В канале: {participants_count}\n"
                    f"💾 В базе: {db_count_before}\n"
                    f"🆕 Новых: {len(existing_ids) + len(new_users)}\n\n"
                    f"Сохранение пачки...",
                    progress, True)
                
                inserted = db.upsert_subscribers(new_users, channel_id)
                db_count_before += inserted
                new_users = []  # Очищаем список после сохранения
                
            # Обновляем прогресс
            if user_count % 200 == 0 or (datetime.now() - last_update_time).total_seconds() > 2:
                progress = min(95, int(user_count / max(participants_count, 1) * 95))
                await update_progress_message(update, message_id,
                    f"⚡ Турбо-режим\n\n"
                    f"📊 В канале: {participants_count}\n"
                    f"💾 В базе: {db_count_before}\n"
                    f"🆕 В буфере: {len(new_users)}\n\n"
                    f"Обработано: {user_count}",
                    progress, True)
                last_update_time = datetime.now()
        
        # Проверка на отмену
        if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
            if message_id in active_downloads:
                del active_downloads[message_id]
            return {"new_count": 0, "db_count": db_count_before, "channel_count": participants_count, "cancelled": True}
        
        # Сохраняем остаток в Supabase
        if new_users:
            await update_progress_message(update, message_id,
                f"💾 Сохранение последних {len(new_users)} подписчиков...",
                98, True)
            inserted = db.upsert_subscribers(new_users, channel_id)
            db_count_before += inserted
        
        # Получаем финальный счётчик
        db_count_after = db.get_subscriber_count(channel_id)
        
        await update_progress_message(update, message_id,
            f"✅ Турбо-режим завершён!\n\n"
            f"📊 В канале: {participants_count}\n"
            f"💾 В базе: {db_count_after}\n"
            f"🆕 Добавлено: {len(new_users)}",
            100, False)
        
        if message_id in active_downloads:
            del active_downloads[message_id]
        
        return {
            "new_count": len(new_users),
            "db_count": db_count_after,
            "channel_count": participants_count,
            "cancelled": False
        }
        
    except Exception as e:
        logger.error(f"Ошибка турбо-режима: {e}")
        try:
            await update_progress_message(update, message_id,
                f"❌ Ошибка турбо-режима: {e}", 100, False)
        except Exception:
            pass
        if message_id in active_downloads:
            del active_downloads[message_id]
        return {"new_count": 0, "db_count": 0, "channel_count": 0, "error": str(e)}


async def enrich_subscribers(channel_peer, channel_id: int, update: Update, message_id: int) -> Dict[str, Any]:
    """Режим обогащения: получение bio и join_date для существующих подписчиков из БД
    
    Returns:
        Dict с ключами: enriched_count, total_needing, skipped
    """
    download_tracker = {"cancelled": False}
    active_downloads[message_id] = download_tracker
    
    try:
        # Получаем подписчиков, которым нужно обогащение
        needing_enrichment = db.get_subscribers_needing_enrichment(channel_id)
        total_needing = len(needing_enrichment)
        
        if total_needing == 0:
            await update_progress_message(update, message_id,
                "✅ Все подписчики уже обогащены!\n\nBio и даты подписки заполнены.",
                100, False)
            if message_id in active_downloads:
                del active_downloads[message_id]
            return {"enriched_count": 0, "total_needing": 0, "skipped": 0}
        
        await update_progress_message(update, message_id,
            f"📋 Обогащение данных\n\n"
            f"Подписчиков для обработки: {total_needing}\n\n"
            f"Получение bio и дат подписки...",
            5, True)
        
        enriched_count = 0
        skipped = 0
        last_update_time = datetime.now()
        
        for idx, user_record in enumerate(needing_enrichment):
            if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
                break
            
            user_id = user_record['id']
            bio = None
            join_date = None
            
            try:
                # Получаем bio
                full_user = await client(GetFullUserRequest(user_id))
                bio = getattr(full_user.full_user, 'about', None)
                
                # Получаем дату подписки
                try:
                    participant = await client(GetParticipantRequest(
                        channel=channel_peer,
                        participant=user_id
                    ))
                    if hasattr(participant.participant, 'date'):
                        join_date = participant.participant.date.isoformat()
                except Exception as e:
                    logger.debug(f"Не удалось получить дату подписки для {user_id}: {e}")
                
                # Обновляем в БД
                if bio or join_date:
                    db.update_enrichment(user_id, channel_id, bio, join_date)
                    enriched_count += 1
                else:
                    skipped += 1
                
                # Rate limiting
                await asyncio.sleep(0.3)
                
            except Exception as e:
                # Проверяем FloodWait (защита от бана)
                if "FloodWait" in str(type(e).__name__) or hasattr(e, 'seconds'):
                    wait_time = getattr(e, 'seconds', 60)
                    logger.warning(f"⚠️ FloodWait: Ждем {wait_time} секунд...")
                    await update_progress_message(update, message_id,
                        f"⏳ Telegram просит подождать {wait_time}с...\nНе закрывайте бота.",
                        progress, False)
                    await asyncio.sleep(wait_time + 2)
                    skipped += 1
                    continue
                
                logger.error(f"Ошибка обогащения пользователя {user_id}: {e}")
                skipped += 1
            
            # Обновляем прогресс
            if (idx + 1) % 20 == 0 or (datetime.now() - last_update_time).total_seconds() > 3:
                progress = min(95, int((idx + 1) / total_needing * 95))
                await update_progress_message(update, message_id,
                    f"📋 Обогащение данных\n\n"
                    f"Обработано: {idx + 1}/{total_needing}\n"
                    f"Обогащено: {enriched_count}\n"
                    f"Пропущено: {skipped}",
                    progress, True)
                last_update_time = datetime.now()
        
        # Проверка на отмену
        if message_id in active_downloads and active_downloads[message_id]["cancelled"]:
            if message_id in active_downloads:
                del active_downloads[message_id]
            return {"enriched_count": enriched_count, "total_needing": total_needing, "skipped": skipped, "cancelled": True}
        
        await update_progress_message(update, message_id,
            f"✅ Обогащение завершено!\n\n"
            f"Обработано: {total_needing}\n"
            f"Обогащено: {enriched_count}\n"
            f"Пропущено: {skipped}",
            100, False)
        
        if message_id in active_downloads:
            del active_downloads[message_id]
        
        return {"enriched_count": enriched_count, "total_needing": total_needing, "skipped": skipped, "cancelled": False}
        
    except Exception as e:
        logger.error(f"Ошибка режима обогащения: {e}")
        try:
            await update_progress_message(update, message_id,
                f"❌ Ошибка обогащения: {e}", 100, False)
        except Exception:
            pass
        if message_id in active_downloads:
            del active_downloads[message_id]
        return {"enriched_count": 0, "total_needing": 0, "skipped": 0, "error": str(e)}


async def export_partial_data(update: Update, message_id: int, channel_title: str) -> bool:
    """Экспорт частично полученных данных при отмене выгрузки"""
    if message_id not in active_downloads or not active_downloads[message_id].get("partial_data"):
        return False

    partial_subscribers = active_downloads[message_id]["partial_data"]
    if not partial_subscribers:
        return False

    csv_result = create_subscribers_csv(partial_subscribers, f"{channel_title}_partial")
    if not csv_result:
        return False

    with open(csv_result["filePath"], 'rb') as file:
        await update.effective_chat.send_document(
            document=file,
            filename=csv_result["fileName"],
            caption=f"Частичный список ({csv_result['count']} обработанных)"
        )

    if os.path.exists(csv_result["filePath"]):
        os.remove(csv_result["filePath"])

    await update.callback_query.edit_message_text(
        f"✅ Отправлен CSV с {csv_result['count']} пользователями",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
        ])
    )
    return True


def create_subscribers_csv(subscribers: List[Dict], channel_title: str) -> Dict[str, Any]:
    """Создание CSV файла с подписчиками (упрощённый формат)"""
    try:
        logger.info(f"Создание CSV файла для {len(subscribers)} подписчиков канала \"{channel_title}\"...")

        timestamp = datetime.now().isoformat().replace(':', '-').replace('.', '-')
        safe_channel_title = ''.join(c if c.isalnum() or c in ['_', '-'] else '_' for c in channel_title.lower())
        file_name = f"subs_{safe_channel_title}_{len(subscribers)}_{timestamp}.csv"
        file_path = os.path.join(DATA_DIR, file_name)

        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            # Упрощённый набор колонок
            writer.writerow([
                'ID', 'Username', 'First Name', 'Last Name', 'Phone', 
                'Bot', 'Status', 'Bio', 'Join Date'
            ])

            for user in subscribers:
                # Поддержка обоих форматов: из Telethon и из Supabase
                username = user.get('username') or user.get('username')
                first_name = user.get('firstName') or user.get('first_name') or ''
                last_name = user.get('lastName') or user.get('last_name') or ''
                is_bot = user.get('bot') or user.get('is_bot', False)
                
                writer.writerow([
                    user.get('id', ''),
                    f"@{username}" if username else '',
                    first_name,
                    last_name,
                    user.get('phone', '') or '',
                    'Да' if is_bot else 'Нет',
                    user.get('status', 'Unknown') or 'Unknown',
                    user.get('bio', '') or '',
                    user.get('join_date', '') or ''
                ])

        logger.info(f"CSV файл создан: {file_name}")
        return {"fileName": file_name, "filePath": file_path, "count": len(subscribers)}
    except Exception as e:
        logger.error(f"Ошибка создания CSV: {e}")
        return None


def create_subscribers_csv_from_db(channel_id: int, channel_title: str) -> Dict[str, Any]:
    """Создание CSV файла с подписчиками из Supabase"""
    subscribers = db.get_all_subscribers(channel_id)
    if not subscribers:
        return None
    return create_subscribers_csv(subscribers, channel_title)


# === Обработчики команд ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start"""
    user_id = update.effective_user.id

    # Проверка доступа
    if not is_user_allowed(user_id):
        await update.message.reply_text(
            '❌ У вас нет доступа к этому боту.\n\n'
            'Если вы считаете, что это ошибка, свяжитесь с администратором.'
        )
        logger.warning(f"Попытка доступа от неавторизованного пользователя: {user_id}")
        return

    keyboard = [
        [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
        [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]

    await update.message.reply_text(
        'GhostList v2.0.0 активирован! 🤖\n\n'
        'Я помогу выгрузить список подписчиков из каналов, где я являюсь администратором.\n\n'
        'Доступные команды:\n'
        '/channels - Показать список добавленных каналов\n'
        '/addchannel - Добавить канал\n'
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
        '*GhostList v2.0.0 - Помощь*\n\n'
        '*Команды:*\n'
        '/start - Начать работу с ботом\n'
        '/channels - Показать список каналов\n'
        '/addchannel - Добавить канал\n'
        '/removechannel - Удалить канал из списка\n'
        '/help - Показать это сообщение\n\n'
        '*Как использовать:*\n'
        '1. Добавьте бота администратором канала\n'
        '2. Добавьте канал командой /addchannel\n'
        '3. Используйте /channels и выберите канал\n'
        '4. ⚡ Турбо — быстрый сбор подписчиков\n'
        '5. 📋 Обогащение — добавить bio и дату\n'
        '6. 📥 Экспорт — скачать CSV\n\n'
        '*Особенности:*\n'
        '• Данные сохраняются в Supabase\n'
        '• Повторный турбо-режим добирает новых\n'
        '• Обогащение заполняет только пустые поля',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /addchannel"""
    user_id = update.effective_user.id

    if not is_user_allowed(user_id):
        await update.message.reply_text('У вас нет прав для использования этой команды.')
        return

    if context.args:
        channel_identifier = ' '.join(context.args).strip()
        status_message = await update.message.reply_text(f"Добавление канала {channel_identifier}...")
        await process_add_channel(update, status_message.message_id, channel_identifier)
    else:
        msg = await update.message.reply_text(
            'Пожалуйста, введите @username или ID канала.\n\n'
            'Пример: @channelname или -100123456789\n\n'
            'Используйте /cancel для отмены.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )
        user_contexts[update.effective_chat.id] = {"action": "add_channel", "message_id": msg.message_id}


async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /removechannel"""
    user_id = update.effective_user.id

    if not is_user_allowed(user_id):
        await update.message.reply_text('У вас нет прав для использования этой команды.')
        return

    await show_remove_channel_menu(update)


async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /channels"""
    user_id = update.effective_user.id

    if not is_user_allowed(user_id):
        await update.message.reply_text('У вас нет прав для использования этой команды.')
        return

    await show_channels_list(update)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /cancel"""
    chat_id = update.effective_chat.id

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
    if update.message.text.startswith('/'):
        return

    chat_id = update.effective_chat.id

    if chat_id in user_contexts:
        context_data = user_contexts[chat_id]

        if context_data["action"] == "add_channel":
            channel_identifier = update.message.text.strip()
            status_message = await update.message.reply_text(f"Добавление канала {channel_identifier}...")
            del user_contexts[chat_id]
            await process_add_channel(update, status_message.message_id, channel_identifier)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка колбеков от кнопок"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    message_id = query.message.message_id
    data = query.data

    if not is_user_allowed(user_id):
        await query.answer("У вас нет прав для использования этой функции.", show_alert=True)
        return

    logger.info(f"Получен callback query: {data} от пользователя {user_id}")

    if data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("❓ Помощь", callback_data="help")]
        ]
        await query.edit_message_text(
            'GhostList v2.0.0 активирован! 🤖\n\nВыберите действие из меню ниже:',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "channels_list":
        await show_channels_list(update)

    elif data == "add_channel":
        await query.edit_message_text(
            'Пожалуйста, введите @username или ID канала.\n\n'
            'Пример: @channelname или -100123456789\n\n'
            'Используйте /cancel для отмены.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )
        user_contexts[chat_id] = {"action": "add_channel", "message_id": message_id}

    elif data == "help":
        await query.edit_message_text(
            '*GhostList v2.0.0 - Помощь*\n\n'
            '*Команды:*\n'
            '/start - Начать работу с ботом\n'
            '/channels - Показать список каналов\n'
            '/addchannel - Добавить канал\n'
            '/removechannel - Удалить канал из списка\n'
            '/help - Показать это сообщение\n\n'
            '*Как использовать:*\n'
            '1. Добавьте бота администратором канала\n'
            '2. Добавьте канал командой /addchannel\n'
            '3. Используйте /channels и выберите канал\n'
            '4. ⚡ Турбо — быстрый сбор подписчиков\n'
            '5. 📋 Обогащение — добавить bio и дату\n'
            '6. 📥 Экспорт — скачать CSV\n\n'
            '*Особенности:*\n'
            '• Данные сохраняются в Supabase\n'
            '• Повторный турбо-режим добирает новых\n'
            '• Обогащение заполняет только пустые поля',
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
            ])
        )

    elif data == "remove_channel_menu":
        await show_remove_channel_menu(update)

    elif data.startswith("channel_"):
        channel_id = data.split("_")[1]
        await show_channel_actions(update, channel_id)

    elif data.startswith("parse_turbo_"):
        # Турбо-режим парсинга
        channel_id = data.split("_")[2]
        await run_turbo_parsing(update, channel_id)

    elif data.startswith("enrich_"):
        # Режим обогащения (bio + дата)
        channel_id = data.split("_")[1]
        await run_enrichment(update, channel_id)

    elif data.startswith("export_"):
        # Экспорт CSV из базы
        channel_id = data.split("_")[1]
        await run_export_csv(update, channel_id)

    elif data.startswith("delete_") or data.startswith("remove_"):
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

    elif data.startswith("cancel_download_"):
        target_message_id = int(data.split("_")[2])
        logger.info(f"Запрос на отмену выгрузки, message_id={target_message_id}")

        try:
            await cancel_download(target_message_id)
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

    elif data.startswith("export_partial_"):
        target_message_id = int(data.split("_")[2])
        channel_title = "Channel"
        try:
            if "channel_peer" in active_downloads.get(target_message_id, {}):
                channel_peer = active_downloads[target_message_id]["channel_peer"]
                channel_info = await client(GetFullChannelRequest(channel=channel_peer))
                if hasattr(channel_info, 'chats') and channel_info.chats:
                    channel_title = channel_info.chats[0].title
        except Exception as e:
            logger.error(f"Ошибка при получении названия канала: {e}")

        success = await export_partial_data(update, target_message_id, channel_title)
        if not success:
            await query.edit_message_text(
                'Нет данных для выгрузки.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Каналы", callback_data="channels_list")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
                ])
            )


# === Вспомогательные функции для UI ===

async def show_channels_list(update: Update) -> None:
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

        if query:
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for channel in channels:
        keyboard.append([InlineKeyboardButton(channel["title"], callback_data=f"channel_{channel['id']}")])

    keyboard.append([InlineKeyboardButton("🗑 Удалить канал", callback_data="remove_channel_menu")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")])

    message = 'Выберите канал для выгрузки подписчиков:'

    if query:
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_remove_channel_menu(update: Update) -> None:
    """Отображение меню удаления каналов"""
    channels = load_channels()

    if not channels:
        message = 'Список каналов пуст. Нечего удалять.'
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]]

        if update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for channel in channels:
        keyboard.append([InlineKeyboardButton(f"🗑 {channel['title']}", callback_data=f"remove_{channel['id']}")])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")])

    message = 'Выберите канал для удаления из списка:'

    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))


async def process_add_channel(update: Update, message_id: int, channel_identifier: str) -> None:
    """Обработка добавления канала"""
    global client
    try:
        if not client or not client.is_connected():
            if update.callback_query:
                await update.callback_query.edit_message_text('Подключение к Telegram API...')
            else:
                await update.message.reply_text('Подключение к Telegram API...')

            client = TelegramClient('bot_session', API_ID, API_HASH)
            await client.start(bot_token=BOT_TOKEN)

        result = await add_channel(channel_identifier)

        keyboard = [
            [InlineKeyboardButton("📋 Список каналов", callback_data="channels_list")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]
        ]

        if result["success"]:
            message = f"✅ Канал \"{result['channel']['title']}\" успешно добавлен в список!"
        else:
            message = f"❌ Ошибка: {result['error']}"
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]]

        if update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Ошибка при добавлении канала: {e}")
        error_message = 'Произошла ошибка при добавлении канала. Проверьте корректность ID/username канала.'
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]]

        if update.callback_query:
            await update.callback_query.edit_message_text(error_message, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(error_message, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_channel_actions(update: Update, channel_id: str) -> None:
    """Отображение меню действий для канала"""
    channels = load_channels()
    channel_info = next((ch for ch in channels if ch["id"] == channel_id), None)
    query = update.callback_query

    if not channel_info:
        await query.edit_message_text(
            'Канал не найден в списке.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
            ])
        )
        return

    channel_title = channel_info.get("title", "Канал")
    
    # Получаем статистику из БД
    db_count = 0
    if db.is_supabase_enabled():
        db_count = db.get_subscriber_count(int(channel_id))
    
    # Формируем статус БД
    db_status = f"💾 В базе: {db_count}" if db.is_supabase_enabled() else "⚠️ База данных не настроена"
    
    keyboard = [
        [InlineKeyboardButton("⚡ Турбо-режим", callback_data=f"parse_turbo_{channel_id}")],
        [InlineKeyboardButton("📋 Обогащение (bio + дата)", callback_data=f"enrich_{channel_id}")],
        [InlineKeyboardButton("📥 Экспорт CSV", callback_data=f"export_{channel_id}")],
        [InlineKeyboardButton("🗑 Удалить канал", callback_data=f"delete_{channel_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
    ]

    await query.edit_message_text(
        f'Канал: *{channel_title}*\n\n'
        f'{db_status}\n\n'
        '⚡ *Турбо* — быстрый сбор подписчиков\n'
        '📋 *Обогащение* — добавить bio и дату подписки\n'
        '📥 *Экспорт* — скачать CSV из базы\n\n'
        'Выберите действие:',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def get_channel_entity(update: Update, channel_id: str):
    """Получить InputPeerChannel для канала, с обновлением accessHash если нужно"""
    query = update.callback_query
    channels = load_channels()
    channel_info = next((ch for ch in channels if ch["id"] == channel_id), None)

    if not channel_info:
        await query.edit_message_text(
            'Канал не найден в списке. Попробуйте добавить его снова.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
            ])
        )
        return None, None

    # Если у канала нет accessHash, пробуем его обновить
    if not channel_info.get("accessHash"):
        try:
            username = channel_info.get("username")
            if username and username != 'Приватный канал':
                await query.edit_message_text("Получение дополнительной информации о канале...")
                entity = await client.get_entity(f"@{username}")
                if hasattr(entity, 'access_hash'):
                    channel_info["accessHash"] = str(entity.access_hash)

                    all_channels = load_channels()
                    idx = next((i for i, ch in enumerate(all_channels) if ch["id"] == channel_id), -1)
                    if idx != -1:
                        all_channels[idx]["accessHash"] = channel_info["accessHash"]
                        save_channels(all_channels)
            else:
                await query.edit_message_text(
                    'Не хватает данных о канале. Пожалуйста, добавьте канал заново через /addchannel',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                    ])
                )
                return None, None
        except Exception as e:
            logger.error(f"Не удалось получить accessHash для канала: {e}")
            await query.edit_message_text(
                'Не удалось получить доступ к каналу. Пожалуйста, добавьте его снова через /addchannel.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                ])
            )
            return None, None

    # Создаем InputPeerChannel
    if channel_info.get("accessHash"):
        channel_entity = InputPeerChannel(
            channel_id=int(channel_info["id"]),
            access_hash=int(channel_info["accessHash"])
        )
        return channel_entity, channel_info
    else:
        await query.edit_message_text(
            'Недостаточно данных для доступа к каналу. Пожалуйста, добавьте канал заново через /addchannel',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
            ])
        )
        return None, None


async def run_turbo_parsing(update: Update, channel_id: str) -> None:
    """Запуск турбо-режима парсинга (iter_participants + Supabase)"""
    query = update.callback_query

    try:
        # Проверяем Supabase
        if not db.is_supabase_enabled():
            await query.edit_message_text(
                '⚠️ База данных не настроена!\n\n'
                'Добавьте SUPABASE_URL и SUPABASE_KEY в переменные окружения.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
                ])
            )
            return

        await query.edit_message_text('⚡ Запуск турбо-режима...')

        channel_entity, channel_info = await get_channel_entity(update, channel_id)
        if not channel_entity:
            return

        channel_title = channel_info.get("title", "Канал")

        # Запускаем турбо-парсинг
        result = await get_channel_subscribers_turbo(
            channel_entity, 
            int(channel_id), 
            update, 
            query.message.message_id
        )

        if result.get("cancelled"):
            return

        if result.get("error"):
            await query.edit_message_text(
                f'❌ Ошибка: {result["error"]}',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
                ])
            )
            return

        # Показываем результат
        db_count = result.get("db_count", 0)
        channel_count = result.get("channel_count", 0)
        new_count = result.get("new_count", 0)
        
        progress_pct = int(db_count / max(channel_count, 1) * 100)
        progress_bar = '█' * (progress_pct // 5) + '░' * (20 - progress_pct // 5)

        await query.edit_message_text(
            f'✅ Турбо-режим завершён!\n\n'
            f'Канал: *{channel_title}*\n\n'
            f'📊 В канале: {channel_count}\n'
            f'💾 В базе: {db_count}\n'
            f'🆕 Добавлено: {new_count}\n\n'
            f'[{progress_bar}] {progress_pct}%\n\n'
            f'{"✅ Все подписчики собраны!" if db_count >= channel_count else "⚡ Запустите ещё раз для добора"}',
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Ещё раз", callback_data=f"parse_turbo_{channel_id}")],
                [InlineKeyboardButton("📥 Экспорт CSV", callback_data=f"export_{channel_id}")],
                [InlineKeyboardButton("⬅️ К каналу", callback_data=f"channel_{channel_id}")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка турбо-парсинга: {e}")
        await query.edit_message_text(
            f'❌ Ошибка: {e}',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
            ])
        )


async def run_enrichment(update: Update, channel_id: str) -> None:
    """Запуск режима обогащения (bio + join_date)"""
    query = update.callback_query

    try:
        # Проверяем Supabase
        if not db.is_supabase_enabled():
            await query.edit_message_text(
                '⚠️ База данных не настроена!\n\n'
                'Добавьте SUPABASE_URL и SUPABASE_KEY в переменные окружения.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
                ])
            )
            return

        await query.edit_message_text('📋 Запуск обогащения...')

        channel_entity, channel_info = await get_channel_entity(update, channel_id)
        if not channel_entity:
            return

        channel_title = channel_info.get("title", "Канал")

        # Запускаем обогащение
        result = await enrich_subscribers(
            channel_entity, 
            int(channel_id), 
            update, 
            query.message.message_id
        )

        if result.get("cancelled"):
            return

        if result.get("error"):
            await query.edit_message_text(
                f'❌ Ошибка: {result["error"]}',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
                ])
            )
            return

        # Показываем результат
        await query.edit_message_text(
            f'✅ Обогащение завершено!\n\n'
            f'Канал: *{channel_title}*\n\n'
            f'📋 Обработано: {result.get("total_needing", 0)}\n'
            f'✅ Обогащено: {result.get("enriched_count", 0)}\n'
            f'⏭ Пропущено: {result.get("skipped", 0)}',
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Экспорт CSV", callback_data=f"export_{channel_id}")],
                [InlineKeyboardButton("⬅️ К каналу", callback_data=f"channel_{channel_id}")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка обогащения: {e}")
        await query.edit_message_text(
            f'❌ Ошибка: {e}',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
            ])
        )


async def run_export_csv(update: Update, channel_id: str) -> None:
    """Экспорт CSV из Supabase"""
    query = update.callback_query

    try:
        # Проверяем Supabase
        if not db.is_supabase_enabled():
            await query.edit_message_text(
                '⚠️ База данных не настроена!\n\n'
                'Добавьте SUPABASE_URL и SUPABASE_KEY в переменные окружения.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
                ])
            )
            return

        channels = load_channels()
        channel_info = next((ch for ch in channels if ch["id"] == channel_id), None)

        if not channel_info:
            await query.edit_message_text(
                'Канал не найден в списке.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="channels_list")]
                ])
            )
            return

        channel_title = channel_info.get("title", "Канал")

        await query.edit_message_text(f'📥 Экспорт данных из базы...')

        # Создаем CSV из БД
        csv_result = create_subscribers_csv_from_db(int(channel_id), channel_title)

        if not csv_result:
            await query.edit_message_text(
                '❌ Нет данных для экспорта.\n\n'
                'Сначала запустите турбо-режим для сбора подписчиков.',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ Турбо-режим", callback_data=f"parse_turbo_{channel_id}")],
                    [InlineKeyboardButton("⬅️ К каналу", callback_data=f"channel_{channel_id}")]
                ])
            )
            return

        # Отправляем файл
        with open(csv_result["filePath"], 'rb') as file:
            await update.effective_chat.send_document(
                document=file,
                filename=csv_result["fileName"],
                caption=f"✅ Экспорт завершён!\nКанал: {channel_title}\nПодписчиков: {csv_result['count']}"
            )

        # Удаляем файл после отправки
        if os.path.exists(csv_result["filePath"]):
            os.remove(csv_result["filePath"])

        await query.edit_message_text(
            f'✅ CSV отправлен!\n\n'
            f'Канал: *{channel_title}*\n'
            f'Подписчиков: {csv_result["count"]}',
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Турбо-режим", callback_data=f"parse_turbo_{channel_id}")],
                [InlineKeyboardButton("📋 Обогащение", callback_data=f"enrich_{channel_id}")],
                [InlineKeyboardButton("⬅️ К каналу", callback_data=f"channel_{channel_id}")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка экспорта CSV: {e}")
        await query.edit_message_text(
            f'❌ Ошибка: {e}',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"channel_{channel_id}")]
            ])
        )


# === Запуск бота ===

async def main() -> None:
    """Запуск бота"""
    global client

    logger.info('GhostList v2.0.0 запускается...')

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        logger.info(f"Создана папка для данных: {DATA_DIR}")

    # Инициализация клиента MTProto
    try:
        client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
        await client.start(bot_token=BOT_TOKEN)
        logger.info('Клиент MTProto запущен')
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
        ("channels", "Показать список каналов"),
        ("addchannel", "Добавить канал"),
        ("removechannel", "Удалить канал из списка"),
        ("help", "Показать справку"),
        ("cancel", "Отменить текущую операцию")
    ]

    try:
        await application.bot.set_my_commands(bot_commands)
        logger.info("Команды бота установлены в меню")
    except Exception as e:
        logger.error(f"Ошибка при установке команд бота: {e}")

    # Запуск бота
    logger.info("Запуск бота...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    logger.info("GhostList готов к работе!")

    stop_signal = asyncio.Future()

    try:
        await stop_signal
    except asyncio.CancelledError:
        logger.info("Получен сигнал остановки...")
    finally:
        await application.stop()
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение работы бота...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")