"""Единая конфигурация пайплайна. Пути переопределяются через env.

Пути по умолчанию строятся относительно BASE_DIR, что корректно работает и
при запуске из исходников, и из собранного .exe (см. _base_dir()).
"""

import os
import sys


def _base_dir() -> str:
    """Каталог приложения: рядом с .exe (frozen) либо рядом с этим файлом."""
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable -> путь к нашему .exe
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR: str = _base_dir()
_join = os.path.join

# --- Пути по умолчанию (относительно BASE_DIR) ---
ASSETS_DIR: str = os.environ.get("TIKTOK_ASSETS", _join(BASE_DIR, "assets"))
WORK_DIR: str = os.environ.get("TIKTOK_WORKDIR", _join(BASE_DIR, "workspace"))
OUTPUT_DIR: str = os.environ.get("TIKTOK_OUTPUT_DIR", _join(BASE_DIR, "output"))

AUDIO_PATH: str = os.environ.get("TIKTOK_AUDIO", _join(ASSETS_DIR, "voice.mp3"))
VIDEO_DIR: str = os.environ.get("TIKTOK_CLIPS", _join(ASSETS_DIR, "clips"))
SUBTITLES_PATH: str = os.environ.get("TIKTOK_SRT", _join(WORK_DIR, "subtitles.srt"))
CLIP_DOWNLOAD_DIR: str = os.environ.get("CLIP_DOWNLOAD_DIR", _join(BASE_DIR, "downloads"))
BGM_PATH: str = os.environ.get("TIKTOK_BGM", _join(ASSETS_DIR, "music.mp3"))
# Папка с фоновой музыкой: трек выбирается случайно.
MUSIC_DIR: str = os.environ.get("TIKTOK_MUSIC_DIR", _join(BASE_DIR, "music"))
OUTPUT_PATH: str = os.environ.get("TIKTOK_OUTPUT", _join(OUTPUT_DIR, "final_video.mp4"))

# --- Таймлайн ---
TIMELINE_NAME: str = "TikTok_Vertical_Auto"
TARGET_WIDTH: int = 1080
TARGET_HEIGHT: int = 1920
TARGET_FPS: float = 30.0

# --- Субтитры (faster-whisper) ---
WHISPER_MODEL: str = os.environ.get("WHISPER_MODEL", "small")  # tiny/base/small/medium
WHISPER_LANGUAGE: str = os.environ.get("WHISPER_LANGUAGE", "ru")
WHISPER_COMPUTE_TYPE: str = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DEVICE: str = os.environ.get("WHISPER_DEVICE", "cpu")  # только CPU (без CUDA)
ENABLE_SUBTITLES: bool = os.environ.get("ENABLE_SUBTITLES", "1") != "0"

# --- TikTok-нарезка субтитров ---
SUBTITLES_TIKTOK_PATH: str = os.environ.get(
    "TIKTOK_SRT_TIKTOK", _join(WORK_DIR, "subtitles_tiktok.srt")
)
SUBTITLE_MAX_WORDS: int = int(os.environ.get("SUBTITLE_MAX_WORDS", "3"))
SUBTITLE_HIGHLIGHT: str = os.environ.get("SUBTITLE_HIGHLIGHT", "upper")
SUBTITLE_KEYWORDS: set = {
    w.strip().lower()
    for w in os.environ.get("SUBTITLE_KEYWORDS", "").split(",")
    if w.strip()
}

# --- Стилизация вжигаемых субтитров (ASS/ffmpeg subtitles) ---
SUBTITLE_FONT: str = os.environ.get("SUBTITLE_FONT", "Impact")
SUBTITLE_FILL_COLOR: str = os.environ.get("SUBTITLE_FILL_COLOR", "#FFFFFF")
SUBTITLE_OUTLINE_COLOR: str = os.environ.get("SUBTITLE_OUTLINE_COLOR", "#000000")
SUBTITLE_OUTLINE_WIDTH: float = float(os.environ.get("SUBTITLE_OUTLINE_WIDTH", "2"))
SUBTITLE_ASS_FONT_SIZE: int = int(os.environ.get("SUBTITLE_ASS_FONT_SIZE", "24"))
SUBTITLE_ALIGNMENT: int = int(os.environ.get("SUBTITLE_ALIGNMENT", "2"))
SUBTITLE_MARGIN_V: int = int(os.environ.get("SUBTITLE_MARGIN_V", "80"))

# --- Clip.Cafe (легальное скачивание клипов) ---
CLIP_CAFE_API_KEY: str = os.environ.get("CLIP_CAFE_API_KEY", "")
CLIP_CAFE_BASE_URL: str = os.environ.get("CLIP_CAFE_BASE_URL", "https://api.clip.cafe/")
AUTO_FETCH_CLIPS: bool = os.environ.get("AUTO_FETCH_CLIPS", "1") != "0"
CLIP_SEARCH_MOVIE_TITLE: str = os.environ.get("CLIP_MOVIE_TITLE", "")
CLIP_SEARCH_TRANSCRIPT: str = os.environ.get("CLIP_TRANSCRIPT", "")
CLIP_SEARCH_LIMIT: int = int(os.environ.get("CLIP_SEARCH_LIMIT", "10"))
HTTP_TIMEOUT: int = int(os.environ.get("HTTP_TIMEOUT", "30"))

# --- yt-dlp (YouTube и др.) ---
YT_VIDEO_URL: str = os.environ.get("YT_VIDEO_URL", "")
YT_RAW_DIR: str = os.environ.get("YT_RAW_DIR", _join(BASE_DIR, "downloads", "raw"))
YT_CLIP_LENGTH: int = int(os.environ.get("YT_CLIP_LENGTH", "4"))
# Искать по названию фильма через yt-dlp, если нет ключа Clip.Cafe и нет ссылки.
YT_SEARCH_FALLBACK: bool = os.environ.get("YT_SEARCH_FALLBACK", "1") != "0"

# --- Динамика и звук (FFmpeg) ---
ZOOM_INTENSITY: float = float(os.environ.get("ZOOM_INTENSITY", "1.1"))
ZOOM_STEP: float = float(os.environ.get("ZOOM_STEP", "0.0015"))
APPLY_ZOOM: bool = os.environ.get("APPLY_ZOOM", "1") != "0"
BGM_VOLUME: float = float(os.environ.get("BGM_VOLUME", "0.15"))
VOICE_VOLUME: float = float(os.environ.get("VOICE_VOLUME", "1.0"))
BURN_SUBTITLES: bool = os.environ.get("BURN_SUBTITLES", "1") != "0"

# --- Озвучка (edge-tts) ---
TTS_VOICE: str = os.environ.get("TTS_VOICE", "ru-RU-DmitryNeural")
TTS_RATE: str = os.environ.get("TTS_RATE", "+0%")
TTS_VOLUME: str = os.environ.get("TTS_VOLUME", "+0%")

# --- Форматы файлов ---
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mxf", ".mkv", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}