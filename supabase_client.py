"""
Supabase клиент для хранения подписчиков каналов
"""
import os
import logging
from typing import List, Dict, Set, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Ленивая загрузка supabase
_supabase_client = None


def get_supabase_client():
    """Получить клиент Supabase (ленивая инициализация)"""
    global _supabase_client
    
    if _supabase_client is not None:
        return _supabase_client
    
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    
    if not supabase_url or not supabase_key:
        logger.warning("SUPABASE_URL или SUPABASE_KEY не настроены. База данных отключена.")
        return None
    
    try:
        from supabase import create_client, Client
        _supabase_client = create_client(supabase_url, supabase_key)
        logger.info("Supabase клиент инициализирован")
        return _supabase_client
    except Exception as e:
        logger.error(f"Ошибка инициализации Supabase: {e}")
        return None


def is_supabase_enabled() -> bool:
    """Проверить, включена ли интеграция с Supabase"""
    return get_supabase_client() is not None


def get_subscriber_count(channel_id: int) -> int:
    """Получить количество подписчиков канала в БД"""
    client = get_supabase_client()
    if not client:
        return 0
    
    try:
        result = client.table('subscribers').select('id', count='exact').eq('channel_id', channel_id).execute()
        return result.count or 0
    except Exception as e:
        logger.error(f"Ошибка получения количества подписчиков: {e}")
        return 0


def get_existing_ids(channel_id: int) -> Set[int]:
    """Получить множество ID существующих подписчиков"""
    client = get_supabase_client()
    if not client:
        return set()
    
    try:
        # Получаем все ID пачками по 1000
        all_ids = set()
        offset = 0
        limit = 1000
        
        while True:
            result = client.table('subscribers').select('id').eq('channel_id', channel_id).range(offset, offset + limit - 1).execute()
            
            if not result.data:
                break
            
            for row in result.data:
                all_ids.add(row['id'])
            
            if len(result.data) < limit:
                break
            
            offset += limit
        
        return all_ids
    except Exception as e:
        logger.error(f"Ошибка получения существующих ID: {e}")
        return set()


def upsert_subscribers(subscribers: List[Dict], channel_id: int) -> int:
    """Добавить или обновить подписчиков в БД. Возвращает количество добавленных."""
    client = get_supabase_client()
    if not client or not subscribers:
        logger.warning(f"Upsert пропущен: клиент={client is not None}, записей={len(subscribers) if subscribers else 0}")
        return 0
    
    try:
        # Подготавливаем данные для вставки
        now = datetime.utcnow().isoformat()
        records = []
        
        for sub in subscribers:
            record = {
                'id': sub['id'],
                'channel_id': channel_id,
                'username': sub.get('username'),
                'first_name': sub.get('firstName'),
                'last_name': sub.get('lastName'),
                'phone': sub.get('phone'),
                'is_bot': sub.get('bot', False),
                'status': sub.get('status'),
                'bio': sub.get('bio'),
                'join_date': sub.get('join_date'),
                'updated_at': now
            }
            records.append(record)
        
        # Upsert пачками по 500
        inserted_count = 0
        batch_size = 500
        
        logger.info(f"Начинаем upsert {len(records)} записей для канала {channel_id}")
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            try:
                result = client.table('subscribers').upsert(
                    batch,
                    on_conflict='id, channel_id'
                ).execute()
                
                # Логируем результат
                if hasattr(result, 'data'):
                    inserted_count += len(batch) # Считаем отправленные, так как upsert может не вернуть данные
                else:
                    logger.warning(f"Supabase upsert не вернул данные, но и не упал. Батч {i//batch_size + 1}")
                    inserted_count += len(batch)
                    
            except Exception as batch_err:
                logger.error(f"Ошибка сохранения пачки {i}: {batch_err}")
                if hasattr(batch_err, 'code'):
                    logger.error(f"Код ошибки Supabase: {batch_err.code}")
                if hasattr(batch_err, 'details'):
                    logger.error(f"Детали ошибки: {batch_err.details}")
        
        logger.info(f"Upsert завершен. Всего обработано: {inserted_count}")
        return inserted_count
        
    except Exception as e:
        logger.error(f"Критическая ошибка upsert подписчиков: {e}", exc_info=True)
        return 0


