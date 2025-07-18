import asyncio
import configparser
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta
from brands import find_car_brands
from typing import List

import telethon
# from aiohttp import web
from telethon import TelegramClient, errors
from telethon.tl.patched import MessageService
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaPoll,
    MessageMediaGeo,
    MessageMediaWebPage,
    InputMediaDice,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('channel_copier.log',encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TelegramChannelCopier:
    def __init__(self, config_file='config.ini'):
        self.config = self._load_config(config_file)
        os.makedirs("sessions", exist_ok=True)
        self.client = TelegramClient(
            'sessions/account_session',
            self.config['Telegram']['api_id'],
            self.config['Telegram']['api_hash']
        )
        self.mode = self.config.get('Settings', 'mode', fallback='standard')
        self.batch_size = int(self.config.get('Settings', 'batch_size', fallback=1))
        self.post_interval = int(self.config.get('Settings', 'post_interval', fallback=0)) * 60
        self.check_interval = int(self.config.get('Settings', 'check_interval', fallback=10)) * 60

        self.scheduled_posts = asyncio.Queue()  # Очередь для отложенных постов
        self.next_post_time = None  # Время следующего поста
        self.message_hashes = set()  # Для хранения хешей сообщений
        self.max_retries = 3  # Максимальное количество попыток повтора
        self.retry_delay = 60  # Задержка между попытками в секундах
        self.state_file = self.config.get('Settings', 'state_file', fallback='state.json')
        self.copy_history_days = int(self.config.get('Settings', 'copy_history_days', fallback=0))
        self.channel_pairs = self._parse_channel_pairs()
        self.running = False
        # self.web_port = int(self.config.get('Web', 'port', fallback=8080))
        self.state = self._load_state()
        self.media_albums = {}  # Для хранения альбомов

    def _load_config(self, config_file):
        config = configparser.ConfigParser()
        try:
            # Пробуем UTF-8 сначала
            with open(config_file, 'r', encoding='utf-8') as f:
                config.read_file(f)
        except UnicodeDecodeError:
            try:
                # Пробуем системную кодировку
                with open(config_file, 'r') as f:
                    config.read_file(f)
            except Exception as e:
                raise ValueError(f"Не могу прочитать конфиг: {e}")
        except FileNotFoundError:
            raise FileNotFoundError(f"Файл конфигурации {config_file} не найден")

        if 'Telegram' not in config:
            raise ValueError("Отсутствует секция [Telegram] с api_id и api_hash")
        return config

    def _parse_channel_pairs(self):
        pairs = []
        for section in self.config.sections():
            if not section.startswith('ChannelPair:'):
                continue
            cfg = self.config[section]
            source = cfg.get('source') or cfg.get('source_channel')
            target = cfg.get('target') or cfg.get('target_channel')
            raw = cfg.get('filter_keywords', '')
            keywords = [kw.strip() for kw in raw.split(',') if kw.strip()]
            tag = cfg.getboolean('tag', fallback=False)
            pairs.append({
                'source': source,
                'target': target,
                'filter_keywords': keywords,
                'tag': tag,
                'name': section.replace('ChannelPair:', '')
            })
            return pairs

    def _save_state(self):
        """Сохранение состояния с очередью сообщений"""
        state = {
            'last_message_ids': self.state['last_message_ids'],
            'scheduled_posts': list(self.scheduled_posts._queue),  # Сохраняем всю очередь
            'next_post_time': self.next_post_time.isoformat() if self.next_post_time else None
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _load_state(self):
        """Загрузка состояния с восстановлением очереди"""
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

                # Восстанавливаем очередь
                self.scheduled_posts = asyncio.Queue()
                for post in state.get('scheduled_posts', []):
                    self.scheduled_posts.put_nowait(post)

                # Восстанавливаем время следующего поста
                if state.get('next_post_time'):
                    self.next_post_time = datetime.fromisoformat(state['next_post_time'])

                return state
        except (FileNotFoundError, json.JSONDecodeError):
            return {'last_message_ids': {}}

    async def _check_bot_permissions(self):
        """Проверка прав с уточнением типа канала"""
        for pair in self.channel_pairs:
            try:
                # Проверяем исходный канал
                source_entity = await self.client.get_entity(pair['source'])
                if not hasattr(source_entity, 'broadcast') or not source_entity.broadcast:
                    logger.error(f"{pair['source']} не является каналом!")
                    return False

                # Проверяем целевой канал
                target_entity = await self.client.get_entity(pair['target'])
                if not hasattr(target_entity, 'broadcast'):
                    logger.error(f"{pair['target']} не является каналом!")
                    return False

                # Проверяем права на отправку
                if target_entity.restricted:
                    if target_entity.restriction_reason:
                        logger.error(f"Бот ограничен в {pair['target']}: {target_entity.restriction_reason}")
                        return False

                # Дополнительная проверка через попытку доступа
                try:
                    await self.client.send_message(target_entity, "Тест прав доступа (сообщение удалится)", silent=True)
                    await asyncio.sleep(1)
                    async for msg in self.client.iter_messages(target_entity, limit=1):
                        await msg.delete()
                except Exception as e:
                    logger.error(f"Ошибка доступа к {pair['target']}: {e}")
                    return False

            except ValueError as e:
                logger.error(f"Канал {pair['source']} или {pair['target']} не найден!")
                return False
            except Exception as e:
                logger.error(f"Ошибка проверки прав: {e}")
                return False
        return True

    def _generate_message_hash(self, message) -> str:
        """Генерация уникального хеша для сообщения"""
        content = str(message.id) + str(message.date) + (message.text or "")
        if message.media:
            if hasattr(message.media, 'document'):
                content += str(message.media.document.id)
        return hashlib.md5(content.encode()).hexdigest()


    async def _process_message_with_retry2(self, message, target, pair):
        """Обработка сообщения с автоматическим повтором при ошибках"""
        for attempt in range(self.max_retries):
            try:
                message_hash = self._generate_message_hash(message)
                if message_hash in self.message_hashes:
                    logger.info(f"Сообщение {message.id} уже было скопировано ранее (дубликат)")
                    return True

                if hasattr(message, 'grouped_id') and message.grouped_id:
                    await self._handle_album(message, target)
                else:
                    await self._copy_single_message(message, target, pair)

                self.message_hashes.add(message_hash)
                return True

            except errors.FloodWaitError as e:
                wait_time = e.seconds + 10
                logger.warning(f"Flood wait: ждём {wait_time} сек (попытка {attempt + 1})")
                await asyncio.sleep(wait_time)

            except Exception as e:
                logger.error(f"Ошибка {attempt + 1}/{self.max_retries} при обработке сообщения {message.id}: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    return False
        return False

    async def _process_message_with_retry(self, message, target, pair):
        """Обработка сообщения с автоматическим повтором при ошибках + фильтрация пустых и системных сообщений"""
        # Фильтрация системных сообщений
        from telethon.tl.patched import MessageService
        if isinstance(message, MessageService):
            logger.info(f"Пропущено системное сообщение {message.id}")
            return True

        # Фильтрация пустых сообщений (без текста и медиа)
        if not getattr(message, "text", None) and not getattr(message, "media", None):
            logger.warning(f"Сообщение {message.id} пустое, пропускаем")
            return True

        for attempt in range(self.max_retries):
            try:
                message_hash = self._generate_message_hash(message)
                if message_hash in self.message_hashes:
                    logger.info(f"Сообщение {message.id} уже было скопировано ранее (дубликат)")
                    return True

                if hasattr(message, 'grouped_id') and message.grouped_id:
                    await self._handle_album(message, target)
                else:
                    await self._copy_single_message(message, target, pair)

                self.message_hashes.add(message_hash)
                return True

            except errors.FloodWaitError as e:
                wait_time = e.seconds + 10
                logger.warning(f"Flood wait: ждём {wait_time} сек (попытка {attempt + 1})")
                await asyncio.sleep(wait_time)

            except Exception as e:
                logger.error(f"Ошибка {attempt + 1}/{self.max_retries} при обработке сообщения {message.id}: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    return False
        return False

    async def _post_scheduler(self):
        while self.running:
            try:
                if self.scheduled_posts.empty():
                    await asyncio.sleep(5)
                    continue

                now = datetime.now()
                post = await self.scheduled_posts.get()

                scheduled_time = datetime.fromisoformat(post['scheduled_time'])
                if scheduled_time > now:
                    await self.scheduled_posts.put(post)
                    await asyncio.sleep(5)
                    continue

                # Восстанавливаем объект сообщения
                message = await self._recreate_message(post['message'])
                if message:
                    success = await self._process_message_with_retry(message, post['target'])
                    if success:
                        logger.info(f"Опубликовано отложенное сообщение {message.id}")
                        self.state['last_message_ids'][post['source']] = message.id
                        self._save_state()
                    else:
                        await self.scheduled_posts.put(post)
                else:
                    logger.error(f"Не удалось восстановить сообщение {post['message']['id']}")

            except Exception as e:
                logger.error(f"Ошибка планировщика: {str(e)[:200]}...")
                await asyncio.sleep(10)

    async def _recreate_message(self, msg_data):
        """Восстановление сообщения из сохраненных данных"""
        try:
            # Здесь должна быть логика восстановления объекта сообщения
            # В зависимости от того, как вы храните данные
            return await self.client.get_messages(
                entity=msg_data.get('peer'),
                ids=msg_data['id']
            )
        except Exception as e:
            logger.error(f"Ошибка восстановления сообщения: {e}")
            return None


    async def _init_last_message_ids(self):
        for pair in self.channel_pairs:
            source = pair['source']
            if source not in self.state['last_message_ids']:
                if self.copy_history_days > 0:
                    self.state['last_message_ids'][source] = 0
                    logger.info(f"Инициализирован last_message_id=0 для {source} (режим копирования истории)")
                elif self.copy_history_days == -1:
                    self.state['last_message_ids'][source] = 0
                    logger.info(
                        f"Инициализирован last_message_id=0 для {source} (режим полного копирования всей истории)")
                else:
                    async for msg in self.client.iter_messages(source, limit=1):
                        self.state['last_message_ids'][source] = msg.id
                        logger.info(
                            f"Инициализирован last_message_id={msg.id} для {source} (режим только новых сообщений)")
                self._save_state()

    async def start(self):
        await self.client.start()
        logger.info("Клиент успешно запущен")

        # # Добавленная проверка прав
        # if not await self._check_bot_permissions():
        #     logger.error("Проверка прав доступа не пройдена. Завершение работы.")
        #     await self.stop()
        #     return

        asyncio.create_task(self._post_scheduler())  # <-- Добавьте эту строку

        await self._init_last_message_ids()

        if self.copy_history_days > 0:
            await self._copy_history()
            logger.info("Первоначальная история скопирована")

        # await self._start_web_server()
        self.running = True

        try:
            while self.running:
                try:
                    await self._check_new_messages()
                    await asyncio.sleep(self.check_interval)
                except asyncio.CancelledError:
                    logger.info("Получен запрос на завершение работы")
                    break
                except Exception as e:
                    logger.error(f"Ошибка в основном цикле: {e}")
                    await asyncio.sleep(60)
        finally:
            await self.stop()

    async def _copy_history(self):
        if self.copy_history_days <= 0 and self.copy_history_days != -1:
            return

        date_threshold = None
        if self.copy_history_days > 0:
            date_threshold = datetime.now() - timedelta(days=self.copy_history_days)
            logger.info(f"Копирование истории сообщений с {date_threshold}")
        else:
            logger.info(f"Копирование ВСЕЙ истории сообщений (с самого начала)")

        for pair in self.channel_pairs:
            source = pair['source']
            last_id = self.state['last_message_ids'].get(source, 0)

            async for message in self.client.iter_messages(
                    source,
                    offset_date=date_threshold,
                    reverse=True
            ):
                if date_threshold and message.date < date_threshold:
                    continue

                if not self._should_copy(message, pair['filter_keywords']):
                    continue

                # Замена на вызов с повтором
                success = await self._process_message_with_retry(message, pair['target'])
                if success:
                    self.state['last_message_ids'][source] = message.id
                    self._save_state()
                await asyncio.sleep(5)  # Уменьшил задержку между сообщениями

                # await self._process_message(message, pair['target'])
                # self.state['last_message_ids'][source] = message.id
                # self._save_state()
                # await asyncio.sleep(5)  # Задержка для избежания flood control

    async def _check_new_messages(self):
        for pair in self.channel_pairs:
            if not self.running:
                break

            source = pair['source']
            last_id = self.state['last_message_ids'].get(source, 0)
            logger.info(f"Проверка новых сообщений для {source} (last_id={last_id})")

            try:
                messages = []
                async for message in self.client.iter_messages(
                        source,
                        limit=self.batch_size,
                        min_id=last_id,
                        reverse=True
                ):
                    if not self.running:
                        break

                    if self._should_copy(message, pair['filter_keywords']):
                        messages.append(message)

                if not messages:
                    continue

                if self.mode == 'standard':
                    # Стандартный режим - отправка с базовой задержкой
                    for message in messages:
                        if not self.running:
                            break

                        success = await self._process_message_with_retry(message, pair['target'], pair)
                        if success:
                            self.state['last_message_ids'][source] = message.id
                            self._save_state()

                        await asyncio.sleep(1)  # Базовая задержка 1 сек между сообщениями

                else:  # Режим delayed
                    current_time = datetime.now()
                    for i, message in enumerate(messages):
                        if not self.running:
                            break

                        # Распределяем сообщения с учетом интервала (в минутах)
                        post_time = current_time + timedelta(minutes=i * (self.post_interval / len(messages)))

                        await self.scheduled_posts.put({
                            'message': message,
                            'target': pair['target'],
                            'source': source,
                            'scheduled_time': post_time.isoformat()
                        })
                        logger.info(f"Сообщение {message.id} запланировано на {post_time}")

            except Exception as e:
                logger.error(f"Ошибка в канале {source}: {str(e)[:200]}...")
                await asyncio.sleep(10)

    def _should_copy(self, message, keywords: List[str]) -> bool:
        if not keywords:
            return True

        if not getattr(message, 'text', None):
            logger.debug(f"Сообщение {message.id} без текста - пропускаем фильтрацию")
            return True

        text = message.text.lower()
        keywords_lower = [k.lower() for k in keywords]

        if any(k in text for k in keywords_lower):
            return True

        logger.debug(f"Сообщение {message.id} не прошло фильтр по ключевым словам")
        return False


    async def _process_message(self, message, target):
        """Основной метод обработки сообщения"""
        try:
            # Игнорируем сообщения без текста и без медиа
            if not message.text and not message.media:
                logger.warning(f"Сообщение {message.id} пустое, пропускаем")
                return False

            # Игнорируем системные сообщения (например, добавление пользователя в чат)
            if isinstance(message, telethon.tl.patched.MessageService):
                logger.warning(f"Сообщение {message.id} является системным, пропускаем")
                return False

            # Обработка сообщений с медиа и текстом
            return await self._process_message_with_retry(message, target, pair)

        except Exception as e:
            logger.error(f"Ошибка копирования {message.id}: {e}")
            return False





    async def _handle_album(self, message, target):
        """Обработка медиа-альбомов как оригинальных сообщений"""
        album_id = message.grouped_id
        if album_id not in self.media_albums:
            self.media_albums[album_id] = {
                'messages': [],
                'target': target,
                'last_update': datetime.now()
            }

        self.media_albums[album_id]['messages'].append(message)
        self.media_albums[album_id]['last_update'] = datetime.now()

        # Ждем 2 секунды на случай, если придут другие части альбома
        await asyncio.sleep(2)

        # Если с момента последнего обновления прошло больше 2 секунд - обрабатываем
        if (datetime.now() - self.media_albums[album_id]['last_update']).total_seconds() > 2:
            await self._send_album(album_id)
            del self.media_albums[album_id]

    async def _send_album(self, album_id):
        """Создание нового альбома в целевом канале"""
        album = self.media_albums.get(album_id)
        if not album or len(album['messages']) == 0:
            return

        messages = sorted(album['messages'], key=lambda m: m.id)
        target = album['target']

        try:
            # Собираем медиафайлы и подписи для создания нового альбома
            media_input = []
            captions = []

            for msg in messages:
                # Для каждого медиа определяем правильный тип вложения
                media = getattr(msg, 'media', None)

                # Обрабатываем разные типы медиа
                if isinstance(media, MessageMediaPhoto):
                    media_input.append(media)
                elif isinstance(media, MessageMediaDocument):
                    if self._is_voice_message(media):
                        # Голосовые сообщения как голосовые ноты
                        media_input.append(InputMediaDice(media.document.id))
                    elif self._is_sticker(media):
                        # Стикеры как стикеры
                        media_input.append(media.document)
                    else:
                        # Обычные документы
                        media_input.append(media)
                else:
                    # Другие типы медиа
                    media_input.append(media)

                # Сохраняем текст сообщения
                captions.append(msg.text if msg.text else None)

            # Создаем новый альбом в целевом канале
            await self.client.send_file(
                target,
                media_input,
                captions=captions,
                parse_mode='html'
            )
            logger.info(f"Создан новый альбом из {len(messages)} сообщений в {target}")
        except Exception as e:
            logger.error(f"Ошибка создания альбома: {e}")
            # При ошибке пробуем переслать оригинальный альбом
            try:
                await self.client.forward_messages(
                    target,
                    [msg.id for msg in messages],
                    messages[0].peer
                )
                logger.info(f"Переслан альбом как fallback в {target}")
            except Exception as e2:
                logger.error(f"Ошибка пересылки альбома: {e2}")

    async def _copy_single_message2(self, message, target, pair):
        """Копирование одиночного сообщения с поддержкой тегов брендов"""
        try:
            # Подготовка текста
            text = html.escape(message.text) if message.text else None

            # Добавление хештегов по брендам, если включен параметр tag
            if text and pair.get('tag'):
                found_brands = find_car_brands(message.text)
                if found_brands:
                    hashtags = ' '.join(f"#{b.replace(' ', '_')}" for b in found_brands[:3])
                    text += f"\n\n🔍 {hashtags}"

            media = getattr(message, 'media', None)

            # Обработка голосовых сообщений
            if self._is_voice_message(media):
                await self.client.send_file(
                    target,
                    media,
                    voice_note=True,
                    caption=text,
                    parse_mode='html'
                )
                return

            # Обработка видеосообщений (кружки)
            if self._is_video_note(media):
                await self.client.send_file(
                    target,
                    media,
                    video_note=True,
                    caption=text,
                    parse_mode='html'
                )
                return

            # Если есть медиа
            if media:
                if isinstance(media, MessageMediaPhoto):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaDocument):
                    if self._is_voice_message(media):
                        await self.client.send_file(
                            target,
                            media,
                            voice_note=True,
                            caption=text,
                            parse_mode='html'
                        )
                    elif self._is_sticker(media):
                        await self.client.send_file(
                            target,
                            media.document,
                            parse_mode='html'
                        )
                    else:
                        await self.client.send_file(
                            target,
                            media,
                            caption=text,
                            parse_mode='html',
                            attributes=media.document.attributes
                        )
                elif isinstance(media, MessageMediaPoll):
                    await self.client.send_poll(
                        target,
                        question=media.poll.question,
                        options=[o.text for o in media.poll.answers],
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaGeo):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaWebPage):
                    await self.client.send_message(
                        target,
                        text,
                        parse_mode='html',
                        link_preview=True
                    )
                else:
                    await self.client.send_message(
                        target,
                        text or "",
                        parse_mode='html'
                    )
            else:
                # Текстовое сообщение
                await self.client.send_message(
                    target,
                    text,
                    parse_mode='html'
                )

            logger.info(f"Скопировано сообщение {message.id} в {target}")
            if text and pair.get('tag') and found_brands:
                logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")

        except Exception as e:
            logger.error(f"Ошибка копирования сообщения {message.id}: {e}")
            try:
                await self.client.forward_messages(target, message)
                logger.info(f"Переслано сообщение {message.id} в {target} как fallback")
            except Exception as e2:
                logger.error(f"Ошибка пересылки {message.id}: {e2}")

    async def _copy_single_message3(self, message, target, pair):
        """Копирование одиночного сообщения с поддержкой тегов брендов и логированием"""
        try:
            text = html.escape(message.text) if message.text else None
            found_brands = []
            hashtags = ""

            # Добавление тегов по брендам, если включено
            if text and pair.get('tag'):
                found_brands = find_car_brands(message.text)
                if found_brands:
                    hashtags = ' '.join(f"#{b.replace(' ', '_')}" for b in found_brands[:3])
                    text += f"\n\n🔍 {hashtags}"

            media = getattr(message, 'media', None)

            # Обработка голосового сообщения
            if self._is_voice_message(media):
                await self.client.send_file(
                    target,
                    media,
                    voice_note=True,
                    caption=text,
                    parse_mode='html'
                )
                logger.info(f"Скопировано сообщение {message.id} в {target}")
                if found_brands:
                    logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")
                return

            # Обработка видеосообщения (кружок)
            if self._is_video_note(media):
                await self.client.send_file(
                    target,
                    media,
                    video_note=True,
                    caption=text,
                    parse_mode='html'
                )
                logger.info(f"Скопировано сообщение {message.id} в {target}")
                if found_brands:
                    logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")
                return

            # Обработка медиа
            if media:
                if isinstance(media, MessageMediaPhoto):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaDocument):
                    if self._is_voice_message(media):
                        await self.client.send_file(
                            target,
                            media,
                            voice_note=True,
                            caption=text,
                            parse_mode='html'
                        )
                    elif self._is_sticker(media):
                        await self.client.send_file(
                            target,
                            media.document,
                            parse_mode='html'
                        )
                    else:
                        await self.client.send_file(
                            target,
                            media,
                            caption=text,
                            parse_mode='html',
                            attributes=media.document.attributes
                        )
                elif isinstance(media, MessageMediaPoll):
                    await self.client.send_poll(
                        target,
                        question=media.poll.question,
                        options=[o.text for o in media.poll.answers],
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaGeo):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaWebPage):
                    await self.client.send_message(
                        target,
                        text,
                        parse_mode='html',
                        link_preview=True
                    )
                else:
                    await self.client.send_message(
                        target,
                        text or "",
                        parse_mode='html'
                    )
            else:
                # Текстовое сообщение
                await self.client.send_message(
                    target,
                    text,
                    parse_mode='html'
                )

            logger.info(f"Скопировано сообщение {message.id} в {target}")
            if found_brands:
                logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")

        except Exception as e:
            logger.error(f"Ошибка копирования сообщения {message.id}: {e}")
            try:
                await self.client.forward_messages(target, message)
                logger.info(f"Переслано сообщение {message.id} в {target} как fallback")
            except Exception as e2:
                logger.error(f"Ошибка пересылки {message.id}: {e2}")

    async def _copy_single_message(self, message, target, pair):
        """Копирование одиночного сообщения с поддержкой тегов брендов и логированием + фильтрация пустых"""
        try:
            # Фильтрация пустых сообщений
            if not getattr(message, "text", None) and not getattr(message, "media", None):
                logger.warning(f"Сообщение {message.id} пустое, пропускаем")
                return False

            # Фильтрация системных сообщений на всякий случай
            from telethon.tl.patched import MessageService
            if isinstance(message, MessageService):
                logger.warning(f"Сообщение {message.id} является системным, пропускаем")
                return False

            text = html.escape(message.text) if message.text else None
            found_brands = []
            hashtags = ""

            # Добавление тегов по брендам, если включено
            if text and pair.get('tag'):
                found_brands = find_car_brands(message.text)
                if found_brands:
                    hashtags = ' '.join(f"#{b.replace(' ', '_')}" for b in found_brands[:3])
                    text += f"\n\n🔍 {hashtags}"

            media = getattr(message, 'media', None)

            # Обработка голосового сообщения
            if self._is_voice_message(media):
                await self.client.send_file(target, media, voice_note=True, caption=text, parse_mode='html')
                logger.info(f"Скопировано сообщение {message.id} в {target}")
                if found_brands:
                    logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")
                return True

            # Обработка видеосообщения (кружок)
            if self._is_video_note(media):
                await self.client.send_file(target, media, video_note=True, caption=text, parse_mode='html')
                logger.info(f"Скопировано сообщение {message.id} в {target}")
                if found_brands:
                    logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")
                return True

            # Обработка медиа
            if media:
                if isinstance(media, MessageMediaPhoto):
                    await self.client.send_file(target, media, caption=text, parse_mode='html')
                elif isinstance(media, MessageMediaDocument):
                    if self._is_voice_message(media):
                        await self.client.send_file(target, media, voice_note=True, caption=text, parse_mode='html')
                    elif self._is_sticker(media):
                        await self.client.send_file(target, media.document, parse_mode='html')
                    else:
                        await self.client.send_file(target, media, caption=text, parse_mode='html',
                                                    attributes=media.document.attributes)
                elif isinstance(media, MessageMediaPoll):
                    await self.client.send_poll(target, question=media.poll.question,
                                                options=[o.text for o in media.poll.answers],
                                                caption=text, parse_mode='html')
                elif isinstance(media, MessageMediaGeo):
                    await self.client.send_file(target, media, caption=text, parse_mode='html')
                elif isinstance(media, MessageMediaWebPage):
                    await self.client.send_message(target, text, parse_mode='html', link_preview=True)
                else:
                    await self.client.send_message(target, text or "", parse_mode='html')
            else:
                # Текстовое сообщение
                await self.client.send_message(target, text, parse_mode='html')

            logger.info(f"Скопировано сообщение {message.id} в {target}")
            if found_brands:
                logger.info(f"Добавлены теги для сообщения {message.id}: {hashtags}")
            return True

        except Exception as e:
            logger.error(f"Ошибка копирования сообщения {message.id}: {e}")
            try:
                await self.client.forward_messages(target, message)
                logger.info(f"Переслано сообщение {message.id} в {target} как fallback")
            except Exception as e2:
                logger.error(f"Ошибка пересылки {message.id}: {e2}")
            return False

    async def _handle_large_video(self, message, target):
        """Обработка больших видеофайлов (>20MB)"""
        try:
            # Вариант 1: Разделить на части (просто пересылаем оригинал)
            # В реальности Telegram автоматически разбивает большие файлы при загрузке
            # await self.client.forward_messages(target, message)
            logger.info(
                f"Большой видеофайл {message.id} переслан как есть (размер: {message.media.document.size / 1024 / 1024:.2f}MB)")

            # Вариант 2: Можно добавить сжатие видео, но это требует дополнительных библиотек
            # и обработки файла перед отправкой
            await self._compress_and_send_video(message, target)
        except Exception as e:
            logger.error(f"Ошибка обработки большого видеофайла {message.id}: {e}")

    async def _handle_large_album(self, album_id):
        """Обработка альбома с большими видео (>20MB)"""
        album = self.media_albums.get(album_id)
        if not album or len(album['messages']) == 0:
            return

        target = album['target']
        messages = sorted(album['messages'], key=lambda m: m.id)

        try:
            # Отправляем каждое сообщение отдельно (если видео - с обработкой)
            for msg in messages:
                if isinstance(msg.media, MessageMediaDocument) and self._is_video_message(msg.media):
                    if msg.media.document.size > 20 * 1024 * 1024:
                        await self._handle_large_video(msg, target)  # Используем существующий метод
                    else:
                        await self.client.forward_messages(target, msg)
                else:
                    await self.client.forward_messages(target, msg)

            logger.warning(f"Альбом {album_id} обработан с разделением из-за большого видео")
        except Exception as e:
            logger.error(f"Ошибка обработки большого альбома {album_id}: {e}")

    async def _compress_and_send_video(self, message, target):
        """Сжатие и отправка видео (требует ffmpeg)"""
        try:
            # Скачиваем видео
            video_path = await self.client.download_media(message, file='temp_video.mp4')

            # Сжимаем видео с помощью ffmpeg (примерные параметры)
            compressed_path = 'temp_video_compressed.mp4'
            import subprocess
            subprocess.run([
                'ffmpeg', '-i', video_path,
                '-vcodec', 'libx264', '-crf', '28',
                '-preset', 'fast', '-acodec', 'copy',
                compressed_path
            ], check=True)

            # Отправляем сжатое видео
            await self.client.send_file(
                target,
                compressed_path,
                caption=message.text,
                parse_mode='html'
            )

            # Удаляем временные файлы
            os.unlink(video_path)
            os.unlink(compressed_path)

            logger.info(f"Видео {message.id} сжато и отправлено")
        except Exception as e:
            logger.error(f"Ошибка сжатия видео {message.id}: {e}")
            # Если сжатие не удалось, просто пересылаем оригинал
            await self.client.forward_messages(target, message)

    def _is_video_message(self, media) -> bool:
        """Проверяет, является ли медиа видео (включая видеосообщения)"""
        if not isinstance(media, MessageMediaDocument):
            return False

        # Проверяем атрибуты документа
        for attr in media.document.attributes:
            if hasattr(attr, 'video') or hasattr(attr, 'round_message'):
                return True

        # Дополнительная проверка по MIME-типу
        mime_type = media.document.mime_type
        if mime_type and 'video' in mime_type.lower():
            return True

        return False

    def _is_video_note(self, media) -> bool:
        """Проверяет, является ли медиа видеосообщением (кружок)"""
        if not isinstance(media, MessageMediaDocument):
            return False
        for attr in media.document.attributes:
            if hasattr(attr, 'round_message'):
                return True
        return False

    def _is_voice_message(self, media) -> bool:
        """Проверяет, является ли медиа голосовым сообщением"""
        if not isinstance(media, MessageMediaDocument):
            return False
        for attr in media.document.attributes:
            if hasattr(attr, 'voice'):
                return True
        return False

    def _is_sticker(self, media) -> bool:
        """Проверяет, является ли медиа стикером"""
        if not isinstance(media, MessageMediaDocument):
            return False
        for attr in media.document.attributes:
            if hasattr(attr, 'sticker'):
                return True
        return False

    # async def _start_web_server(self):
    #     app = web.Application()
    #     app.add_routes([
    #         web.get('/status', self._handle_status),
    #         web.post('/add_pair', self._handle_add),
    #         web.post('/remove_pair', self._handle_remove),
    #         web.get('/pairs', self._handle_get),
    #     ])
    #     runner = web.AppRunner(app)
    #     await runner.setup()
    #     site = web.TCPSite(runner, '0.0.0.0', self.web_port)
    #     await site.start()
    #     logger.info(f"Web UI на http://localhost:{self.web_port}")
    #
    # async def _handle_status(self, request):
    #     return web.json_response({
    #         'running': self.running,
    #         'pairs': self.channel_pairs,
    #         'last_ids': self.state['last_message_ids'],
    #         'time': datetime.now().isoformat()
    #     })
    #
    # async def _handle_add(self, request):
    #     data = await request.json()
    #     self.channel_pairs.append({
    #         'source': data['source'],
    #         'target': data['target'],
    #         'filter_keywords': data.get('filter_keywords', '').split(','),
    #         'name': data.get('name', f'pair_{len(self.channel_pairs)}')
    #     })
    #     self._save_state()
    #     return web.json_response({'status': 'added'})
    #
    # async def _handle_remove(self, request):
    #     data = await request.json()
    #     self.channel_pairs = [p for p in self.channel_pairs if p['source'] != data['source']]
    #     self._save_state()
    #     return web.json_response({'status': 'removed'})
    #
    # async def _handle_get(self, request):
    #     return web.json_response({'pairs': self.channel_pairs})

    async def stop(self):
        self.running = False
        try:
            # Дожидаемся завершения текущих операций
            await asyncio.sleep(1)
            # Сохраняем состояние
            self._save_state()
            # Отключаем клиента
            await self.client.disconnect()
            logger.info("Клиент остановлен")
        except Exception as e:
            logger.error(f"Ошибка при отключении клиента: {e}")



async def main():
    copier = TelegramChannelCopier()
    try:
        await copier.start()
    except KeyboardInterrupt:
        logger.info("Получено прерывание от пользователя")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
    finally:
        await copier.stop()
        logger.info("Программа завершена")


if __name__ == '__main__':
    asyncio.run(main())