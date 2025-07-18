import asyncio
import configparser
import json
import logging
import re
import random
import requests
import time
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps
import hashlib

from telethon import TelegramClient, events, Button
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from openai import OpenAI
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import pandas as pd

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('travel_channel_manager.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TravelChannelManager:
    TRAVEL_STYLES = {
        "adventure": "авантюрный путешественник, ищущий экстрима и новых ощущений",
        "luxury": "эксперт по роскошным путешествиям с изысканным вкусом",
        "budget": "бюджетный путешественник, знающий все лайфхаки",
        "cultural": "знаток культуры и традиций разных стран",
        "nature": "любитель дикой природы и экотуризма"
    }

    def __init__(self, config_file='config.ini'):
        self.config = self._load_config(config_file)
        self.client = TelegramClient(
            'travel_channel_session',
            self.config['Telegram']['api_id'],
            self.config['Telegram']['api_hash']
        )
        self.style = self.config.get('Style', 'type', fallback='adventure')
        self.openai_client = OpenAI(api_key=self.config['OpenAI']['api_key'])
        self.analytics_data = self._load_analytics()
        self.channel_pairs = self._parse_channel_pairs()
        self.running = False
        self.last_analytic_report = None

    def _load_config(self, config_file):
        """Загрузка конфигурации из файла"""
        config = configparser.ConfigParser()
        config.read(config_file)

        # Проверка обязательных параметров
        required_sections = ['Telegram', 'OpenAI', 'Affiliate', 'Channels']
        for section in required_sections:
            if section not in config:
                raise ValueError(f"Отсутствует обязательная секция {section}")

        return config

    def _parse_channel_pairs(self):
        """Парсинг пар каналов из конфига"""
        pairs = []
        for section in self.config.sections():
            if section.startswith('ChannelPair:'):
                pair_config = self.config[section]
                pairs.append({
                    'source': pair_config['source'],
                    'target': self.config['Channels']['target_channel'],
                    'keywords': [kw.strip() for kw in pair_config['keywords'].split(',')],
                    'exclude': [ex.strip() for ex in pair_config.get('exclude', '').split(',') if ex],
                    'min_quality': int(pair_config.get('min_quality', 3))
                })
        return pairs

    def _load_analytics(self):
        """Загрузка аналитических данных"""
        try:
            with open('analytics.json', 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'posts': {},
                'link_clicks': {},
                'conversions': {},
                'follower_growth': [],
                'best_times': []
            }

    def _save_analytics(self):
        """Сохранение аналитических данных"""
        with open('analytics.json', 'w') as f:
            json.dump(self.analytics_data, f)

    async def start(self):
        """Запуск системы"""
        await self.client.start()
        logger.info("Telegram клиент успешно запущен")

        # Запуск обработчиков
        self.client.add_event_handler(self._handle_new_message, events.NewMessage)

        # Запуск периодических задач
        self.running = True
        asyncio.create_task(self._periodic_tasks())

        logger.info("Система управления каналом путешествий запущена")
        await self.client.run_until_disconnected()

    async def _periodic_tasks(self):
        """Периодические задачи системы"""
        while self.running:
            try:
                # Отчет каждый день в 10:00
                now = datetime.now()
                if now.hour == 10 and now.minute == 0:
                    await self._send_daily_report()

                # Сохранение аналитики каждые 6 часов
                if now.hour % 6 == 0 and now.minute == 0:
                    self._save_analytics()

                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Ошибка в периодических задачах: {str(e)}")
                await asyncio.sleep(300))

                async

                def _handle_new_message(self, event):
            """Обработка новых сообщений в мониторимых каналах"""
            for pair in self.channel_pairs:
                if event.chat.username == pair['source'] or str(event.chat.id) == pair['source']:
                    if self._should_process_message(event.message, pair):
                        await self._process_and_forward(event.message, pair)

        def _should_process_message(self, message, pair):
            """Определение, нужно ли обрабатывать сообщение"""
            text = message.text or ""
            text_lower = text.lower()

            # Проверка ключевых слов
            if not any(kw.lower() in text_lower for kw in pair['keywords']):
                return False

            # Проверка исключающих слов
            if any(ex.lower() in text_lower for ex in pair['exclude']):
                return False

            # Проверка качества (если есть рейтинг)
            if hasattr(message, 'rating') and message.rating < pair['min_quality']:
                return False

            return True

        async def _process_and_forward(self, message, pair):
            """Обработка и пересылка сообщения"""
            try:
                # Обработка текста
                original_text = message.text or ""

                # 1. Рерайт через ИИ
                rewritten_text = self._rewrite_with_ai(original_text)

                # 2. Замена ссылок
                processed_text = self._replace_affiliate_links(rewritten_text)

                # 3. Добавление хэштегов
                final_text = self._add_hashtags(processed_text)

                # 4. Обработка медиа
                if message.media:
                    media_file = await self.client.download_media(message.media, file=BytesIO())

                    # Добавление водяного знака
                    if isinstance(message.media, MessageMediaPhoto):
                        watermarked_image = self._add_watermark(media_file)
                        await self._send_photo(pair['target'], watermarked_image, final_text)
                    else:
                        await self.client.send_file(pair['target'], media_file, caption=final_text)
                else:
                    await self.client.send_message(pair['target'], final_text)

                # Сохранение аналитики
                post_id = f"{message.chat_id}_{message.id}"
                self.analytics_data['posts'][post_id] = {
                    'source': pair['source'],
                    'original_text': original_text,
                    'final_text': final_text,
                    'time': datetime.now().isoformat(),
                    'link_clicks': 0,
                    'conversions': 0
                }

                logger.info(f"Сообщение обработано и отправлено в {pair['target']}")

            except Exception as e:
                logger.error(f"Ошибка обработки сообщения: {str(e)}")

        def _rewrite_with_ai(self, text):
            """Рерайт текста через OpenAI с учетом стиля"""
            if not text.strip():
                return text

            style_description = self.TRAVEL_STYLES.get(self.style, "")

            response = self.openai_client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Ты эксперт по путешествиям в стиле: {style_description}. "
                            "Перепиши текст, сохраняя основной смысл, но добавляя уникальность. "
                            "Используй эмодзи, сделай текст более живым и личным. "
                            "Добавь интересные детали или аналогии где это уместно."
                        )
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.8,
                max_tokens=1000
            )

            return response.choices[0].message.content.strip()

        def _replace_affiliate_links(self, text):
            """Замена ссылок на партнерские"""
            # Поиск и замена стандартных ссылок
            replacements = {
                r'https?://(www\.)?booking\.com\S+': self.config['Affiliate']['booking_com'],
                r'https?://(www\.)?airbnb\.\S+': self.config['Affiliate']['airbnb'],
                r'https?://(www\.)?tripadvisor\.\S+': self.config['Affiliate']['tripadvisor'],
                r'https?://(www\.)?getyourguide\.\S+': self.config['Affiliate']['getyourguide'],
                r'https?://(www\.)?skyscanner\.\S+': self.config['Affiliate']['skyscanner']
            }

            for pattern, replacement in replacements.items():
                if replacement:
                    text = re.sub(pattern, replacement + "?utm_source=telegram&utm_medium=travel_channel", text)

            return text

        def _add_hashtags(self, text):
            """Добавление релевантных хэштегов"""
            base_hashtags = {
                "adventure": ["#Приключения", "#Экстрим", "#ИсследоватьМир"],
                "luxury": ["#Роскошь", "#VIPпутешествия", "#ПремиумТуризм"],
                "budget": ["#БюджетныеПутешествия", "#ЭкономичныйТуризм", "#ПутешествияДёшево"],
                "cultural": ["#Культура", "#Традиции", "#ИсторическиеМеста"],
                "nature": ["#Природа", "#Экотуризм", "#НациональныеПарки"]
            }

            hashtags = base_hashtags.get(self.style, ["#Путешествия", "#Туризм"])

            # Добавляем 3 случайных дополнительных хэштега
            additional = [
                "#Отдых", "#Открытия", "#Странники", "#МирПутешествий",
                "#Турист", "#Достопримечательности", "#НовыеГоризонты"
            ]
            hashtags.extend(random.sample(additional, 3))

            return text + "\n\n" + " ".join(hashtags)

        def _add_watermark(self, image_data):
            """Добавление водяного знака на изображение"""
            try:
                img = Image.open(image_data)

                # Создаем прозрачный слой для водяного знака
                watermark = Image.new("RGBA", img.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(watermark)

                # Настройки шрифта
                try:
                    font = ImageFont.truetype("arial.ttf", int(img.width * 0.03))
                except:
                    font = ImageFont.load_default()

                text = self.config.get('Branding', 'watermark', fallback='@travel_channel')
                text_bbox = draw.textbbox((0, 0), text, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]

                # Позиция в правом нижнем углу
                position = (img.width - text_width - 20, img.height - text_height - 20)

                # Рисуем текст с тенью
                shadow_position = (position[0] + 2, position[1] + 2)
                draw.text(shadow_position, text, font=font, fill=(0, 0, 0, 128))
                draw.text(position, text, font=font, fill=(255, 255, 255, 192))

                # Объединяем с оригинальным изображением
                watermarked = Image.alpha_composite(
                    img.convert("RGBA"),
                    watermark
                )

                # Сохраняем в BytesIO
                output = BytesIO()
                watermarked.save(output, format='PNG')
                output.seek(0)

                return output

            except Exception as e:
                logger.error(f"Ошибка добавления водяного знака: {str(e)}")
                return image_data

        async def _send_photo(self, target, image_data, caption):
            """Отправка фото с водяным знаком"""
            image_data.seek(0)
            await self.client.send_file(target, image_data, caption=caption)

        async def _send_daily_report(self):
            """Отправка ежедневного аналитического отчета"""
            report = self._generate_daily_report()
            chart_path = self._generate_analytics_chart()

            try:
                await self.client.send_message(
                    self.config['Channels']['target_channel'],
                    report
                )

                if chart_path:
                    await self.client.send_file(
                        self.config['Channels']['target_channel'],
                        chart_path,
                        caption="📈 Статистика активности за последнюю неделю"
                    )

                logger.info("Ежедневный отчет отправлен")

            except Exception as e:
                logger.error(f"Ошибка отправки отчета: {str(e)}")

        def _generate_daily_report(self):
            """Генерация текста ежедневного отчета"""
            # Статистика за последние 7 дней
            week_ago = datetime.now() - timedelta(days=7)

            posts_count = 0
            total_clicks = 0
            conversions = 0

            for post_id, data in self.analytics_data['posts'].items():
                post_time = datetime.fromisoformat(data['time'])
                if post_time > week_ago:
                    posts_count += 1
                    total_clicks += data.get('link_clicks', 0)
                    conversions += data.get('conversions', 0)

            # Расчет конверсии
            conversion_rate = (conversions / total_clicks * 100) if total_clicks > 0 else 0

            # Самый популярный пост
            top_post = max(
                [(k, v) for k, v in self.analytics_data['posts'].items()
                 if datetime.fromisoformat(v['time']) > week_ago],
                key=lambda x: x[1].get('link_clicks', 0),
                default=(None, None)
            )

            # Формирование отчета
            report = f"""
📊 **ЕЖЕДНЕВНЫЙ ОТЧЕТ: Статистика за {datetime.now().strftime('%d.%m.%Y')}**

• Опубликовано постов: **{posts_count}**
• Всего переходов по ссылкам: **{total_clicks}**
• Конверсий: **{conversions}** (конверсия: **{conversion_rate:.1f}%**)

🚀 **Топ-пост недели:**
{top_post[1]['final_text'][:200] + '...' if top_post else 'Нет данных'}

🔍 **Рекомендации:**
{self._generate_recommendations(total_clicks, conversion_rate)}

#Отчет #Аналитика #Путешествия
        """

            self.last_analytic_report = report
            return report

        def _generate_recommendations(self, clicks, conversion_rate):
            """Генерация рекомендаций на основе аналитики"""
            if clicks == 0:
                return "➖ Пока недостаточно данных для анализа. Продолжайте публиковать контент!"

            if conversion_rate < 2:
                return (
                    "⚠️ **Низкая конверсия!**\n"
                    "1. Проверьте актуальность партнерских ссылок\n"
                    "2. Добавляйте более убедительные призывы к действию\n"
                    "3. Протестируйте разные форматы описаний предложений"
                )
            elif conversion_rate < 5:
                return (
                    "👍 **Средняя конверсия**\n"
                    "1. Экспериментируйте с разными партнерскими программами\n"
                    "2. Добавьте ограниченные предложения\n"
                    "3. Просите подписчиков оставлять отзывы"
                )
            else:
                return (
                    "✅ **Отличная конверсия!**\n"
                    "1. Увеличьте частоту публикации коммерческих постов\n"
                    "2. Создайте серию постов о самых успешных предложениях\n"
                    "3. Рассмотрите возможность расширения тематики"
                )

        def _generate_analytics_chart(self):
            """Генерация графика аналитики"""
            try:
                # Подготовка данных
                data = []
                today = datetime.now().date()

                for i in range(7, 0, -1):
                    day = today - timedelta(days=i)
                    day_str = day.strftime('%d.%m')

                    clicks = sum(
                        v.get('link_clicks', 0)
                        for k, v in self.analytics_data['posts'].items()
                        if datetime.fromisoformat(v['time']).date() == day
                    )

                    data.append({'Дата': day_str, 'Переходы': clicks})

                df = pd.DataFrame(data)

                # Создание графика
                plt.figure(figsize=(10, 5))
                plt.plot(df['Дата'], df['Переходы'], marker='o', linestyle='-', color='#4CAF50')
                plt.title('Переходы по ссылкам за последнюю неделю', fontsize=14)
                plt.xlabel('Дата', fontsize=12)
                plt.ylabel('Количество переходов', fontsize=12)
                plt.grid(True, linestyle='--', alpha=0.7)
                plt.gca().yaxis.set_major_locator(MaxNLocator(integer=True))

                # Сохранение в файл
                chart_path = f"analytics_chart_{time.strftime('%Y%m%d')}.png"
                plt.savefig(chart_path, bbox_inches='tight')
                plt.close()

                return chart_path

            except Exception as e:
                logger.error(f"Ошибка генерации графика: {str(e)}")
                return None

        def track_link_click(self, link, user_id=None):
            """Регистрация перехода по ссылке"""
            # Упрощенная реализация - в реальной системе это должен быть endpoint
            post_id = self._find_post_by_link(link)
            if post_id:
                self.analytics_data['posts'][post_id]['link_clicks'] = \
                    self.analytics_data['posts'][post_id].get('link_clicks', 0) + 1

                # Сохраняем информацию о пользователе, если доступна
                if user_id:
                    if 'link_clicks' not in self.analytics_data:
                        self.analytics_data['link_clicks'] = {}

                    click_id = hashlib.md5(f"{post_id}_{user_id}".encode()).hexdigest()
                    self.analytics_data['link_clicks'][click_id] = {
                        'post_id': post_id,
                        'user_id': user_id,
                        'timestamp': datetime.now().isoformat(),
                        'converted': False
                    }

        def track_conversion(self, user_id):
            """Регистрация конверсии (например, бронирования)"""
            # Поиск последнего клика пользователя
            user_clicks = [
                k for k, v in self.analytics_data.get('link_clicks', {}).items()
                if v['user_id'] == user_id and not v['converted']
            ]

            if user_clicks:
                last_click_id = user_clicks[-1]
                self.analytics_data['link_clicks'][last_click_id]['converted'] = True

                # Обновление статистики поста
                post_id = self.analytics_data['link_clicks'][last_click_id]['post_id']
                self.analytics_data['posts'][post_id]['conversions'] = \
                    self.analytics_data['posts'][post_id].get('conversions', 0) + 1

        def _find_post_by_link(self, link):
            """Поиск поста по ссылке"""
            for post_id, data in self.analytics_data['posts'].items():
                if link in data['final_text']:
                    return post_id
            return None

        async def change_style(self, new_style):
            """Изменение стиля контента"""
            if new_style in self.TRAVEL_STYLES:
                self.style = new_style
                self.config['Style']['type'] = new_style

                with open('config.ini', 'w') as configfile:
                    self.config.write(configfile)

                return f"✅ Стиль изменен на: {new_style}"
            else:
                return "❌ Неизвестный стиль. Доступные варианты: " + ", ".join(self.TRAVEL_STYLES.keys())

    async def main():
        try:
            manager = TravelChannelManager()
            await manager.start()
        except Exception as e:
            logger.error(f"Критическая ошибка: {str(e)}")

    if __name__ == '__main__':
        asyncio.run(main())