def get_subscribers_needing_enrichment(channel_id: int) -> List[Dict]:
    """Получить подписчиков, у которых нет bio или join_date"""
    client = get_supabase_client()
    if not client:
        return []
    
    try:
        # Получаем подписчиков где bio или join_date пустые
        all_needing = []
        offset = 0
        limit = 500
        
        while True:
            result = client.table('subscribers').select('id, username, first_name, last_name').eq('channel_id', channel_id).or_('bio.is.null,join_date.is.null').range(offset, offset + limit - 1).execute()
            
            if not result.data:
                break
            
            all_needing.extend(result.data)
            
            if len(result.data) < limit:
                break
            
            offset += limit
        
        return all_needing
    except Exception as e:
        logger.error(f"Ошибка получения подписчиков для обогащения: {e}")
        return []


def update_enrichment(user_id: int, channel_id: int, bio: Optional[str] = None, join_date: Optional[str] = None) -> bool:
    """Обновить bio и/или join_date для подписчика"""
    client = get_supabase_client()
    if not client:
        return False
    
    try:
        update_data = {'updated_at': datetime.utcnow().isoformat()}
        
        if bio is not None:
            update_data['bio'] = bio
        if join_date is not None:
            update_data['join_date'] = join_date
        
        client.table('subscribers').update(update_data).eq('id', user_id).eq('channel_id', channel_id).execute()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления обогащения для {user_id}: {e}")
        return False


def get_all_subscribers(channel_id: int) -> List[Dict]:
    """Получить всех подписчиков канала из БД для экспорта"""
    client = get_supabase_client()
    if not client:
        return []
    
    try:
        all_subscribers = []
        offset = 0
        limit = 1000
        
        while True:
            result = client.table('subscribers').select('*').eq('channel_id', channel_id).order('id').range(offset, offset + limit - 1).execute()
            
            if not result.data:
                break
            
            all_subscribers.extend(result.data)
            
            if len(result.data) < limit:
                break
            
            offset += limit
        
        logger.info(f"Получено {len(all_subscribers)} подписчиков из БД для канала {channel_id}")
        return all_subscribers
    except Exception as e:
        logger.error(f"Ошибка получения всех подписчиков: {e}")
        return []


def delete_channel_subscribers(channel_id: int) -> bool:
    """Удалить всех подписчиков канала из БД"""
    client = get_supabase_client()
    if not client:
        return False
    
    try:
        client.table('subscribers').delete().eq('channel_id', channel_id).execute()
        logger.info(f"Удалены все подписчики канала {channel_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления подписчиков канала: {e}")
        return False

def get_processed_queries(channel_id: int) -> Set[str]:
    """Получить обработанные поисковые запросы за последние 24 часа"""
    client = get_supabase_client()
    if not client:
        return set()
    
    try:
        from datetime import timedelta
        yesterday = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        
        # Получаем все запросы, созданные за последние 24 часа
        processed = set()
        offset = 0
        limit = 1000
        
        while True:
            result = client.table('search_history')\
                .select('query')\
                .eq('channel_id', channel_id)\
                .gte('created_at', yesterday)\
                .range(offset, offset + limit - 1)\
                .execute()
            
            if not result.data:
                break
            
            for row in result.data:
                processed.add(row['query'])
            
            if len(result.data) < limit:
                break
            
            offset += limit
            
        logger.info(f"Загружено {len(processed)} обработанных запросов для канала {channel_id}")
        return processed
    except Exception as e:
        logger.error(f"Ошибка получения истории поиска: {e}")
        return set()


def add_processed_query(channel_id: int, query: str) -> bool:
    """Сохранить обработанный поисковый запрос (или обновить время)"""
    client = get_supabase_client()
    if not client:
        return False
    
    try:
        # Используем upsert: если такой запрос уже был когда-то, обновится created_at
        client.table('search_history').upsert({
            'channel_id': channel_id,
            'query': query,
            'created_at': datetime.utcnow().isoformat()
        }, on_conflict='channel_id, query').execute()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения истории поиска '{query}': {e}")
        return False
