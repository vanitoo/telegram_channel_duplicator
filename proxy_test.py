import requests
import logging
from datetime import datetime


def test_proxy_connection_console():
    """Улучшенный консольный тестер прокси с проверкой анонимности"""
    # Настройка логирования
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger()

    print("\n=== ТЕСТЕР ПРОКСИ-СЕРВЕРА ===")
    print("(оставьте тип пустым для автоопределения)\n")

    # Ввод параметров
    proxy_url = input("Введите адрес прокси (host:port): ").strip()
    proxy_type = input("Тип прокси [auto/http/https/socks4/socks5] (по умолчанию auto): ").strip().lower() or 'auto'
    username = input("Логин (если есть, иначе Enter): ").strip()
    password = input("Пароль (если есть, иначе Enter): ").strip()

    try:
        logger.info(f"Начинаем тест: {proxy_url}, тип: {proxy_type}")

        # Автоопределение типа прокси
        detected_type = None
        if proxy_type == 'auto':
            if ':3128' in proxy_url or ':8080' in proxy_url:
                detected_type = 'http'
                logger.info("Автоопределен HTTP прокси по порту")
            elif ':1080' in proxy_url or ':1081' in proxy_url:
                detected_type = 'socks5'
                logger.info("Автоопределен SOCKS5 прокси по порту")
            else:
                detected_type = 'http'  # По умолчанию пробуем HTTP
                logger.info("Тип не определен, пробуем HTTP")
        else:
            detected_type = proxy_type

        print(f"\nОпределен тип прокси: {detected_type.upper()}")

        # Подготовка URL прокси
        clean_proxy = proxy_url.replace('http://', '').replace('https://', '') \
            .replace('socks4://', '').replace('socks5://', '')

        # Формирование параметров для requests
        proxies = {}
        auth = None

        if username and password:
            # Вариант 1: Аутентификация в URL
            proxy_with_auth = f"{detected_type}://{username}:{password}@{clean_proxy}"
            proxies = {
                'http': proxy_with_auth,
                'https': proxy_with_auth
            }

            # Вариант 2: Отдельная аутентификация
            proxy_no_auth = f"{detected_type}://{clean_proxy}"
            proxies_alt = {
                'http': proxy_no_auth,
                'https': proxy_no_auth
            }
            auth = requests.auth.HTTPProxyAuth(username, password)
        else:
            proxies = {
                'http': f"{detected_type}://{clean_proxy}",
                'https': f"{detected_type}://{clean_proxy}"
            }

        # Тестовые URL
        test_urls = [
            ("HTTP тест", "http://httpbin.org/ip"),
            ("HTTPS тест", "https://httpbin.org/ip"),
            ("Проверка анонимности", "http://httpbin.org/headers"),
            ("TikTok тест", "https://www.tiktok.com")
        ]

        # Результаты тестов
        results = []
        detected_ip = None
        is_anonymous = None

        # Выполнение тестов
        for name, url in test_urls:
            print(f"\n--- {name} ({url}) ---")

            for attempt in [1, 2]:
                try:
                    if attempt == 1:
                        print("[Попытка 1] Аутентификация в URL")
                        current_proxies = proxies
                        current_auth = None
                    else:
                        if not (username and password):
                            break
                        print("[Попытка 2] Отдельная аутентификация")
                        current_proxies = proxies_alt
                        current_auth = auth

                    start = datetime.now()
                    response = requests.get(
                        url,
                        proxies=current_proxies,
                        auth=current_auth,
                        timeout=15,
                        headers={
                            'User-Agent': 'Mozilla/5.0',
                            'Accept': 'application/json'
                        }
                    )

                    elapsed = (datetime.now() - start).total_seconds()

                    if response.ok:
                        if 'httpbin.org/ip' in url:
                            detected_ip = response.json().get('origin', 'Unknown')
                            result = f"УСПЕХ! IP: {detected_ip}, Время: {elapsed:.2f}с"
                            results.append((name, True, detected_ip, elapsed))
                        elif 'httpbin.org/headers' in url:
                            headers = response.json().get('headers', {})
                            client_ip = headers.get('X-Forwarded-For', headers.get('X-Real-Ip'))
                            is_anonymous = client_ip != detected_ip if detected_ip else None

                            anonymity = "АНОНИМНЫЙ" if is_anonymous else "НЕ АНОНИМНЫЙ"
                            result = f"УСПЕХ! {anonymity}, Время: {elapsed:.2f}с\n" \
                                     f"Заголовки: {headers}"
                            results.append((name, True, is_anonymous, elapsed))
                        else:
                            result = f"УСПЕХ! Время: {elapsed:.2f}с"
                            results.append((name, True, None, elapsed))

                        print(result)
                        break
                    else:
                        result = f"Ошибка HTTP {response.status_code}"
                        print(result)
                        results.append((name, False, result, elapsed))

                except Exception as e:
                    result = f"Ошибка: {str(e)}"
                    print(result)
                    if attempt == 2:
                        results.append((name, False, result, 0))
                        logger.error(f"Тест {name} не пройден: {str(e)}")

        # Итоговый отчет
        print("\n=== ИТОГОВЫЙ ОТЧЕТ ===")
        print(f"Использованный тип прокси: {detected_type.upper()}")

        if detected_ip:
            print(f"Определенный внешний IP: {detected_ip}")

        if is_anonymous is not None:
            print(f"Уровень анонимности: {'АНОНИМНЫЙ' if is_anonymous else 'НЕ АНОНИМНЫЙ'}")

        print("\nДетальные результаты:")
        for name, success, detail, time in results:
            status = "УСПЕХ" if success else "ОШИБКА"
            print(f"{name}: {status} ({time:.2f}с)")
            if detail:
                print(f"  Детали: {detail}")

    except Exception as e:
        logger.exception("Критическая ошибка")
        print(f"\n!!! КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")

    input("\nНажмите Enter для выхода...")


if __name__ == "__main__":
    test_proxy_connection_console()