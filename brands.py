import re
import os

def load_brands_from_file(filename='car_brands.txt'):
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Файл {filename} не найден")
    with open(filename, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

car_brands = load_brands_from_file()

def normalize_tag(brand_name):
    """Преобразует название бренда в безопасный хештег с подчёркиваниями"""
    return re.sub(r'[^a-zA-Z0-9]', '_', brand_name)

def find_car_brands(text):
    """Возвращает список до 3 нормализованных хештегов брендов"""
    found = set()
    text_lower = text.lower()
    for brand in car_brands:
        if re.search(rf'\b{re.escape(brand.lower())}\b', text_lower):
            tag = f"{normalize_tag(brand)}"
            found.add(tag)
        if len(found) >= 3:
            break
    return sorted(found)
