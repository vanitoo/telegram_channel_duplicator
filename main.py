import asyncio
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaPoll,
    MessageMediaGeo,
    MessageMediaWebPage,
    DocumentAttributeFilename,
)
import configparser
import logging
import json
import html
from aiohttp import web
import os
from typing import Optional, List
import colorama


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('channel_copier.log'),
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
        self.state_file = self.config.get('Settings', 'state_file', fallback='state.json')
        self.check_interval = int(self.config.get('Settings', 'check_interval', fallback=10))
        self.copy_history_days = int(self.config.get('Settings', 'copy_history_days', fallback=0))
        self.channel_pairs = self._parse_channel_pairs()
        self.running = False
        self.web_port = int(self.config.get('Web', 'port', fallback=8080))
        self.state = self._load_state()
        self.media_albums = {}  # Для хранения альбомов

    def _load_config(self, config_file):
        config = configparser.ConfigParser()
        config.read(config_file)
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
            pairs.append({
                'source': source,
                'target': target,
                'filter_keywords': keywords,
                'name': section.replace('ChannelPair:', '')
            })
        return pairs

    def _load_state(self):
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                if 'last_message_ids' not in state:
                    state['last_message_ids'] = {}
                return state
        except (FileNotFoundError, json.JSONDecodeError):
            return {'last_message_ids': {}}

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            logger.error(f"Ошибка сохранения состояния: {e}")

    async def _init_last_message_ids2(self):
        for pair in self.channel_pairs:
            source = pair['source']
            if source not in self.state['last_message_ids']:
                if self.copy_history_days > 0:
                    self.state['last_message_ids'][source] = 0
                    logger.info(f"Инициализирован last_message_id=0 для {source} (режим копирования истории)")
                else:
                    async for msg in self.client.iter_messages(source, limit=1):
                        self.state['last_message_ids'][source] = msg.id
                        logger.info(f"Инициализирован last_message_id={msg.id} для {source}")
                self._save_state()

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

        await self._init_last_message_ids()

        if self.copy_history_days > 0:
            await self._copy_history()
            logger.info("Первоначальная история скопирована")

        await self._start_web_server()
        self.running = True
        while self.running:
            try:
                await self._check_new_messages()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {e}")
                await asyncio.sleep(60)


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

                await self._process_message(message, pair['target'])
                self.state['last_message_ids'][source] = message.id
                self._save_state()
                await asyncio.sleep(5)  # Задержка для избежания flood control


    async def _check_new_messages(self):

        for pair in self.channel_pairs:
            source = pair['source']
            last_id = self.state['last_message_ids'].get(source, 0)
            logger.info(f"Проверка новых сообщений для {source} (last_id={last_id})")

            try:
                async for message in self.client.iter_messages(
                        source,
                        min_id=last_id,
                        reverse=True
                ):
                    if not self._should_copy(message, pair['filter_keywords']):
                        continue

                    logger.info(f"Обработка сообщения ID={message.id}, Дата={message.date}")
                    await self._process_message(message, pair['target'])
                    self.state['last_message_ids'][source] = message.id
                    self._save_state()
                    await asyncio.sleep(50*60)  # 50 мин
            except Exception as e:
                logger.error(f"Ошибка при проверке новых сообщений в {source}: {e}")

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
        """Основной метод обработки сообщения с поддержкой альбомов"""
        if hasattr(message, 'grouped_id') and message.grouped_id:
            await self._handle_album(message, target)
        else:
            await self._copy_single_message(message, target)

    async def _handle_album2(self, message, target):
        """Обработка медиа-альбомов"""
        album_id = message.grouped_id
        if album_id not in self.media_albums:
            self.media_albums[album_id] = {
                'messages': [],
                'target': target,
                'last_update': datetime.now()
            }

        logger.debug(f"Media info: {self.media_albums}")  # <-- Вставьте здесь


        self.media_albums[album_id]['messages'].append(message)
        self.media_albums[album_id]['last_update'] = datetime.now()

        # Ждем 2 секунды на случай, если придут другие части альбома
        await asyncio.sleep(2)

        # Если с момента последнего обновления прошло больше 2 секунд - обрабатываем
        if (datetime.now() - self.media_albums[album_id]['last_update']).total_seconds() > 2:
            await self._send_album(album_id)
            del self.media_albums[album_id]

    async def _handle_album(self, message, target):
        """Обработка медиа-альбомов с проверкой размера видео"""
        album_id = message.grouped_id
        if album_id not in self.media_albums:
            self.media_albums[album_id] = {
                'messages': [],
                'target': target,
                'last_update': datetime.now(),
                'has_large_video': False  # Флаг для больших видео
            }

        # Проверяем размер видео (если это видео)
        if isinstance(message.media, MessageMediaDocument):
            if self._is_video_message(message.media):
                if message.media.document.size > 20 * 1024 * 1024:  # 20MB
                    self.media_albums[album_id]['has_large_video'] = True
                    logger.warning(
                        f"Обнаружено большое видео в альбоме {album_id} ({message.media.document.size / 1024 / 1024:.2f}MB)")

        self.media_albums[album_id]['messages'].append(message)
        self.media_albums[album_id]['last_update'] = datetime.now()

        # Ждем 2 секунды на случай, если придут другие части альбома
        await asyncio.sleep(2)

        # Если с момента последнего обновления прошло больше 2 секунд - обрабатываем
        if (datetime.now() - self.media_albums[album_id]['last_update']).total_seconds() > 2:
            if self.media_albums[album_id]['has_large_video']:
                await self._handle_large_album(album_id)  # Отдельная обработка больших видео
            else:
                await self._send_album(album_id)  # Стандартная отправка
            del self.media_albums[album_id]

    async def _send_album(self, album_id):
        """Отправка собранного альбома"""
        album = self.media_albums.get(album_id)
        if not album or len(album['messages']) == 0:
            return

        messages = sorted(album['messages'], key=lambda m: m.id)
        target = album['target']

        try:
            # Собираем медиафайлы и подписи
            media = []
            captions = []

            for msg in messages:
                media.append(msg.media)
                captions.append(html.escape(msg.text) if msg.text else None)

            # Отправляем альбом
            await self.client.send_file(
                target,
                media,
                captions=captions,
                parse_mode='html'
            )
            logger.info(f"Отправлен альбом из {len(messages)} сообщений в {target}")
        except Exception as e:
            logger.error(f"Ошибка отправки альбома: {e}")

    async def _copy_single_message2(self, message, target):
        """Копирование одиночного сообщения"""
        text = html.escape(message.text) if message.text else None
        media = getattr(message, 'media', None)

        try:
            if media:
                if isinstance(media, MessageMediaPhoto):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaDocument):
                    # Проверяем, является ли документ голосовым сообщением или стикером
                    if self._is_voice_message(media):
                        await self.client.forward_messages(target, message)
                    elif self._is_sticker(media):
                        await self.client.forward_messages(target, message)
                    else:
                        await self.client.send_file(
                            target,
                            media,
                            caption=text,
                            parse_mode='html',
                            attributes=media.document.attributes
                        )
                elif isinstance(media, MessageMediaPoll):
                    await self.client.forward_messages(target, message)
                elif isinstance(media, MessageMediaGeo):
                    await self.client.forward_messages(target, message)
                elif isinstance(media, MessageMediaWebPage):
                    await self.client.send_message(
                        target,
                        message.text,
                        parse_mode='html',
                        link_preview=True
                    )
                else:
                    await self.client.forward_messages(target, message)
            else:
                await self.client.send_message(
                    target,
                    text,
                    parse_mode='html'
                )
            logger.info(f"Скопировано сообщение {message.id} в {target}")
        except Exception as e:
            logger.error(f"Ошибка копирования {message.id}: {e}")

    async def _copy_single_message3(self, message, target):
        """Копирование одиночного сообщения"""
        text = html.escape(message.text) if message.text else None
        media = getattr(message, 'media', None)

        try:
            if media:
                if isinstance(media, MessageMediaPhoto):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaDocument):
                    # Проверяем размер видеофайла
                    if media.document.size > 20 * 1024 * 1024:  # 20MB
                        print(media.document.size)
                        await self._handle_large_video(message, target)
                    else:
                        # Обработка обычных документов
                        if self._is_voice_message(media):
                            await self.client.forward_messages(target, message)
                        elif self._is_sticker(media):
                            await self.client.forward_messages(target, message)
                        else:
                            await self.client.send_file(
                                target,
                                media,
                                caption=text,
                                parse_mode='html',
                                attributes=media.document.attributes
                            )
                elif isinstance(media, MessageMediaPoll):
                    await self.client.forward_messages(target, message)
                elif isinstance(media, MessageMediaGeo):
                    await self.client.forward_messages(target, message)
                elif isinstance(media, MessageMediaWebPage):
                    await self.client.send_message(
                        target,
                        message.text,
                        parse_mode='html',
                        link_preview=True
                    )
                else:
                    await self.client.forward_messages(target, message)
            else:
                await self.client.send_message(
                    target,
                    text,
                    parse_mode='html'
                )
            logger.info(f"Скопировано сообщение {message.id} в {target}")
        except Exception as e:
            logger.error(f"Ошибка копирования {message.id}: {e}")

    async def _copy_single_message(self, message, target):
        """Копирование одиночного сообщения"""
        text = html.escape(message.text) if message.text else None
        media = getattr(message, 'media', None)

        logger.debug(text)
        # Логируем информацию о медиа для отладки
        if media:
            logger.debug(f"Media info: {media.to_dict()}")  # <-- Вставьте здесь

        try:
            if media:
                if isinstance(media, MessageMediaPhoto):
                    await self.client.send_file(
                        target,
                        media,
                        caption=text,
                        parse_mode='html'
                    )
                elif isinstance(media, MessageMediaDocument):
                    # Проверяем, является ли документ видео
                    if self._is_video_message(media):
                        if media.document.size > 20 * 1024 * 1024:  # 20MB
                            await self._handle_large_video(message, target)
                        else:
                            await self.client.forward_messages(target, message)
                    # Обработка голосовых сообщений и стикеров
                    elif self._is_voice_message(media):
                        await self.client.forward_messages(target, message)
                    elif self._is_sticker(media):
                        await self.client.forward_messages(target, message)
                    else:
                        # Обычные документы
                        await self.client.send_file(
                            target,
                            media,
                            caption=text,
                            parse_mode='html',
                            attributes=media.document.attributes
                        )
                elif isinstance(media, MessageMediaPoll):
                    await self.client.forward_messages(target, message)
                elif isinstance(media, MessageMediaGeo):
                    await self.client.forward_messages(target, message)
                elif isinstance(media, MessageMediaWebPage):
                    await self.client.send_message(
                        target,
                        message.text,
                        parse_mode='html',
                        link_preview=True
                    )
                else:
                    await self.client.forward_messages(target, message)
            else:
                await self.client.send_message(
                    target,
                    text,
                    parse_mode='html'
                )
            logger.info(f"Скопировано сообщение {message.id} в {target}")
        except Exception as e:
            logger.error(f"Ошибка копирования {message.id}: {e}")

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

    async def _start_web_server(self):
        app = web.Application()
        app.add_routes([
            web.get('/status', self._handle_status),
            web.post('/add_pair', self._handle_add),
            web.post('/remove_pair', self._handle_remove),
            web.get('/pairs', self._handle_get),
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.web_port)
        await site.start()
        logger.info(f"Web UI на http://localhost:{self.web_port}")

    async def _handle_status(self, request):
        return web.json_response({
            'running': self.running,
            'pairs': self.channel_pairs,
            'last_ids': self.state['last_message_ids'],
            'time': datetime.now().isoformat()
        })

    async def _handle_add(self, request):
        data = await request.json()
        self.channel_pairs.append({
            'source': data['source'],
            'target': data['target'],
            'filter_keywords': data.get('filter_keywords', '').split(','),
            'name': data.get('name', f'pair_{len(self.channel_pairs)}')
        })
        self._save_state()
        return web.json_response({'status': 'added'})

    async def _handle_remove(self, request):
        data = await request.json()
        self.channel_pairs = [p for p in self.channel_pairs if p['source'] != data['source']]
        self._save_state()
        return web.json_response({'status': 'removed'})

    async def _handle_get(self, request):
        return web.json_response({'pairs': self.channel_pairs})

    async def stop(self):
        self.running = False
        await self.client.disconnect()
        logger.info("Клиент остановлен")


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