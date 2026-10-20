"""Точное определение длительности аудио через ffprobe (без Resolve)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import List, Optional

import config
from applog import log, require
from cancellation import CancelledError, check, is_cancelled


class FFprobeError(RuntimeError):
    """Ошибка вызова ffprobe (не найден, таймаут, битый файл и т.п.)."""


def _binary_candidates(base_name: str):
    """Возможные места бинаря с учётом .exe и PyInstaller (_MEIPASS)."""
    name = f"{base_name}.exe" if os.name == "nt" else base_name
    roots = [config.BASE_DIR]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    for root in roots:
        yield os.path.join(root, name)
        yield os.path.join(root, "bin", name)
        yield os.path.join(root, "ffmpeg", "bin", name)


def _find_ffprobe() -> str:
    """Ищет ffprobe в PATH и рядом с приложением, иначе даёт инструкцию."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe
    for candidate in _binary_candidates("ffprobe"):
        if os.path.isfile(candidate):
            return candidate
    raise FFprobeError(
        "ffprobe не найден в PATH.\n"
        "Установка:\n"
        "  Windows: winget install Gyan.FFmpeg  (или choco install ffmpeg)\n"
        "           и добавь <ffmpeg>\\bin в переменную PATH, перезапусти терминал.\n"
        "  macOS:   brew install ffmpeg\n"
        "  Linux:   sudo apt install ffmpeg\n"
        "Проверка: ffprobe -version"
    )


def find_ffmpeg() -> str:
    """Ищет ffmpeg в PATH и рядом с приложением, иначе даёт инструкцию."""
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    for candidate in _binary_candidates("ffmpeg"):
        if os.path.isfile(candidate):
            return candidate
    raise FFprobeError(
        "ffmpeg не найден. Установи: winget install Gyan.FFmpeg "
        "(или положи ffmpeg.exe рядом с приложением / в bin\\)."
    )


def run_process(cmd: List[str], desc: str = "process") -> None:
    """Запускает внешний процесс с поддержкой отмены.

    stderr пишется во временный файл (а НЕ в PIPE): иначе ffmpeg может
    заполнить буфер пайпа и процесс зависнет на записи (дедлок).
    """
    check()
    log(f"запуск: {desc}")
    with tempfile.TemporaryFile() as err_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_file)
        while proc.poll() is None:
            if is_cancelled():
                proc.terminate()
                raise CancelledError(f"{desc}: отменено пользователем.")
            time.sleep(0.2)
        if proc.returncode != 0:
            err_file.seek(0)
            err = err_file.read().decode("utf-8", "replace")[-600:]
            raise RuntimeError(f"{desc}: код {proc.returncode}: {err}")


def get_audio_duration_ffprobe(audio_path: str, timeout: int = 30) -> float:
    """Возвращает точную длительность аудио в секундах (float).

    Использует `ffprobe -show_entries format=duration -of json`, что даёт
    длительность контейнера с точностью до миллисекунд — надёжнее, чем
    property 'Duration' из Resolve (тот зависит от fps проекта).
    """
    require(audio_path, "Не передан путь к аудио.")
    ffprobe = _find_ffprobe()

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        audio_path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:  # бинарь исчез между which и запуском
        raise FFprobeError(f"ffprobe не запустился: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFprobeError(f"ffprobe завис на файле: {audio_path}") from exc

    if proc.returncode != 0:
        raise FFprobeError(
            f"ffprobe вернул код {proc.returncode} для '{audio_path}':\n{proc.stderr.strip()}"
        )

    try:
        data = json.loads(proc.stdout or "{}")
        duration = float(data["format"]["duration"])
    except (KeyError, ValueError, TypeError) as exc:
        raise FFprobeError(
            f"Не удалось распарсить длительность из вывода ffprobe для '{audio_path}'."
        ) from exc

    if duration <= 0:
        raise FFprobeError(f"ffprobe вернул некорректную длительность: {duration}")

    log(f"ffprobe: длительность аудио {duration:.3f} сек.")
    return duration


def seconds_to_frames(seconds: float, fps: float) -> int:
    """Секунды -> целые кадры при заданном fps (с округлением)."""
    return int(round(seconds * fps))