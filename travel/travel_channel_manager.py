#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import configparser
import json
import logging
import random
import re
import sys
from datetime import datetime
from io import BytesIO
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont
from telethon import TelegramClient, events
from telethon.tl.types import Message, MessageMediaPhoto
from openai import OpenAI

import asyncio
import random
import functools

def backoff(max_retries=5, base_delay=1.0, factor=2.0, jitter=0.1):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"{fn.__name__} error (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        logger.error(f"{fn.__name__} failed after {attempt} attempts")
                        raise
                    # экспоненциальная задержка с небольшим джиттером
                    sleep = delay + random.uniform(-jitter, jitter)
                    logger.info(f"Ждем {sleep:.2f}s перед retry {attempt + 1}")
                    await asyncio.sleep(sleep)
                    delay *= factor
        return wrapper
    return decorator



# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

def setup_logger() -> logging.Logger:
    logger = logging.getLogger('TravelChannelManager')
    logger.setLevel(logging.DEBUG)
    fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    datefmt = '%Y-%m-%d %H:%M:%S'

    fh = logging.FileHandler('travel_channel.log', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt, datefmt))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

logger = setup_logger()


class TravelChannelManager:
    """Менеджер копирования и обработки сообщений между каналами"""

    CONTENT_STYLES = {
        "adventure": "авантюрный стиль с акцентом на экстрим и активный отдых",
        "luxury": "премиум стиль для роскошных путешествий",
        "budget": "бюджетные путешествия и лайфхаки",
        "cultural": "культурно-познавательный контент",
        "nature": "экотуризм и природные достопримечательности"
    }

    def __init__(self, config_file: str = 'config.ini'):
        logger.info("Инициализация TravelChannelManager")
        self.config = self._load_config(config_file)
        self._validate_config()

        self.client = TelegramClient(
            session='travel_channel_session',
            api_id=self.config['Telegram']['api_id'],
            api_hash=self.config['Telegram']['api_hash'],
            connection_retries=3,
            timeout=30
        )

        self.style = self.config.get('Style', 'type', fallback='adventure')
        self.openai_client = OpenAI(api_key=self.config['OpenAI']['api_key'])
        self.check_interval = int(self.config.get('Settings', 'check_interval', fallback=10))

        self.channel_pairs = self._parse_channel_pairs()
        self.analytics = self._load_analytics()
        self._lock = asyncio.Lock()
        self.running = False
        logger.info("Менеджер каналов успешно инициализирован")

    def _load_config(self, path: str) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        return config

    def _validate_config(self) -> None:
        required = {
            'Telegram': ['api_id', 'api_hash'],
            'OpenAI': ['api_key'],
            'Channels': ['target_channel']
        }
        for section, keys in required.items():
            if section not in self.config:
                raise ValueError(f"Отсутствует секция [{section}]")
            for key in keys:
                if not self.config[section].get(key):
                    raise ValueError(f"Отсутствует параметр '{key}' в секции [{section}]")

    def _parse_channel_pairs(self) -> List[Dict]:
        pairs = []
        default_target = self.config['Channels']['target_channel']
        for section in self.config.sections():
            if not section.startswith('ChannelPair:'):
                continue
            cfg = self.config[section]
            if not cfg.get('source') or not cfg.get('keywords'):
                continue
            pairs.append({
                'source': cfg['source'],
                'target': cfg.get('target', default_target),
                'keywords': [k.strip() for k in cfg['keywords'].split(',') if k.strip()],
                'exclude': [e.strip() for e in cfg.get('exclude', '').split(',') if e.strip()],
                'enabled': cfg.getboolean('enabled', True),
                'source_id': None,
                'target_id': None
            })
        if not pairs:
            logger.warning("Не найдено ни одной пары каналов для мониторинга!")
        return pairs

    def _load_analytics(self) -> Dict:
        try:
            with open('analytics.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {'last_ids': {}, 'posts': {}}

    def _save_analytics(self) -> None:
        with open('analytics.json', 'w', encoding='utf-8') as f:
            json.dump(self.analytics, f, ensure_ascii=False, indent=2)

    def _get_last_id(self, source: str) -> int:
        return self.analytics.get('last_ids', {}).get(source, 0)

    async def _set_last_id(self, source: str, message_id: int) -> None:
        async with self._lock:
            self.analytics.setdefault('last_ids', {})[source] = message_id
            self._save_analytics()

    async def start(self) -> None:
        await self.client.start()
        logger.info("Телеграм клиент запущен и авторизован")

        # Разрешение имён каналов в ID
        for pair in self.channel_pairs:
            try:
                src_entity = await self.client.get_entity(pair['source'])
                tgt_entity = await self.client.get_entity(pair['target'])
                pair['source_id'] = src_entity.id
                pair['target_id'] = tgt_entity.id
                logger.info(f"Сопоставление каналов: {pair['source']} (ID {src_entity.id}) -> {pair['target']} (ID {tgt_entity.id})")
            except Exception as e:
                pair['enabled'] = False
                logger.error(f"Не удалось разрешить канал {pair['source']} или {pair['target']}: {e}")

        self._setup_handlers()
        self.running = True
        await self.client.run_until_disconnected()

    def _setup_handlers(self) -> None:
        @self.client.on(events.NewMessage)
        async def handler(evt):
            msg = evt.message
            for pair in self.channel_pairs:
                if not pair['enabled']:
                    continue
                if pair['source_id'] is None or msg.chat_id != pair['source_id']:
                    continue
                last_id = self._get_last_id(pair['source'])
                if msg.id <= last_id:
                    return
                if self._should_process(msg, pair):
                    await self._process_message(msg, pair)
                    await self._set_last_id(pair['source'], msg.id)

    def _should_process(self, msg: Message, pair: Dict) -> bool:
        text = (msg.text or "").lower()
        if not any(k.lower() in text for k in pair['keywords']):
            logger.debug(f"Пропущено (нет ключевых слов): {msg.id}")
            return False
        if any(e.lower() in text for e in pair['exclude']):
            logger.debug(f"Пропущено (исключено): {msg.id}")
            return False
        return True

    async def _process_message(self, msg: Message, pair: Dict) -> None:
        logger.info(f"Обработка сообщения {msg.id} из {pair['source']}")
        text = msg.text or ""
        text = await self._rewrite_text(text)
        text = self._replace_links(text)
        text = self._add_hashtags(text)

        # Экранируем HTML
        safe_text = html.escape(text)

        if msg.media and isinstance(msg.media, MessageMediaPhoto):
            await self._send_media(msg, safe_text, pair['target_id'])
        else:
            await self._send_text(safe_text, pair['target_id'])

        self.analytics['posts'][f"{pair['source']}_{msg.id}"] = {'time': datetime.now().isoformat()}
        self._save_analytics()

    @backoff()
    async def _rewrite_text(self, text: str) -> str:
        if not text.strip():
            return text
        try:
            resp = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role":"system","content":(
                        f"Ты эксперт по путешествиям в стиле: {self.CONTENT_STYLES.get(self.style)}. Перепиши текст, сохраняя смысл."
                    )},
                    {"role":"user","content": text}
                ], temperature=0.7, max_tokens=2000
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Ошибка ИИ: {e}")
            return text

    def _replace_links(self, text: str) -> str:
        affiliates = {
            r'https?://(?:www\.)?booking\.com\S+': self.config['Affiliate'].get('booking_com', ''),
            r'https?://(?:www\.)?airbnb\.\S+': self.config['Affiliate'].get('airbnb', ''),
        }
        for pat, rep in affiliates.items():
            if rep:
                text = re.sub(pat, rep + '?utm_source=telegram', text)
        return text

    def _add_hashtags(self, text: str) -> str:
        base = {
            "adventure": ["#Приключения","#Экстрим"],
            "luxury": ["#Роскошь"],
        }.get(self.style, ["#Путешествия"])
        extra = random.sample(["#Отдых","#Мир","#Туризм"], 2)
        return f"{text}\n\n" + ' '.join(base + extra)

    async def _send_text(self, text: str, target_id: int) -> None:
        await self.client.send_message(entity=target_id, message=text, link_preview=False)

    async def _send_media(self, msg: Message, caption: str, target_id: int) -> None:
        buf = await self.client.download_media(msg.media, file=BytesIO())
        buf = self._add_watermark(buf)
        await self.client.send_file(entity=target_id, file=buf, caption=caption[:1024], link_preview=False)

    def _add_watermark(self, buf: BytesIO) -> BytesIO:
        try:
            img = Image.open(buf).convert('RGBA')
            watermark = Image.new('RGBA', img.size)
            draw = ImageDraw.Draw(watermark)
            font = ImageFont.load_default()
            text = self.config['Branding'].get('watermark', '@travel')
            w, h = draw.textsize(text, font)
            pos = (img.width - w - 10, img.height - h - 10)
            draw.text(pos, text, font=font, fill=(255,255,255,128))
            out = BytesIO()
            Image.alpha_composite(img, watermark).save(out, format='PNG')
            out.seek(0)
            return out
        except Exception as e:
            logger.error(f"Watermark error: {e}")
            buf.seek(0)
            return buf

    async def stop(self) -> None:
        self.running = False
        await self.client.disconnect()
        logger.info("Клиент отключен")


async def main():
    mgr = TravelChannelManager()
    try:
        await mgr.start()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt: стоп")
    finally:
        await mgr.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        sys.exit(1)
