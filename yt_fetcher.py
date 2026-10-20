"""Скачивание видео по ссылке/поиску через yt-dlp и нарезка на короткие клипы."""

import glob
import os
from typing import List

import config
from applog import log
from audio_utils import find_ffmpeg as _find_ffmpeg, run_process
from cancellation import CancelledError, check


# ---------------------------------------------------------------------------
# ПРОВЕРКА ЗАВИСИМОСТИ
# ---------------------------------------------------------------------------
def _ensure_ytdlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise RuntimeError("yt-dlp не установлен. Выполни: pip install yt-dlp") from exc
    return yt_dlp


# ---------------------------------------------------------------------------
# YT-DLP
# ---------------------------------------------------------------------------
def _ydl_options(output_dir: str, ffmpeg_dir: str) -> dict:
    """Опции yt-dlp: mp4, до 1080p, логи в пайплайн, отмена через хук."""
    def hook(status: dict) -> None:
        check()  # отмена во время скачивания
        if status.get("status") == "finished":
            log(f"  загрузка завершена: {os.path.basename(status.get('filename', ''))}")

    class _Logger:
        def debug(self, msg):
            if not str(msg).startswith("[debug]"):
                log(str(msg))

        def info(self, msg):
            log(str(msg))

        def warning(self, msg):
            log(f"yt-dlp: {msg}")

        def error(self, msg):
            log(f"yt-dlp ERROR: {msg}")

    opts = {
        "outtmpl": os.path.join(output_dir, "%(title).80s_%(id)s.%(ext)s"),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "logger": _Logger(),
    }
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    return opts


def _download(target: str, output_dir: str) -> str:
    """Общий путь скачивания: target = URL или 'ytsearch1:query'."""
    yt_dlp = _ensure_ytdlp()
    os.makedirs(output_dir, exist_ok=True)

    ffmpeg_binary = _find_ffmpeg()
    try:
        ydl_opts = _ydl_options(output_dir, os.path.dirname(ffmpeg_binary))
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target, download=True)
            if info is None:
                raise RuntimeError(f"yt-dlp не вернул данные для: {target}")
            if "entries" in info and info["entries"]:
                info = info["entries"][0]  # для поиска берём первый результат
            filepath = ydl.prepare_filename(info)
    except CancelledError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"yt-dlp не смог скачать '{target}': {exc}") from exc

    if not os.path.isfile(filepath):
        # merge мог сменить расширение на .mp4
        alt = os.path.splitext(filepath)[0] + ".mp4"
        filepath = alt if os.path.isfile(alt) else filepath
    if not os.path.isfile(filepath):
        raise RuntimeError(f"Файл после скачивания не найден: {filepath}")

    log(f"yt-dlp скачал: {filepath}")
    return filepath


def download_from_url(url: str, output_dir: str) -> str:
    """Качает видео по ссылке (YouTube и ~1000 других сайтов) -> путь к файлу."""
    if not url or not url.strip():
        raise ValueError("Пустая ссылка для скачивания.")
    log(f"Скачивание по ссылке: {url}")
    return _download(url.strip(), output_dir)


def download_by_search(query: str, output_dir: str) -> str:
    """Ищет первое видео по запросу и качает его -> путь к файлу."""
    if not query or not query.strip():
        raise ValueError("Пустой поисковый запрос для yt-dlp.")
    log(f"Поиск и скачивание: {query}")
    return _download(f"ytsearch1:{query.strip()}", output_dir)


# ---------------------------------------------------------------------------
# НАРЕЗКА
# ---------------------------------------------------------------------------
def _run_ffmpeg(cmd: List[str]) -> None:
    """Запускает ffmpeg с поддержкой отмены (см. audio_utils.run_process)."""
    run_process(cmd, "нарезка видео")


def split_video_into_clips(video_path: str, output_dir: str,
                           clip_length: int = 4) -> List[str]:
    """Нарезает видео на равные куски по clip_length секунд через ffmpeg.

    Использует segment-муксер + принудительные keyframes на границах, поэтому
    сегменты получаются ровными (ценой перекодирования).
    Возвращает список путей к клипам.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Видео не найдено: {video_path}")

    clip_length = max(1, int(clip_length))
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg = _find_ffmpeg()
    pattern = os.path.join(output_dir, "clip_%03d.mp4")

    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-force_key_frames", f"expr:gte(t,n_forced*{clip_length})",
        "-c:a", "aac",
        "-f", "segment", "-segment_time", str(clip_length),
        "-reset_timestamps", "1",
        pattern,
    ]
    log(f"Нарезка '{os.path.basename(video_path)}' по {clip_length}с...")
    _run_ffmpeg(cmd)

    clips = sorted(glob.glob(os.path.join(output_dir, "clip_*.mp4")))
    if not clips:
        raise RuntimeError("ffmpeg не создал ни одного клипа при нарезке.")
    log(f"Нарезано клипов: {len(clips)}.")
    return clips