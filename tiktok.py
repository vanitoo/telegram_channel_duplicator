import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from datetime import datetime
import pandas as pd
import requests
from playwright.sync_api import sync_playwright
from threading import Thread
import json


class TikTokUploaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TikTok Video Uploader (Playwright + Proxy)")
        self.root.geometry("1100x750")

        self.browser = None
        self.page = None
        self.playwright = None

        self.min_delay = 2.5  # Минимальная задержка между действиями
        self.max_delay = 6.0  # Максимальная задержка

        self.cookies_file = "tiktok_cookies.json"
        self.storage_state = {
            "cookies": [],
            "origins": []
        }

        # Playwright instances
        self.playwright = None
        self.browser = None
        self.page = None

        # Proxy settings
        self.proxy_config = {
            'server': None,
            'username': None,
            'password': None,
            'enabled': False
        }

        # Video data
        self.video_files = []
        self.uploaded_videos = []

        # GUI setup
        self.setup_styles()
        self.create_widgets()
        self.load_config()

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.configure('TButton', font=('Helvetica', 10), padding=5)
        self.style.configure('Red.TButton', foreground='red')
        self.style.configure('Green.TButton', foreground='green')

    def create_widgets(self):
        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Proxy settings frame
        proxy_frame = ttk.LabelFrame(main_frame, text="Proxy Settings", padding=10)
        proxy_frame.pack(fill=tk.X, pady=5)

        # Proxy server row
        ttk.Label(proxy_frame, text="Server:").grid(row=0, column=0, sticky=tk.W)
        self.proxy_server_entry = ttk.Entry(proxy_frame, width=30)
        self.proxy_server_entry.grid(row=0, column=1, padx=5, sticky=tk.W)

        # Proxy type combo
        ttk.Label(proxy_frame, text="Type:").grid(row=0, column=2, sticky=tk.W)
        self.proxy_type_combo = ttk.Combobox(
            proxy_frame,
            values=['auto', 'http://', 'https://', 'socks4://', 'socks5://'],
            width=8,
            state='readonly'
        )
        self.proxy_type_combo.grid(row=0, column=3, padx=5)
        self.proxy_type_combo.set('auto')

        # Proxy auth row
        ttk.Label(proxy_frame, text="Username:").grid(row=1, column=0, sticky=tk.W)
        self.proxy_user_entry = ttk.Entry(proxy_frame, width=20)
        self.proxy_user_entry.grid(row=1, column=1, padx=5, sticky=tk.W)

        ttk.Label(proxy_frame, text="Password:").grid(row=2, column=0, sticky=tk.W)
        self.proxy_pass_entry = ttk.Entry(proxy_frame, width=20, show="*")
        self.proxy_pass_entry.grid(row=2, column=1, padx=5, sticky=tk.W)

        # Proxy buttons
        self.proxy_toggle = ttk.Button(
            proxy_frame,
            text="Enable Proxy",
            command=self.toggle_proxy,
            style='Red.TButton'
        )
        self.proxy_toggle.grid(row=1, column=2, rowspan=2, padx=10, sticky=tk.W)

        ttk.Button(
            proxy_frame,
            text="Test Proxy",
            command=self.test_proxy_connection,
            style='TButton'
        ).grid(row=1, column=3, rowspan=2, padx=10, sticky=tk.W)

        # Session status
        self.session_status = ttk.Label(
            proxy_frame,
            text="Session: inactive",
            foreground="gray"
        )
        self.session_status.grid(row=0, column=4, rowspan=3, padx=10, sticky=tk.E)

        # Browser control frame
        browser_frame = ttk.Frame(main_frame)
        browser_frame.pack(fill=tk.X, pady=5)

        self.browser_btn = ttk.Button(
            browser_frame,
            text="Launch Browser",
            command=self.launch_browser,
            style='TButton'
        )
        self.browser_btn.pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(
            browser_frame,
            text="Status: Browser not launched",
            foreground="gray"
        )
        self.status_label.pack(side=tk.RIGHT, padx=5)

        # Video table frame
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True)
        self.create_video_table(table_frame)

        # Action buttons frame
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=10)

        ttk.Button(
            action_frame,
            text="Select Videos Folder",
            command=self.select_folder
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            action_frame,
            text="Upload Selected",
            command=self.start_upload_thread
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            action_frame,
            text="Check Published",
            command=self.check_published
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            action_frame,
            text="View Statistics",
            command=self.show_statistics
        ).pack(side=tk.LEFT, padx=5)

        # Progress bar
        self.progress = ttk.Progressbar(
            action_frame,
            orient=tk.HORIZONTAL,
            length=200,
            mode='determinate'
        )
        self.progress.pack(side=tk.RIGHT, padx=5)

    # Метод обновления статуса
    def update_session_status(self):
        status = "не активна"
        color = "gray"

        if hasattr(self, 'page'):
            if self.page.context.cookies():
                status = "активна (cookies)"
                color = "green"
            else:
                status = "нет cookies"
                color = "orange"

        self.session_status.config(text=f"Сессия: {status}", foreground=color)

    def test_proxy_connection2(self):
        """Проверка прокси с автоматическим определением типа"""
        proxy_url = self.proxy_server_entry.get().strip()
        username = self.proxy_user_entry.get().strip()
        password = self.proxy_pass_entry.get().strip()

        if not proxy_url:
            messagebox.showwarning("Ошибка", "Введите адрес прокси")
            return

        # Автодополнение протокола если не указан
        if not proxy_url.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
            proxy_url = f"http://{proxy_url}"  # Пробуем HTTP по умолчанию

        # Добавляем авторизацию если есть
        if username and password:
            if "@" in proxy_url:
                # Удаляем старые creds если есть
                proxy_url = proxy_url.split("@")[-1]
            proxy_url = f"{username}:{password}@{proxy_url}"

        # Тестируем все возможные протоколы
        results = []
        for protocol in ['http', 'https', 'socks4', 'socks5']:
            if not proxy_url.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
                test_url = proxy_url.replace("http://", f"{protocol}://", 1)
            else:
                test_url = proxy_url.replace(
                    proxy_url.split("://")[0] + "://",
                    f"{protocol}://",
                    1
                )

            try:
                import requests
                proxies = {
                    'http': test_url,
                    'https': test_url
                }

                # Быстрая проверка
                response = requests.get(
                    'https://api.ipify.org?format=json',
                    proxies=proxies,
                    timeout=10
                )
                ip = response.json().get('ip', 'Unknown')
                results.append(f"✓ {protocol.upper()}: Работает (IP: {ip})")
            except Exception as e:
                results.append(f"✗ {protocol.upper()}: Ошибка ({str(e)[:50]})")

        messagebox.showinfo(
            "Результаты проверки",
            "\n".join(results) + "\n\nИспользуйте префикс (http://, socks5://) для явного указания типа"
        )

    def test_proxy_connection3(self):
        """Test proxy connection with proper HTTP/SOCKS support"""
        proxy_url = self.proxy_server_entry.get().strip()
        username = self.proxy_user_entry.get().strip()
        password = self.proxy_pass_entry.get().strip()
        proxy_type = self.proxy_type_combo.get()

        if not proxy_url:
            messagebox.showwarning("Error", "Please enter proxy server address")
            return

        try:
            import requests
            from urllib.parse import urlparse

            # 1. Prepare proxy URL
            if proxy_type == 'auto':
                # Auto-detect proxy type by port
                if ':3128' in proxy_url or ':8080' in proxy_url:
                    proxy_type = 'http://'
                else:
                    proxy_type = 'socks5://'

            # Remove existing protocol if present
            clean_proxy_url = proxy_url
            for proto in ['http://', 'https://', 'socks4://', 'socks5://']:
                if proxy_url.startswith(proto):
                    clean_proxy_url = proxy_url[len(proto):]
                    break

            # Add selected protocol
            formatted_proxy = f"{proxy_type}{clean_proxy_url}"

            # 2. Add authentication if provided
            if username and password:
                if '@' in formatted_proxy:
                    formatted_proxy = formatted_proxy.split('@')[-1]
                formatted_proxy = f"{username}:{password}@{formatted_proxy}"

            # 3. Prepare proxies dict for requests
            proxies = {
                'http': formatted_proxy,
                'https': formatted_proxy
            }

            # 4. Make test requests
            test_services = [
                ("HTTP test", "http://httpbin.org/ip"),
                ("HTTPS test", "https://httpbin.org/ip"),
                ("TikTok test", "https://www.tiktok.com")
            ]

            results = []
            for service_name, test_url in test_services:
                try:
                    response = requests.get(
                        test_url,
                        proxies=proxies,
                        timeout=15,
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    if response.ok:
                        if 'httpbin' in test_url:
                            ip = response.json().get('origin', 'Unknown')
                            results.append(f"✓ {service_name}: Success (IP: {ip})")
                        else:
                            results.append(f"✓ {service_name}: Connection successful")
                    else:
                        results.append(f"✗ {service_name}: Failed (HTTP {response.status_code})")
                except Exception as e:
                    results.append(f"✗ {service_name}: Error ({str(e)[:100]})")

            # 5. Show comprehensive results
            messagebox.showinfo(
                "Proxy Test Results",
                f"Proxy: {formatted_proxy}\n\n" +
                "\n".join(results) +
                "\n\nNote: TikTok may block some proxies even if they work with other sites"
            )

        except Exception as e:
            messagebox.showerror(
                "Proxy Test Failed",
                f"Critical error during testing:\n{str(e)}\n\n"
                "Possible issues:\n"
                "1. Invalid proxy credentials\n"
                "2. Proxy server not responding\n"
                "3. Network firewall blocking\n"
                "4. Incorrect proxy type selected"
            )

    def test_proxy_connection4(self):
        """Test proxy connection with detailed logging"""
        import logging
        from datetime import datetime

        # Configure logging
        logging.basicConfig(
            filename='proxy_test.log',
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger()

        try:
            proxy_url = self.proxy_server_entry.get().strip()
            username = self.proxy_user_entry.get().strip()
            password = self.proxy_pass_entry.get().strip()
            proxy_type = self.proxy_type_combo.get()

            logger.info(f"Starting proxy test with: {proxy_url}, type: {proxy_type}")

            if not proxy_url:
                error_msg = "Proxy server address is empty"
                logger.error(error_msg)
                messagebox.showwarning("Error", error_msg)
                return

            # Prepare proxy URL
            clean_proxy_url = proxy_url
            for proto in ['http://', 'https://', 'socks4://', 'socks5://']:
                if proxy_url.startswith(proto):
                    clean_proxy_url = proxy_url[len(proto):]
                    break

            # Auto-detect proxy type if needed
            if proxy_type == 'auto':
                if ':3128' in clean_proxy_url or ':8080' in clean_proxy_url:
                    proxy_type = 'http://'
                    logger.info("Auto-detected HTTP proxy by port")
                else:
                    proxy_type = 'socks5://'
                    logger.info("Auto-detected SOCKS5 proxy by port")

            formatted_proxy = f"{proxy_type}{clean_proxy_url}"
            logger.info(f"Formatted proxy: {formatted_proxy}")

            # Add authentication
            if username and password:
                if '@' in formatted_proxy:
                    formatted_proxy = formatted_proxy.split('@')[-1]
                formatted_proxy = f"{username}:{password}@{formatted_proxy}"
                logger.info("Added credentials to proxy URL")

            # Prepare requests session
            session = requests.Session()
            session.proxies = {
                'http': formatted_proxy,
                'https': formatted_proxy
            }
            session.headers.update({'User-Agent': 'Mozilla/5.0'})

            # Test URLs
            test_urls = {
                "HTTP Test": "http://httpbin.org/ip",
                "HTTPS Test": "https://httpbin.org/ip",
                "TikTok Test": "https://www.tiktok.com"
            }

            results = []
            for name, url in test_urls.items():
                try:
                    logger.info(f"Testing {name} with {url}")
                    start_time = datetime.now()

                    response = session.get(url, timeout=15)
                    elapsed = (datetime.now() - start_time).total_seconds()

                    if response.ok:
                        if 'httpbin' in url:
                            ip = response.json().get('origin', 'Unknown')
                            result = f"✓ {name}: Success (IP: {ip}, Time: {elapsed:.2f}s)"
                        else:
                            result = f"✓ {name}: Success (Time: {elapsed:.2f}s)"
                        logger.info(result)
                    else:
                        result = f"✗ {name}: HTTP {response.status_code}"
                        logger.error(f"{result} - Response: {response.text[:200]}")

                    results.append(result)

                except Exception as e:
                    error_msg = f"✗ {name}: Error - {str(e)}"
                    logger.exception(error_msg)
                    results.append(error_msg)

            # Save full log to file
            with open('proxy_test.log', 'r') as log_file:
                full_log = log_file.read()

            # Show results
            messagebox.showinfo(
                "Proxy Test Results",
                f"Proxy: {formatted_proxy}\n\n" +
                "\n".join(results) +
                f"\n\nFull log saved to: proxy_test.log"
            )

        except Exception as e:
            logger.exception("Critical error during proxy test")
            messagebox.showerror(
                "Proxy Test Failed",
                f"Critical error:\n{str(e)}\n\n"
                "Check proxy_test.log for details"
            )

    def test_proxy_connection5(self):
        """Test proxy connection with detailed logging"""
        import logging
        from datetime import datetime

        # Configure logging
        logging.basicConfig(
            filename='proxy_test.log',
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger()

        try:
            proxy_url = self.proxy_server_entry.get().strip()
            username = self.proxy_user_entry.get().strip()
            password = self.proxy_pass_entry.get().strip()
            proxy_type = self.proxy_type_combo.get()

            logger.info(f"Starting proxy test with: {proxy_url}, type: {proxy_type}")

            if not proxy_url:
                error_msg = "Proxy server address is empty"
                logger.error(error_msg)
                messagebox.showwarning("Error", error_msg)
                return

            # Prepare proxy URL
            clean_proxy_url = proxy_url
            for proto in ['http://', 'https://', 'socks4://', 'socks5://']:
                if proxy_url.startswith(proto):
                    clean_proxy_url = proxy_url[len(proto):]
                    break

            # Auto-detect proxy type if needed
            if proxy_type == 'auto':
                if ':3128' in clean_proxy_url or ':8080' in clean_proxy_url:
                    proxy_type = 'http'
                    logger.info("Auto-detected HTTP proxy by port")
                else:
                    proxy_type = 'socks5'
                    logger.info("Auto-detected SOCKS5 proxy by port")
            else:
                proxy_type = proxy_type.replace('://', '')

            # Prepare proxy dict for requests
            proxy_dict = {
                'http': f"{proxy_type}://{clean_proxy_url}",
                'https': f"{proxy_type}://{clean_proxy_url}"
            }

            # Add authentication if provided
            if username and password:
                auth_str = f"{username}:{password}@"
                proxy_dict = {
                    'http': f"{proxy_type}://{auth_str}{clean_proxy_url}",
                    'https': f"{proxy_type}://{auth_str}{clean_proxy_url}"
                }

            logger.info(f"Proxy dict: {proxy_dict}")

            # Prepare requests session
            session = requests.Session()
            session.proxies = proxy_dict
            session.headers.update({'User-Agent': 'Mozilla/5.0'})

            # Test URLs
            test_urls = {
                "HTTP Test": "http://httpbin.org/ip",
                "HTTPS Test": "https://httpbin.org/ip",
                "TikTok Test": "https://www.tiktok.com"
            }

            results = []
            for name, url in test_urls.items():
                try:
                    logger.info(f"Testing {name} with {url}")
                    start_time = datetime.now()

                    response = session.get(url, timeout=15)
                    elapsed = (datetime.now() - start_time).total_seconds()

                    if response.ok:
                        if 'httpbin' in url:
                            ip = response.json().get('origin', 'Unknown')
                            result = f"✓ {name}: Success (IP: {ip}, Time: {elapsed:.2f}s)"
                        else:
                            result = f"✓ {name}: Success (Time: {elapsed:.2f}s)"
                        logger.info(result)
                    else:
                        result = f"✗ {name}: HTTP {response.status_code}"
                        logger.error(f"{result} - Response: {response.text[:200]}")

                    results.append(result)

                except Exception as e:
                    error_msg = f"✗ {name}: Error - {str(e)}"
                    logger.exception(error_msg)
                    results.append(error_msg)

            # Save full log to file
            with open('proxy_test.log', 'r') as log_file:
                full_log = log_file.read()

            # Show results
            messagebox.showinfo(
                "Proxy Test Results",
                f"Proxy: {proxy_dict}\n\n" +
                "\n".join(results) +
                f"\n\nFull log saved to: proxy_test.log"
            )

        except Exception as e:
            logger.exception("Critical error during proxy test")
            messagebox.showerror(
                "Proxy Test Failed",
                f"Critical error:\n{str(e)}\n\n"
                "Check proxy_test.log for details"
            )

    def test_proxy_connection6(self):
        """Test proxy connection with proper authentication handling"""
        import logging
        from datetime import datetime

        # Configure logging
        logging.basicConfig(
            filename='proxy_test.log',
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger()

        try:
            proxy_url = self.proxy_server_entry.get().strip()
            username = self.proxy_user_entry.get().strip()
            password = self.proxy_pass_entry.get().strip()
            proxy_type = self.proxy_type_combo.get()

            logger.info(f"Starting proxy test with: {proxy_url}, type: {proxy_type}")

            if not proxy_url:
                error_msg = "Proxy server address is empty"
                logger.error(error_msg)
                messagebox.showwarning("Error", error_msg)
                return

            # Prepare proxy URL without protocol
            clean_proxy_url = proxy_url
            for proto in ['http://', 'https://', 'socks4://', 'socks5://']:
                if proxy_url.startswith(proto):
                    clean_proxy_url = proxy_url[len(proto):]
                    break

            # Auto-detect proxy type if needed
            if proxy_type == 'auto':
                if ':3128' in clean_proxy_url or ':8080' in clean_proxy_url:
                    proxy_type = 'http'
                    logger.info("Auto-detected HTTP proxy by port")
                else:
                    proxy_type = 'socks5'
                    logger.info("Auto-detected SOCKS5 proxy by port")
            else:
                proxy_type = proxy_type.replace('://', '')

            # Prepare proxy URL with authentication
            proxy_auth_url = f"{proxy_type}://{clean_proxy_url}"
            if username and password:
                # Вариант 1: Аутентификация в URL
                proxy_auth_url = f"{proxy_type}://{username}:{password}@{clean_proxy_url}"

                # Вариант 2: Отдельные параметры аутентификации
                proxy_dict = {
                    'http': f"{proxy_type}://{clean_proxy_url}",
                    'https': f"{proxy_type}://{clean_proxy_url}"
                }
                auth = requests.auth.HTTPProxyAuth(username, password)
            else:
                proxy_dict = {
                    'http': proxy_auth_url,
                    'https': proxy_auth_url
                }
                auth = None

            logger.info(f"Using proxy: {proxy_auth_url}")

            # Test URLs
            test_urls = {
                "HTTP Test": "http://httpbin.org/ip",
                "HTTPS Test": "https://httpbin.org/ip",
                "TikTok Test": "https://www.tiktok.com"
            }

            results = []
            for name, url in test_urls.items():
                try:
                    logger.info(f"Testing {name} with {url}")
                    start_time = datetime.now()

                    # Пробуем разные способы аутентификации
                    for attempt in [1, 2]:
                        try:
                            if attempt == 1:
                                # Способ 1: Аутентификация в URL
                                proxies = {
                                    'http': proxy_auth_url,
                                    'https': proxy_auth_url
                                }
                                response = requests.get(
                                    url,
                                    proxies=proxies,
                                    timeout=15,
                                    headers={'User-Agent': 'Mozilla/5.0'}
                                )
                            else:
                                # Способ 2: Отдельная аутентификация
                                response = requests.get(
                                    url,
                                    proxies=proxy_dict,
                                    auth=auth,
                                    timeout=15,
                                    headers={'User-Agent': 'Mozilla/5.0'}
                                )

                            if response.ok:
                                elapsed = (datetime.now() - start_time).total_seconds()
                                if 'httpbin' in url:
                                    ip = response.json().get('origin', 'Unknown')
                                    result = f"✓ {name}: Success (IP: {ip}, Time: {elapsed:.2f}s)"
                                else:
                                    result = f"✓ {name}: Success (Time: {elapsed:.2f}s)"
                                logger.info(result)
                                results.append(result)
                                break

                        except requests.exceptions.ProxyError as pe:
                            if attempt == 2:
                                raise pe
                            continue

                except Exception as e:
                    error_msg = f"✗ {name}: Error - {str(e)}"
                    logger.exception(error_msg)
                    results.append(error_msg)

            # Show results
            messagebox.showinfo(
                "Proxy Test Results",
                f"Proxy: {proxy_auth_url}\n\n" +
                "\n".join(results) +
                f"\n\nFull log saved to: proxy_test.log"
            )

        except Exception as e:
            logger.exception("Critical error during proxy test")
            messagebox.showerror(
                "Proxy Test Failed",
                f"Critical error:\n{str(e)}\n\n"
                "Check proxy_test.log for details"
            )

    def test_proxy_connection(self):
        """Проверка прокси с определением типа, замерами времени и проверкой анонимности"""
        import logging
        from datetime import datetime

        # Настройка логирования
        logging.basicConfig(
            filename='proxy_test.log',
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger()

        try:
            proxy_url = self.proxy_server_entry.get().strip()
            username = self.proxy_user_entry.get().strip()
            password = self.proxy_pass_entry.get().strip()
            proxy_type = self.proxy_type_combo.get().strip().lower() or 'auto'

            logger.info(f"Начинаем тест: {proxy_url}, тип: {proxy_type}")

            if not proxy_url:
                messagebox.showwarning("Ошибка", "Введите адрес прокси-сервера")
                return

            # Автоопределение типа прокси
            if proxy_type == 'auto':
                if ':3128' in proxy_url or ':8080' in proxy_url:
                    proxy_type = 'http'
                    logger.info("Автоопределен HTTP прокси по порту")
                elif ':1080' in proxy_url or ':1081' in proxy_url:
                    proxy_type = 'socks5'
                    logger.info("Автоопределен SOCKS5 прокси по порту")
                else:
                    proxy_type = 'http'
                    logger.info("Тип не определен, пробуем HTTP")

            # Подготовка URL прокси
            clean_proxy = proxy_url.replace('http://', '').replace('https://', '') \
                .replace('socks4://', '').replace('socks5://', '')

            # Формирование параметров для requests
            proxies = {}
            auth = None

            if username and password:
                # Вариант 1: Аутентификация в URL
                proxy_with_auth = f"{proxy_type}://{username}:{password}@{clean_proxy}"
                proxies = {
                    'http': proxy_with_auth,
                    'https': proxy_with_auth
                }

                # Вариант 2: Отдельная аутентификация
                proxy_no_auth = f"{proxy_type}://{clean_proxy}"
                proxies_alt = {
                    'http': proxy_no_auth,
                    'https': proxy_no_auth
                }
                auth = requests.auth.HTTPProxyAuth(username, password)
            else:
                proxies = {
                    'http': f"{proxy_type}://{clean_proxy}",
                    'https': f"{proxy_type}://{clean_proxy}"
                }

            # Тестовые URL
            test_urls = [
                ("HTTP тест", "http://httpbin.org/ip"),
                ("HTTPS тест", "https://httpbin.org/ip"),
                ("Проверка анонимности", "http://httpbin.org/headers"),
                ("TikTok тест", "https://www.tiktok.com")
            ]

            results = []
            detected_ip = None
            is_anonymous = None

            # Выполнение тестов
            for name, url in test_urls:
                try:
                    for attempt in [1, 2]:
                        try:
                            if attempt == 1:
                                current_proxies = proxies
                                current_auth = None
                            else:
                                if not (username and password):
                                    break
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
                                    result = f"✓ {name}: Успех (IP: {detected_ip}, Время: {elapsed:.2f}с)"
                                elif 'httpbin.org/headers' in url:
                                    headers = response.json().get('headers', {})
                                    client_ip = headers.get('X-Forwarded-For', headers.get('X-Real-Ip'))
                                    is_anonymous = client_ip != detected_ip if detected_ip else None
                                    result = f"✓ {name}: {'АНОНИМНЫЙ' if is_anonymous else 'НЕ АНОНИМНЫЙ'} (Время: {elapsed:.2f}с)"
                                else:
                                    result = f"✓ {name}: Успех (Время: {elapsed:.2f}с)"
                                results.append(result)
                                break

                        except Exception as e:
                            if attempt == 2:
                                results.append(f"✗ {name}: Ошибка - {str(e)[:100]}")
                                logger.error(f"Тест {name} не пройден: {str(e)}")

                except Exception as e:
                    results.append(f"✗ {name}: Критическая ошибка - {str(e)[:100]}")
                    logger.exception(f"Критическая ошибка в тесте {name}")

            # Формирование итогового отчета
            report = [
                f"=== Результаты тестирования прокси ===",
                f"Тип прокси: {proxy_type.upper()}",
                f"Адрес: {clean_proxy}",
                f"Аутентификация: {'Да' if username else 'Нет'}"
            ]

            if detected_ip:
                report.append(f"Определенный IP: {detected_ip}")

            if is_anonymous is not None:
                report.append(f"Анонимность: {'ДА' if is_anonymous else 'НЕТ'}")

            report.append("\nДетальные результаты:")
            report.extend(results)

            # Показ результатов в GUI
            self.status_label.config(text="Тестирование завершено", foreground="green")
            messagebox.showinfo(
                "Результаты тестирования прокси",
                "\n".join(report)
            )

        except Exception as e:
            logger.exception("Критическая ошибка в тестировании прокси")
            messagebox.showerror(
                "Ошибка тестирования",
                f"Произошла ошибка:\n{str(e)}\n\nПроверьте логи для деталей."
            )

    def random_delay(self):
        """Случайная задержка между действиями"""
        import random
        import time
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

        # Обновляем прогресс-бар для визуализации задержки
        if hasattr(self, 'progress'):
            self.progress['value'] += 1
            self.root.update()

    def create_video_table(self, parent):
        self.tree = ttk.Treeview(
            parent,
            columns=('filename', 'size', 'duration', 'proxy', 'status', 'upload_date'),
            show='headings',
            selectmode='extended'
        )

        # Configure columns
        columns = {
            'filename': {'text': 'Filename', 'width': 250, 'anchor': tk.W},
            'size': {'text': 'Size (MB)', 'width': 80, 'anchor': tk.CENTER},
            'duration': {'text': 'Duration', 'width': 80, 'anchor': tk.CENTER},
            'proxy': {'text': 'Proxy', 'width': 150, 'anchor': tk.W},
            'status': {'text': 'Status', 'width': 120, 'anchor': tk.CENTER},
            'upload_date': {'text': 'Upload Date', 'width': 120, 'anchor': tk.CENTER}
        }

        for col, params in columns.items():
            self.tree.heading(col, text=params['text'])
            self.tree.column(col, width=params['width'], anchor=params['anchor'])

        # Add scrollbars
        y_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.tree.yview)
        x_scroll = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        # Grid layout
        self.tree.grid(row=0, column=0, sticky='nsew')
        y_scroll.grid(row=0, column=1, sticky='ns')
        x_scroll.grid(row=1, column=0, sticky='ew')

        # Configure grid weights
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

    def toggle_proxy(self):
        """Enable/disable proxy settings"""
        if self.proxy_config['enabled']:
            self.proxy_config['enabled'] = False
            self.proxy_toggle.config(text="Enable Proxy", style='Red.TButton')
            messagebox.showinfo("Proxy", "Proxy disabled")
        else:
            self.proxy_config['server'] = self.proxy_server_entry.get()
            self.proxy_config['username'] = self.proxy_user_entry.get()
            self.proxy_config['password'] = self.proxy_pass_entry.get()

            if not self.proxy_config['server']:
                messagebox.showerror("Error", "Proxy server address is required!")
                return

            self.proxy_config['enabled'] = True
            self.proxy_toggle.config(text="Disable Proxy", style='Green.TButton')
            messagebox.showinfo("Proxy", "Proxy settings saved!\nWill be used on next browser launch.")

        self.save_config()

    def launch_browser2(self):
        """Launch browser with proxy settings and session management"""
        if self.browser:
            messagebox.showwarning("Warning", "Browser is already running!")
            return

        def browser_thread():
            try:
                self.playwright = sync_playwright().start()

                # 1. Prepare launch options
                launch_options = {
                    'headless': False,
                    'args': [
                        '--start-maximized',
                        '--disable-blink-features=AutomationControlled',
                        '--lang=en-US',
                        f'--window-size={self.root.winfo_screenwidth()},{self.root.winfo_screenheight()}'
                    ],
                    'ignore_default_args': ['--enable-automation']
                }

                # 2. Proxy configuration
                if self.proxy_config['enabled']:
                    proxy_url = self.proxy_config['server']

                    # Auto-detect proxy type if not specified
                    if not any(proxy_url.startswith(p) for p in ('http://', 'https://', 'socks4://', 'socks5://')):
                        if ':3128' in proxy_url or ':8080' in proxy_url:
                            proxy_url = f"http://{proxy_url}"
                        else:
                            proxy_url = f"socks5://{proxy_url}"  # Default to SOCKS5

                    # Add authentication if provided
                    if self.proxy_config['username']:
                        creds = f"{self.proxy_config['username']}:{self.proxy_config['password']}@"
                        proxy_url = proxy_url.replace('://', f'://{creds}')

                    launch_options['proxy'] = {
                        'server': proxy_url,
                        'username': self.proxy_config.get('username'),
                        'password': self.proxy_config.get('password'),
                        'bypass': 'localhost,127.0.0.1'
                    }

                    # Add to args for compatibility
                    launch_options['args'].append(f'--proxy-server={proxy_url.split("://")[-1]}')

                # 3. Browser launch with session persistence
                self.browser = self.playwright.chromium.launch(**launch_options)

                # Load existing session if available
                storage_state = None
                if os.path.exists('tiktok_session.json'):
                    try:
                        with open('tiktok_session.json', 'r') as f:
                            storage_state = json.load(f)
                    except:
                        pass

                self.page = self.browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
                    viewport={'width': 1366, 'height': 768},
                    storage_state=storage_state
                )

                # 4. Anti-detection measures
                self.page.add_init_script("""
                    delete navigator.__proto__.webdriver;
                    Object.defineProperty(navigator, 'webdriver', {get: () => false});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                """)

                # 5. Set up session saving on close
                self.page.context.on("close", lambda: self.save_browser_session())

                # 6. Navigate to TikTok
                self.page.goto(
                    "https://www.tiktok.com/upload",
                    timeout=60000,
                    wait_until="networkidle"
                )

                # 7. Update UI
                self.status_label.config(
                    text="Status: Browser ready - check login",
                    foreground="green"
                )
                self.browser_btn.config(state=tk.DISABLED, text="Browser Running")

                # Check if already logged in
                if self.page.locator("text=Log in").count() == 0:
                    messagebox.showinfo("Info", "Session restored! You appear to be logged in.")
                else:
                    messagebox.showinfo(
                        "Action Required",
                        "1. Log in to TikTok in the browser window\n"
                        "2. Return to this app\n"
                        "3. Click 'Upload Selected'"
                    )

            except Exception as e:
                error_msg = str(e)[:200]
                self.status_label.config(
                    text=f"Error: {error_msg}",
                    foreground="red"
                )
                messagebox.showerror(
                    "Browser Error",
                    f"Failed to launch browser:\n{error_msg}\n\n"
                    f"Check your proxy settings and internet connection."
                )
                self.cleanup_browser()

        Thread(target=browser_thread, daemon=True).start()

    def launch_browser3(self):
        """Launch browser with proxy settings and detailed logging"""
        if self.browser:
            messagebox.showwarning("Warning", "Browser is already running!")
            return

        def browser_thread():
            import logging
            logging.basicConfig(
                filename='browser_launch.log',
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
            logger = logging.getLogger()

            try:
                logger.info("Initializing Playwright")
                self.playwright = sync_playwright().start()

                # Prepare launch options
                launch_options = {
                    'headless': False,
                    'args': [
                        '--start-maximized',
                        '--disable-blink-features=AutomationControlled',
                        '--lang=en-US',
                        f'--window-size={self.root.winfo_screenwidth()},{self.root.winfo_screenheight()}'
                    ],
                    'ignore_default_args': ['--enable-automation']
                }
                logger.debug(f"Base launch options: {launch_options}")

                # Proxy configuration
                if self.proxy_config['enabled']:
                    proxy_url = self.proxy_config['server']
                    username = self.proxy_config.get('username')
                    password = self.proxy_config.get('password')

                    logger.info(f"Configuring proxy: {proxy_url}")

                    # Auto-detect proxy type
                    if not any(proxy_url.startswith(p) for p in ('http://', 'https://', 'socks4://', 'socks5://')):
                        if ':3128' in proxy_url or ':8080' in proxy_url:
                            proxy_url = f"http://{proxy_url}"
                            logger.info("Auto-detected HTTP proxy")
                        else:
                            proxy_url = f"socks5://{proxy_url}"
                            logger.info("Auto-detected SOCKS5 proxy")

                    # Add authentication if provided
                    if username and password:
                        if '@' in proxy_url:
                            proxy_url = proxy_url.split('@')[-1]
                        proxy_url = f"{username}:{password}@{proxy_url}"
                        logger.info("Added proxy credentials")

                    # Configure proxy for Playwright
                    proxy_settings = {
                        'server': proxy_url.split('://')[1].split('@')[-1],
                        'bypass': 'localhost,127.0.0.1'
                    }

                    if username and password:
                        proxy_settings['username'] = username
                        proxy_settings['password'] = password

                    launch_options['proxy'] = proxy_settings
                    launch_options['args'].append(f'--proxy-server={proxy_settings["server"]}')

                    logger.debug(f"Full proxy settings: {proxy_settings}")

                # Browser launch
                logger.info("Launching browser")
                self.browser = self.playwright.chromium.launch(**launch_options)
                logger.info("Browser launched successfully")

                # Load session
                storage_state = None
                if os.path.exists('tiktok_session.json'):
                    try:
                        with open('tiktok_session.json', 'r') as f:
                            storage_state = json.load(f)
                        logger.info("Loaded existing session")
                    except Exception as e:
                        logger.error(f"Failed to load session: {str(e)}")

                # Create new page
                self.page = self.browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
                    viewport={'width': 1366, 'height': 768},
                    storage_state=storage_state
                )
                logger.info("New page created")

                # Anti-detection
                self.page.add_init_script("""
                    delete navigator.__proto__.webdriver;
                    Object.defineProperty(navigator, 'webdriver', {get: () => false});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                """)
                logger.info("Anti-detection measures applied")

                # Session saving
                self.page.context.on("close", lambda: self.save_browser_session())
                logger.info("Session save handler configured")

                # Navigation
                logger.info("Navigating to TikTok")
                self.page.goto(
                    "https://www.tiktok.com/upload",
                    timeout=60000,
                    wait_until="networkidle"
                )
                logger.info("Navigation completed")

                # Update UI
                self.root.after(0, lambda: self.status_label.config(
                    text="Status: Browser ready - check login",
                    foreground="green"
                ))
                self.root.after(0, lambda: self.browser_btn.config(
                    state=tk.DISABLED,
                    text="Browser Running"
                ))

                # Check login status
                if self.page.locator("text=Log in").count() == 0:
                    logger.info("User appears to be logged in")
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Info",
                        "Session restored! You appear to be logged in."
                    ))
                else:
                    logger.info("User needs to log in")
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Action Required",
                        "1. Log in to TikTok in the browser window\n"
                        "2. Return to this app\n"
                        "3. Click 'Upload Selected'"
                    ))

            except Exception as e:
                logger.exception("Browser launch failed")
                error_msg = str(e)[:200]
                self.root.after(0, lambda: self.status_label.config(
                    text=f"Error: {error_msg}",
                    foreground="red"
                ))
                self.root.after(0, lambda: messagebox.showerror(
                    "Browser Error",
                    f"Failed to launch browser:\n{error_msg}\n\n"
                    f"Check browser_launch.log for details."
                ))
                self.cleanup_browser()

        Thread(target=browser_thread, daemon=True).start()

    def launch_browser4(self):
        """Launch browser with improved proxy handling and TikTok bypass"""
        if self.browser:
            messagebox.showwarning("Warning", "Browser is already running!")
            return

        def browser_thread():
            import logging
            logging.basicConfig(
                filename='browser_launch.log',
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
            logger = logging.getLogger()

            try:
                logger.info("Initializing Playwright")
                self.playwright = sync_playwright().start()

                # Prepare launch options
                launch_options = {
                    'headless': False,
                    'args': [
                        '--start-maximized',
                        '--disable-blink-features=AutomationControlled',
                        '--lang=en-US',
                        '--disable-web-security',
                        '--disable-features=IsolateOrigins,site-per-process',
                        f'--window-size={self.root.winfo_screenwidth()},{self.root.winfo_screenheight()}'
                    ],
                    'ignore_default_args': ['--enable-automation']
                }

                # Proxy configuration
                if self.proxy_config['enabled']:
                    proxy_url = self.proxy_config['server']
                    username = self.proxy_config.get('username')
                    password = self.proxy_config.get('password')

                    logger.info(f"Configuring proxy: {proxy_url}")

                    # Ensure proxy has protocol
                    if not any(proxy_url.startswith(p) for p in ('http://', 'https://', 'socks4://', 'socks5://')):
                        if ':3128' in proxy_url or ':8080' in proxy_url:
                            proxy_url = f"http://{proxy_url}"
                        else:
                            proxy_url = f"socks5://{proxy_url}"

                    # Add authentication
                    if username and password:
                        if '@' in proxy_url:
                            proxy_url = proxy_url.split('@')[-1]
                        proxy_url = f"{username}:{password}@{proxy_url}"

                    # Extract server for playwright
                    proxy_server = proxy_url.split('://')[1].split('@')[-1]
                    proxy_settings = {
                        'server': proxy_server,
                        'bypass': 'localhost,127.0.0.1'
                    }

                    if username and password:
                        proxy_settings['username'] = username
                        proxy_settings['password'] = password

                    launch_options['proxy'] = proxy_settings
                    launch_options['args'].append(f'--proxy-server={proxy_server}')

                # Browser launch
                logger.info("Launching browser with options: %s", launch_options)
                self.browser = self.playwright.chromium.launch(**launch_options)

                # Context settings for bypassing restrictions
                context = self.browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
                    viewport={'width': 1366, 'height': 768},
                    locale='en-US',
                    timezone_id='America/New_York',
                    permissions=['geolocation']
                )

                # Anti-detection
                context.add_init_script("""
                    delete navigator.__proto__.webdriver;
                    Object.defineProperty(navigator, 'webdriver', {get: () => false});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                """)

                self.page = context.new_page()

                # Try alternative TikTok URLs if main fails
                tiktok_urls = [
                    "https://www.tiktok.com",
                    "https://www.tiktok.com/foryou",
                    "https://www.tiktok.com/upload"
                ]

                last_error = None
                for url in tiktok_urls:
                    try:
                        logger.info(f"Trying to navigate to: {url}")
                        import time
                        time.sleep(5)
                        г
                        self.page.goto(
                            url,
                            timeout=60000,
                            wait_until="domcontentloaded"  # Changed from networkidle
                        )
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning(f"Failed to load {url}: {str(e)}")
                        continue
                else:
                    raise last_error if last_error else Exception("All TikTok URLs failed")

                # Success - update UI
                self.root.after(0, lambda: self.status_label.config(
                    text="Status: Browser ready - check login",
                    foreground="green"
                ))
                self.root.after(0, lambda: messagebox.showinfo(
                    "Info",
                    "Browser launched successfully!\n"
                    "If you don't see TikTok, the proxy might be blocked.\n"
                    "Try logging in manually."
                ))

            except Exception as e:
                logger.exception("Browser launch failed")
                self.root.after(0, lambda: self.status_label.config(
                    text="Error: Browser launch failed",
                    foreground="red"
                ))
                self.root.after(0, lambda: messagebox.showerror(
                    "Browser Error",
                    f"Failed to launch browser:\n{str(e)}\n\n"
                    f"Possible reasons:\n"
                    f"1. Proxy blocked by TikTok\n"
                    f"2. Invalid proxy credentials\n"
                    f"3. Network restrictions\n\n"
                    f"Check browser_launch.log for details."
                ))
                self.cleanup_browser()

        Thread(target=browser_thread, daemon=True).start()

    def launch_browser5(self):
        """Improved browser launch with comprehensive error handling"""
        if self.browser:
            messagebox.showwarning("Warning", "Browser is already running!")
            return

        def browser_thread():
            import logging
            import traceback
            from urllib.parse import urlparse

            logging.basicConfig(
                filename='browser_launch.log',
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s',
                encoding='utf-8'  # Явно указываем кодировку
            )
            logger = logging.getLogger()

            try:
                # 1. Initialize Playwright with validation
                logger.info("Initializing Playwright")
                try:
                    self.playwright = sync_playwright().start()
                except Exception as e:
                    logger.error(f"Playwright initialization failed: {str(e)}")
                    raise Exception("Failed to initialize browser engine. Check if Playwright is properly installed.")

                # 2. Prepare launch options
                launch_options = {
                    'headless': False,
                    'args': [
                        '--start-maximized',
                        '--disable-blink-features=AutomationControlled',
                        '--lang=en-US',
                        '--disable-web-security',
                        f'--window-size={self.root.winfo_screenwidth()},{self.root.winfo_screenheight()}'
                    ],
                    'ignore_default_args': ['--enable-automation']
                }
                logger.debug(f"Base launch options prepared")

                # 3. Configure proxy with validation
                if self.proxy_config['enabled']:
                    try:
                        proxy_url = self.proxy_config['server'].strip()
                        username = self.proxy_config.get('username', '').strip()
                        password = self.proxy_config.get('password', '').strip()

                        if not proxy_url:
                            raise ValueError("Proxy server address is empty")

                        # Validate and format proxy URL
                        if not any(proxy_url.startswith(p) for p in ('http://', 'https://', 'socks4://', 'socks5://')):
                            if ':3128' in proxy_url or ':8080' in proxy_url:
                                proxy_url = f"http://{proxy_url}"
                                logger.info("Auto-detected HTTP proxy")
                            else:
                                proxy_url = f"socks5://{proxy_url}"
                                logger.info("Auto-detected SOCKS5 proxy")

                        # Validate credentials
                        if username and password:
                            if '@' in proxy_url:
                                proxy_url = proxy_url.split('@')[-1]
                            proxy_url = f"{username}:{password}@{proxy_url}"
                            logger.info("Added proxy credentials")

                        # Prepare proxy settings for Playwright
                        parsed = urlparse(proxy_url)
                        proxy_settings = {
                            'server': f"{parsed.hostname}:{parsed.port}",
                            'bypass': 'localhost,127.0.0.1'
                        }

                        if username and password:
                            proxy_settings['username'] = username
                            proxy_settings['password'] = password

                        launch_options['proxy'] = proxy_settings
                        launch_options['args'].append(f'--proxy-server={proxy_settings["server"]}')

                        logger.info(f"Proxy configured: {proxy_settings['server']}")

                    except Exception as e:
                        logger.error(f"Proxy configuration error: {str(e)}")
                        raise Exception(f"Invalid proxy settings: {str(e)}")

                # 4. Launch browser with error handling
                try:
                    logger.info("Launching browser")
                    self.browser = self.playwright.chromium.launch(**launch_options)
                    logger.info("Browser launched successfully")
                except Exception as e:
                    logger.error(f"Browser launch failed: {str(e)}")
                    raise Exception("Failed to launch browser. Check proxy settings and internet connection.")

                # 5. Create context with anti-detection
                try:
                    context = self.browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
                        viewport={'width': 1366, 'height': 768},
                        locale='en-US',
                        timezone_id='America/New_York'
                    )

                    context.add_init_script("""
                        delete navigator.__proto__.webdriver;
                        Object.defineProperty(navigator, 'webdriver', {get: () => false});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    """)
                    logger.info("Browser context created with anti-detection")
                except Exception as e:
                    logger.error(f"Context creation failed: {str(e)}")
                    raise Exception("Failed to configure browser settings")

                # 6. Navigation with retry logic
                tiktok_urls = [
                    "https://www.tiktok.com",
                    "https://www.tiktok.com/foryou",
                    "https://www.tiktok.com/upload"
                ]

                last_error = None
                self.page = context.new_page()

                for url in tiktok_urls:
                    try:
                        logger.info(f"Attempting to navigate to: {url}")
                        response = self.page.goto(
                            url,
                            timeout=60000,
                            wait_until="domcontentloaded"
                        )

                        if response and response.ok:
                            logger.info(f"Successfully loaded: {url}")
                            break

                        logger.warning(f"Received status {response.status if response else 'unknown'} for {url}")
                    except Exception as e:
                        last_error = e
                        logger.warning(f"Failed to load {url}: {str(e)}")
                        continue
                else:
                    if last_error:
                        logger.error("All navigation attempts failed")
                        raise last_error
                    else:
                        logger.error("No URLs could be loaded")
                        raise Exception("Failed to load TikTok - all URLs failed")

                # 7. Success - update UI
                self.root.after(0, lambda: [
                    self.status_label.config(
                        text="Status: Browser ready - check login",
                        foreground="green"
                    ),
                    self.browser_btn.config(
                        state=tk.DISABLED,
                        text="Browser Running"
                    ),
                    messagebox.showinfo(
                        "Success",
                        "Browser launched successfully!\n"
                        "If the page doesn't load correctly:\n"
                        "1. Check if proxy is working\n"
                        "2. Try manual refresh\n"
                        "3. Verify your network connection"
                    )
                ])

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Browser launch failed: {error_msg}\n{traceback.format_exc()}")

                self.root.after(0, lambda: [
                    self.status_label.config(
                        text="Error: Browser launch failed",
                        foreground="red"
                    ),
                    messagebox.showerror(
                        "Critical Error",
                        f"Failed to launch browser:\n{error_msg}\n\n"
                        f"Detailed error logged to browser_launch.log\n"
                        f"Possible solutions:\n"
                        f"1. Try different proxy\n"
                        f"2. Disable VPN/firewall\n"
                        f"3. Check internet connection"
                    )
                ])
                self.cleanup_browser()

        Thread(target=browser_thread, daemon=True).start()

    def launch_browser(self):
        """Launch browser with initial IP check and delayed TikTok navigation"""
        if self.browser:
            messagebox.showwarning("Warning", "Browser is already running!")
            return

        def browser_thread():
            import logging
            import time
            from urllib.parse import urlparse

            logging.basicConfig(
                filename='browser_launch.log',
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s',
                encoding='utf-8'
            )
            logger = logging.getLogger()

            try:
                # 1. Initialize Playwright
                logger.info("Initializing Playwright")
                self.playwright = sync_playwright().start()

                # 2. Configure proxy if enabled
                launch_options = {
                    'headless': False,
                    'args': [
                        '--start-maximized',
                        # '--disable-blink-features=AutomationControlled',
                        # '--lang=en-US',
                        f'--window-size={self.root.winfo_screenwidth()},{self.root.winfo_screenheight()}'
                    ],
                    'ignore_default_args': ['--enable-automation']
                }

                if self.proxy_config['enabled']:
                    proxy_url = self.proxy_config['server']
                    username = self.proxy_config.get('username')
                    password = self.proxy_config.get('password')

                    # Format proxy URL
                    if not any(proxy_url.startswith(p) for p in ('http://', 'https://', 'socks4://', 'socks5://')):
                        proxy_url = f"http://{proxy_url}" if ':3128' in proxy_url or ':8080' in proxy_url else f"socks5://{proxy_url}"

                    if username and password:
                        proxy_url = f"{proxy_url.split('://')[0]}://{username}:{password}@{proxy_url.split('://')[1]}"

                    proxy_settings = {
                        'server': proxy_url.split('://')[1].split('@')[-1],
                        'bypass': 'localhost,127.0.0.1'
                    }
                    if username and password:
                        proxy_settings.update({
                            'username': username,
                            'password': password
                        })

                    launch_options['proxy'] = proxy_settings
                    logger.info(f"Proxy configured: {proxy_settings}")

                # 3. Launch browser
                self.browser = self.playwright.chromium.launch(**launch_options)
                context = self.browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
                    viewport={'width': 1366, 'height': 768}
                )
                self.page = context.new_page()

                # 4. First navigate to IP check site
                ip_check_url = "https://api.ipify.org?format=json"
                logger.info(f"Navigating to IP check: {ip_check_url}")
                try:
                    response = self.page.goto(ip_check_url, timeout=15000)
                    if response.ok:
                        ip_data = self.page.evaluate("() => document.body.textContent")
                        logger.info(f"IP Check Result: {ip_data}")
                        self.root.after(0, lambda: self.status_label.config(
                            text=f"Proxy IP: {ip_data}",
                            foreground="blue"
                        ))
                    else:
                        logger.warning(f"IP check failed with status {response.status}")
                except Exception as e:
                    logger.error(f"IP check failed: {str(e)}")
                    raise Exception(f"Proxy connection test failed: {str(e)}")

                # 5. Wait 30 seconds before TikTok
                self.root.after(0, lambda: self.status_label.config(
                    text="Waiting 30 seconds before TikTok...",
                    foreground="orange"
                ))
                time.sleep(30)

                # 6. Navigate to TikTok
                tiktok_urls = [
                    "https://www.tiktok.com",
                    "https://www.tiktok.com/upload"
                ]

                for url in tiktok_urls:
                    try:
                        logger.info(f"Trying to navigate to: {url}")
                        response = self.page.goto(url, timeout=60000)
                        if response.ok:
                            logger.info(f"Successfully loaded: {url}")
                            break
                    except Exception as e:
                        logger.warning(f"Failed to load {url}: {str(e)}")
                        continue
                else:
                    raise Exception("All TikTok URLs failed")

                # 7. Final status update
                self.root.after(0, lambda: [
                    self.status_label.config(
                        text="Status: Ready - check browser window",
                        foreground="green"
                    ),
                    self.browser_btn.config(
                        state=tk.DISABLED,
                        text="Browser Running"
                    ),
                    messagebox.showinfo(
                        "Ready",
                        "Browser navigation complete!\n"
                        "1. Check if TikTok loaded correctly\n"
                        "2. Login if required\n"
                        "3. Then return to the app"
                    )
                ])

            except Exception as e:
                logger.error(f"Browser failed: {str(e)}")
                self.root.after(0, lambda: [
                    self.status_label.config(
                        text="Error: Browser failed",
                        foreground="red"
                    ),
                    messagebox.showerror(
                        "Error",
                        f"Browser operation failed:\n{str(e)}\n\n"
                        f"Check logs for details."
                    )
                ])
                self.cleanup_browser()

        Thread(target=browser_thread, daemon=True).start()

    def save_browser_session(self):
        """Save browser cookies and session data"""
        if self.page:
            try:
                session_data = self.page.context.storage_state()
                with open('tiktok_session.json', 'w') as f:
                    json.dump(session_data, f)
            except Exception as e:
                print(f"Failed to save session: {str(e)}")

    def cleanup_browser2(self):
        """Properly close browser resources"""
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except:
            pass
        finally:
            self.browser = None
            self.playwright = None
            self.page = None
            self.browser_btn.config(state=tk.NORMAL, text="Launch Browser")

    def cleanup_browser(self):
        """Safe browser cleanup"""
        try:
            if hasattr(self, 'browser') and self.browser:
                self.browser.close()
            if hasattr(self, 'playwright') and self.playwright:
                self.playwright.stop()
        except:
            pass
        finally:
            self.browser = None
            self.page = None
            self.playwright = None


    def select_folder(self):
        """Select folder with videos"""
        folder_path = filedialog.askdirectory(title="Select Folder with Videos")
        if folder_path:
            self.scan_video_folder(folder_path)

    def scan_video_folder(self, folder_path):
        """Scan folder for video files"""
        self.video_files = []
        for item in self.tree.get_children():
            self.tree.delete(item)

        video_exts = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
        for filename in os.listdir(folder_path):
            if filename.lower().endswith(video_exts):
                filepath = os.path.join(folder_path, filename)
                size_mb = os.path.getsize(filepath) / (1024 * 1024)

                status, upload_date = self.check_if_uploaded(filename)
                proxy = self.proxy_config['server'] if self.proxy_config['enabled'] else "No proxy"

                self.video_files.append({
                    'filename': filename,
                    'filepath': filepath,
                    'size_mb': round(size_mb, 2),
                    'duration': self.get_video_duration(filepath),
                    'proxy': proxy,
                    'status': status,
                    'upload_date': upload_date
                })

        # Populate table
        for video in sorted(self.video_files, key=lambda x: x['filename']):
            self.tree.insert('', tk.END, values=(
                video['filename'],
                video['size_mb'],
                video['duration'],
                video['proxy'],
                video['status'],
                video['upload_date'] if video['upload_date'] else ''
            ))

    def get_video_duration(self, filepath):
        """Get video duration (placeholder)"""
        return "N/A"

    def check_if_uploaded(self, filename):
        """Check if video was uploaded"""
        for video in self.uploaded_videos:
            if video['filename'] == filename:
                return "Uploaded", video['upload_date']
        return "Not Uploaded", None

    def start_upload_thread(self):
        """Start upload in background thread"""
        if not self.page:
            messagebox.showerror("Error", "Please launch the browser first!")
            return

        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("Warning", "No videos selected!")
            return

        if not messagebox.askyesno(
                "Confirm Upload",
                f"Upload {len(selected_items)} video(s)?\n\n"
                f"Proxy: {'Enabled' if self.proxy_config['enabled'] else 'Disabled'}"
        ):
            return

        Thread(
            target=self.upload_videos,
            args=(selected_items,),
            daemon=True
        ).start()

    def upload_videos(self, selected_items):
        """Upload videos to TikTok"""
        self.progress['maximum'] = len(selected_items)
        self.progress['value'] = 0

        for i, item_id in enumerate(selected_items, 1):
            item = self.tree.item(item_id)
            filename = item['values'][0]
            video = next((v for v in self.video_files if v['filename'] == filename), None)

            if not video:
                continue

            try:
                self.random_delay()  # Перед загрузкой

                # Update UI
                self.status_label.config(
                    text=f"Uploading {filename[:20]}... (Proxy: {'ON' if self.proxy_config['enabled'] else 'OFF'})",
                    foreground="blue"
                )
                self.tree.item(item_id, values=(
                    video['filename'],
                    video['size_mb'],
                    video['duration'],
                    video['proxy'],
                    "Uploading...",
                    video['upload_date']
                ))
                self.root.update()

                # Upload process
                self.page.locator("input[type='file']").set_input_files(video['filepath'])
                self.random_delay()  # После загрузки

                # Wait for upload to complete
                self.page.wait_for_selector("div.uploading-status:has-text('Upload complete')", timeout=120000)
                self.random_delay()  # После появления статуса

                # Add description
                description = simpledialog.askstring(
                    "Video Description",
                    f"Enter description for {filename}:",
                    initialvalue=f"Uploaded via TikTok Uploader - {datetime.now().strftime('%Y-%m-%d')}"
                ) or ""

                if description:
                    self.page.locator("div[role='textbox']").fill(description)
                    self.random_delay()  # После ввода текста

                # Post video
                self.page.click("button:has-text('Post')")
                self.random_delay(scale=1.5)  # Увеличенная задержка перед проверкой

                # Wait for completion
                try:
                    self.page.wait_for_selector("div.upload-result:has-text('Posted')", timeout=60000)
                except:
                    # Check if upload failed
                    if self.page.locator("text=Upload failed").count() > 0:
                        raise Exception("TikTok reported upload failure")

                # Update status
                video['status'] = "Uploaded"
                video['upload_date'] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.uploaded_videos.append({
                    'filename': video['filename'],
                    'upload_date': video['upload_date'],
                    'proxy_used': video['proxy'],
                    'views': 0,
                    'likes': 0,
                    'comments': 0,
                    'shares': 0
                })

                # Update UI
                self.tree.item(item_id, values=(
                    video['filename'],
                    video['size_mb'],
                    video['duration'],
                    video['proxy'],
                    video['status'],
                    video['upload_date']
                ))
                self.save_uploaded_videos()

            except Exception as e:
                error_msg = str(e)[:100]
                video['status'] = f"Error: {error_msg}"
                self.tree.item(item_id, values=(
                    video['filename'],
                    video['size_mb'],
                    video['duration'],
                    video['proxy'],
                    video['status'],
                    video['upload_date']
                ))

                # Try to close any popups
                try:
                    self.page.click("button:has-text('Cancel')", timeout=2000)
                except:
                    pass

            finally:
                self.progress['value'] = i
                self.root.update()

        self.status_label.config(text="Upload process completed", foreground="green")
        messagebox.showinfo("Done", "All selected videos processed!")

    def check_published(self):
        """Check published videos (simplified)"""
        if not self.page:
            messagebox.showerror("Error", "Browser not launched!")
            return

        try:
            # Go to profile page
            self.page.goto("https://www.tiktok.com/@yourusername")
            self.page.wait_for_selector("div.video-item", timeout=10000)

            # Update stats (placeholder - in real app you'd scrape the data)
            for video in self.uploaded_videos:
                video['views'] = video.get('views', 0) + 100  # Simulate views
                video['likes'] = video.get('likes', 0) + 10  # Simulate likes

            self.save_uploaded_videos()
            messagebox.showinfo("Updated", "Video stats refreshed (simulated data)")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to check videos:\n{str(e)}")

    def show_statistics(self):
        """Show statistics window"""
        if not self.uploaded_videos:
            messagebox.showinfo("Info", "No videos uploaded yet!")
            return

        stats_window = tk.Toplevel(self.root)
        stats_window.title("Upload Statistics")
        stats_window.geometry("900x600")

        # Calculate stats
        total_videos = len(self.uploaded_videos)
        total_views = sum(v.get('views', 0) for v in self.uploaded_videos)
        avg_views = total_views / total_videos if total_videos > 0 else 0

        # Stats frame
        stats_frame = ttk.Frame(stats_window, padding=10)
        stats_frame.pack(fill=tk.X)

        ttk.Label(
            stats_frame,
            text=f"Total Videos Uploaded: {total_videos}",
            font=('Helvetica', 12, 'bold')
        ).pack(anchor=tk.W)

        ttk.Label(
            stats_frame,
            text=f"Total Views: {total_views:,} (Avg: {avg_views:,.1f} per video)"
        ).pack(anchor=tk.W)

        ttk.Label(
            stats_frame,
            text=f"Proxies Used: {len(set(v['proxy_used'] for v in self.uploaded_videos if 'proxy_used' in v))}"
        ).pack(anchor=tk.W)

        # Detailed table
        table_frame = ttk.Frame(stats_window)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = [
            ('filename', 'Filename', 200),
            ('upload_date', 'Upload Date', 120),
            ('proxy_used', 'Proxy', 150),
            ('views', 'Views', 80),
            ('likes', 'Likes', 80),
            ('comments', 'Comments', 80)
        ]

        tree = ttk.Treeview(table_frame, columns=[c[0] for c in columns], show='headings')

        for col_id, col_text, col_width in columns:
            tree.heading(col_id, text=col_text)
            tree.column(col_id, width=col_width, anchor=tk.CENTER if col_id in ('views', 'likes', 'comments') else tk.W)

        # Add scrollbars
        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        # Grid layout
        tree.grid(row=0, column=0, sticky='nsew')
        y_scroll.grid(row=0, column=1, sticky='ns')
        x_scroll.grid(row=1, column=0, sticky='ew')

        # Configure grid weights
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        # Add data
        for video in sorted(self.uploaded_videos, key=lambda x: x.get('upload_date', ''), reverse=True):
            tree.insert('', tk.END, values=(
                video['filename'],
                video.get('upload_date', ''),
                video.get('proxy_used', 'No proxy'),
                video.get('views', 0),
                video.get('likes', 0),
                video.get('comments', 0)
            ))

    def save_config(self):
        """Save proxy settings to file"""
        config = {
            'proxy': self.proxy_config,
            'last_folder': getattr(self, 'last_folder', '')
        }
        with open('tiktok_uploader_config.json', 'w') as f:
            json.dump(config, f)

    def load_config(self):
        """Load proxy settings from file"""
        try:
            if os.path.exists('tiktok_uploader_config.json'):
                with open('tiktok_uploader_config.json', 'r') as f:
                    config = json.load(f)

                self.proxy_config = config.get('proxy', self.proxy_config)
                self.last_folder = config.get('last_folder', '')

                # Update UI
                self.proxy_server_entry.insert(0, self.proxy_config.get('server', ''))
                self.proxy_user_entry.insert(0, self.proxy_config.get('username', ''))
                self.proxy_pass_entry.insert(0, self.proxy_config.get('password', ''))

                if self.proxy_config['enabled']:
                    self.proxy_toggle.config(text="Disable Proxy", style='Green.TButton')

        except Exception as e:
            print(f"Error loading config: {str(e)}")

    def save_uploaded_videos(self):
        """Save uploaded videos data"""
        try:
            df = pd.DataFrame(self.uploaded_videos)
            df.to_csv('uploaded_videos.csv', index=False)
        except Exception as e:
            print(f"Error saving data: {str(e)}")

    def load_uploaded_videos(self):
        """Load uploaded videos data"""
        try:
            if os.path.exists('uploaded_videos.csv'):
                df = pd.read_csv('uploaded_videos.csv')
                self.uploaded_videos = df.to_dict('records')
        except Exception as e:
            print(f"Error loading data: {str(e)}")
            self.uploaded_videos = []

    def get_network_conditions(self):
        """Возвращает текущие сетевые настройки"""
        return {
            'ip': self.current_ip,
            'user_agent': self.page.evaluate("navigator.userAgent"),
            'cookies': self.page.context.cookies()
        }

    def ensure_consistent_session(self):
        """Проверяет соответствие IP и cookies"""
        if hasattr(self, 'session_ip') and self.session_ip != self.current_ip:
            messagebox.showwarning(
                "Внимание!",
                "IP изменился! Рекомендуется перелогиниться.\n"
                f"Был: {self.session_ip}\nСейчас: {self.current_ip}"
            )

    def on_closing2(self):
        """При закрытии приложения"""
        self.save_cookies()
        if self.browser:
            self.browser.close()

        """Cleanup on window close"""
        # if self.browser:
        #     self.browser.close()

        if self.playwright:
            self.playwright.stop()
        self.save_config()
        self.root.destroy()

    def on_closing(self):
        """Safe application shutdown"""
        try:
            # Проверяем, существует ли еще браузер
            if hasattr(self, 'browser') and self.browser:
                # Пытаемся сохранить cookies только если страница еще доступна
                if hasattr(self, 'page') and self.page and not self.page.is_closed():
                    try:
                        # Сохраняем cookies в отдельном потоке
                        Thread(target=self.save_cookies, daemon=True).start()
                    except Exception as e:
                        print(f"Cookie save error (non-critical): {str(e)}")

                # Закрываем браузер
                self.browser.close()

            # Останавливаем Playwright
            if hasattr(self, 'playwright') and self.playwright:
                self.playwright.stop()
        except Exception as e:
            print(f"Cleanup error: {str(e)}")
        finally:
            # Гарантированно закрываем приложение
            self.root.destroy()

    def save_cookies(self):
        """Thread-safe cookie saving"""
        try:
            if hasattr(self, 'page') and self.page and not self.page.is_closed():
                # Создаем новый контекст для сохранения
                with self.page.context.expect_page() as new_page_info:
                    self.page.evaluate("() => window.open()")
                new_page = new_page_info.value
                storage_state = new_page.context.storage_state()
                new_page.close()

                with open('tiktok_session.json', 'w') as f:
                    json.dump(storage_state, f)
        except Exception as e:
            print(f"Failed to save cookies: {str(e)}")



if __name__ == "__main__":
    root = tk.Tk()
    app = TikTokUploaderApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()