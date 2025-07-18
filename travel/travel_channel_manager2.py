import asyncio
import configparser
import json
import logging
import random
import re
from io import BytesIO
from typing import Dict, List
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI
from telethon import TelegramClient, events
from telethon.tl.types import Message, MessageMediaPhoto



def backoff(max_retries=5, base_delay=1.0, factor=2.0, jitter=0.1):
    import functools
    import random
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
                    sleep = delay + random.uniform(-jitter, jitter)
                    logger.info(f"Waiting {sleep:.2f}s before retry {attempt + 1}")
                    await asyncio.sleep(sleep)
                    delay *= factor
        return wrapper
    return decorator


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
    CONTENT_STYLES = {
        'adventure': 'авантюрный стиль с акцентом на экстрим и активный отдых',
        'luxury': 'премиум стиль для роскошных путешествий',
        'budget': 'бюджетные путешествия и лайфхаки',
        'cultural': 'культурно-познавательный контент',
        'nature': 'экотуризм и природные достопримечательности'
    }

    def __init__(self, config_file: str = 'config.ini'):
        logger.info('Инициализация TravelChannelManager')
        self.config = self._load_config(config_file)
        self._validate_config()

        self.client = TelegramClient(
            session='travel_channel_session',
            api_id=self.config['Telegram']['api_id'],
            api_hash=self.config['Telegram']['api_hash'],
        )
        self.style = self.config.get('Style', 'type', fallback='adventure')
        self.openai_client = OpenAI(api_key=self.config['OpenAI']['api_key'])
        self.check_interval = int(self.config.get('Settings', 'check_interval', fallback=10))

        # parse channel pairs: each must have source, target, keywords
        self.channel_pairs = self._parse_channel_pairs()
        self.analytics = self._load_analytics()
        self._lock = asyncio.Lock()

    def _load_config(self, path: str) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        return config

    def _validate_config(self):
        sections = {'Telegram': ['api_id', 'api_hash'], 'OpenAI': ['api_key']}
        for sect, keys in sections.items():
            if sect not in self.config:
                raise ValueError(f'Отсутствует секция [{sect}]')
            for k in keys:
                if not self.config[sect].get(k):
                    raise ValueError(f'Отсутствует параметр {k} в секции [{sect}]')

    def _parse_channel_pairs(self) -> List[Dict]:
        """
        Считывает из конфига все секции ChannelPair:*
        и возвращает список словарей вида:
        {
          'source': <строка>,
          'target': <строка>,
          'keywords': [<список ключевых слов>],
          'exclude': [<список исключений>],
          'min_quality': <int>,
          'enabled': <bool>,
          'source_id': None,
          'target_id': None
        }
        """
        pairs = []
        for section in self.config.sections():
            if not section.startswith('ChannelPair:'):
                continue
            cfg = self.config[section]

            source = cfg.get('source')
            target = cfg.get('target')
            if not source or not target:
                logger.warning(f"Пропуск {section}: нужны source и target")
                continue

            # keywords → список, пустой список если не задано
            keywords = [w.strip() for w in cfg.get('keywords', '').split(',') if w.strip()]

            # exclude → список
            exclude = [e.strip() for e in cfg.get('exclude', '').split(',') if e.strip()]

            # min_quality → int, по умолчанию 0
            try:
                min_quality = cfg.getint('min_quality')
            except Exception:
                min_quality = 0

            # enabled → bool, по умолчанию True
            enabled = cfg.getboolean('enabled', True)

            pairs.append({
                'source': source,
                'target': target,
                'keywords': keywords,
                'exclude': exclude,
                'min_quality': min_quality,
                'enabled': enabled,
                'source_id': None,
                'target_id': None
            })

        if not pairs:
            raise ValueError("Не найдено ни одной секции ChannelPair:* в конфиге")
        return pairs

    def _load_analytics(self) -> Dict:
        try:
            with open('analytics.json','r',encoding='utf-8') as f:
                return json.load(f)
        except:
            return {'last_ids':{}, 'posts':{}}

    def _save_analytics(self):
        with open('analytics.json','w',encoding='utf-8') as f:
            json.dump(self.analytics, f, ensure_ascii=False, indent=2)

    def _get_last_id(self, source):
        return self.analytics.get('last_ids',{}).get(source,0)

    async def _set_last_id(self, source,msg_id:int):
        async with self._lock:
            self.analytics.setdefault('last_ids',{})[source]=msg_id
            self._save_analytics()

    async def start(self):
        await self.client.start()
        logger.info('Телеграм клиент запущен')

        # resolve channel IDs
        for p in self.channel_pairs:
            try:
                s_ent = await self.client.get_entity(p['source'])
                t_ent = await self.client.get_entity(p['target'])
                p['source_id']=s_ent.id
                p['target_id']=t_ent.id
                logger.info(f"Mapped {p['source']}->{p['target']} IDs {s_ent.id}->{t_ent.id}")
            except Exception as e:
                p['enabled']=False
                logger.error(f"Не удалось разрешить {p['source']} или {p['target']}: {e}")

        self._setup_handlers()
        # await self.client.run_until_disconnected()
        try:
            await self.client.run_until_disconnected()
        except asyncio.CancelledError:
            logger.info("run_until_disconnected получило CancelledError — завершаемся спокойно")


    def _setup_handlers(self):
        @self.client.on(events.NewMessage)
        async def handler(evt):
            msg=evt.message
            for p in self.channel_pairs:
                if not p['enabled'] or p['source_id']!=msg.chat_id:
                    continue
                last=self._get_last_id(p['source'])
                if msg.id<=last:
                    return
                if self._should_process(msg,p):
                    await self._process_message(msg,p)
                    await self._set_last_id(p['source'],msg.id)

    # def _should_process(self,msg,pair):
    #     txt=(msg.text or '').lower()
    #     if not any(k.lower() in txt for k in pair['keywords']):
    #         return False
    #     if any(e.lower() in txt for e in pair['exclude']):
    #         return False
    #     return True

    def _should_process(self, msg: Message, pair: Dict) -> bool:
        """
        Решает, нужно ли обрабатывать и копировать сообщение.
        - Если keywords пустой → пропускаем фильтр по ключам (разрешаем ВСЕ сообщения)
        - Иначе проверяем, что хотя бы одно ключевое слово есть в тексте.
        - Если exclude не пустой → проверяем, что ни одно исключённое слово не встречается.
        """
        text = (msg.text or "").lower()

        # фильтр по ключевым словам
        if not pair['keywords']:
            keywords_ok = True
        else:
            keywords_ok = any(k.lower() in text for k in pair['keywords'])
        if not keywords_ok:
            logger.debug(f"Пропущено (нет ключевых слов): {msg.id}")
            return False

        # фильтр по исключениям
        if pair['exclude']:
            if any(e.lower() in text for e in pair['exclude']):
                logger.debug(f"Пропущено (есть исключённое слово): {msg.id}")
                return False

        return True

    async def _process_message(self, msg: Message, pair: Dict) -> None:
        """
        1) Проверяет качество (min_quality)
        2) Переписывает текст ИИ
        3) Заменяет ссылки
        4) Добавляет хэштеги
        5) Отправляет текст или медиа
        6) Сохраняет в аналитике
        """
        text = msg.text or ''
        min_q = pair.get('min_quality', 0)

        # Шаг 1: качество
        if min_q:
            ok = await self._assess_quality(text, min_q)
            if not ok:
                logger.info(f"Пропущено {msg.id}: качество < {min_q}")
                return

        # Шаг 2: ИИ-перепись
        text = await self._rewrite_text(text)
        # Шаг 3: партнёрские ссылки
        text = self._replace_links(text)
        # Шаг 4: хэштеги
        text = self._add_hashtags(text)

        # Шаг 5: отправка
        if msg.media and isinstance(msg.media, MessageMediaPhoto):
            await self._send_media(msg, text, pair['target_id'])
        else:
            await self._send_text(text, pair['target_id'])

        # Шаг 6: аналитика
        self.analytics['posts'][f"{pair['source']}_{msg.id}"] = {
            'time': datetime.now().isoformat()
        }
        self._save_analytics()

    @backoff()
    async def _rewrite_text(self,text:str)->str:
        if not text.strip(): return text
        resp=await self.openai_client.chat.completions.create(
            model='gpt-4-turbo',
            messages=[
                {'role':'system','content':(
                    f"Ты эксперт по путешествиям в стиле: {self.CONTENT_STYLES.get(self.style)}."
                    "Перепиши текст, сохраняя смысл.")},
                {'role':'user','content':text}
            ],
            temperature=0.7, max_tokens=2000
        )
        return resp.choices[0].message.content.strip()

    def _replace_links(self,text:str)->str:
        aff={r'https?://(?:www\.)?booking\.com\S+':self.config['Affiliate'].get('booking_com','')}
        for pat,rep in aff.items():
            if rep: text=re.sub(pat,rep+'?utm_source=telegram',text)
        return text

    async def _assess_quality(self, text: str, threshold: int) -> bool:
        """
        Оценивает текст от 1 до 5 через OpenAI
        и возвращает True, если score >= threshold.
        """
        try:
            resp = await self.openai_client.chat.completions.create(
                model='gpt-4-turbo',
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'Ты эксперт по путешествиям. '
                            'Оцени это сообщение от 1 до 5 по полезности для туристов.'
                        )
                    },
                    {'role': 'user', 'content': text}
                ],
                temperature=0.0,
                max_tokens=1
            )
            score = int(resp.choices[0].message.content.strip())
            logger.info(f"Оценка качества: {score} (порог {threshold})")
            return score >= threshold
        except Exception as e:
            logger.error(f"Ошибка оценки качества: {e}")
            # если не получилось оценить — пропускаем проверку
            return True

    def _add_hashtags(self,text:str)->str:
        base=['#Путешествия','#Туризм']
        extra=random.sample(['#Отдых','#Мир','#Туризм'],2)
        return f"{text}\n\n"+' '.join(base+extra)

    @backoff()
    async def _send_text(self,text,target_id:int):
        import html
        safe=html.escape(text)
        await self.client.send_message(entity=target_id,message=safe,link_preview=False)

    @backoff()
    async def _send_media(self,msg,caption:str,target_id:int):
        buf=await self.client.download_media(msg.media,file=BytesIO())
        buf=self._add_watermark(buf)
        await self.client.send_file(entity=target_id,file=buf,caption=caption[:1024],link_preview=False)

    def _add_watermark(self,buf:BytesIO)->BytesIO:
        try:
            img=Image.open(buf).convert('RGBA')
            wm=Image.new('RGBA',img.size)
            draw=ImageDraw.Draw(wm)
            font=ImageFont.load_default()
            txt=self.config['Branding'].get('watermark','@travel')
            w,h=draw.textsize(txt,font)
            draw.text((img.width-w-10,img.height-h-10),txt,font=font,fill=(255,255,255,128))
            out=BytesIO()
            Image.alpha_composite(img,wm).save(out,format='PNG')
            out.seek(0)
            return out
        except Exception as e:
            logger.error(f'Watermark error: {e}')
            buf.seek(0)
            return buf


async def main():
    mgr=TravelChannelManager()
    try:
        await mgr.start()
    except KeyboardInterrupt:
        logger.info("Получен KeyboardInterrupt — останавливаем менеджер")
        # pass
    finally:
        await mgr.client.disconnect()
        logger.info("Программа завершена")


if __name__=='__main__':
    asyncio.run(main())
