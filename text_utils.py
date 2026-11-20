"""Общая работа с текстом: чистка и нормализация.

Нужна и TTS (что реально озвучивать), и пайплайну (сравнить с прошлой
озвучкой). Отдельный модуль — чтобы не тянуть torch ради одной функции.
"""

import re


def clean_text(text: str) -> str:
    """Убирает управляющие символы; для текста без букв возвращает ''."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    if not re.search(r"[^\W\d_]", text, flags=re.UNICODE):
        return ""
    return text.strip()


def normalize_text(text: str) -> str:
    """Чистит и схлопывает пробелы: один и тот же текст в разных переносах равен."""
    return " ".join(clean_text(text).split())
