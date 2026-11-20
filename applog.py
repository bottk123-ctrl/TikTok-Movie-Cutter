"""Единый лог с приёмниками (для GUI) и записью в файл — без DaVinci Resolve."""

import os
import threading
import time

import config

_LOG_SINKS: list = []
_LOCK = threading.Lock()
_MAX_LOG_BYTES = 2 * 1024 * 1024  # после 2 МБ файл уходит в .1 и начинается заново


def register_log_sink(callback) -> None:
    """Регистрирует приёмник логов (например, вывод в GUI)."""
    with _LOCK:
        if callback and callback not in _LOG_SINKS:
            _LOG_SINKS.append(callback)


def unregister_log_sink(callback) -> None:
    with _LOCK:
        try:
            _LOG_SINKS.remove(callback)
        except ValueError:
            pass


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.getsize(path) < _MAX_LOG_BYTES:
            return
        backup = path + ".1"
        if os.path.exists(backup):
            os.remove(backup)
        os.replace(path, backup)
    except OSError:
        pass


def _write_to_file(text: str) -> None:
    """Пишет строку в LOG_PATH (если включено). Ошибки записи не роняют пайплайн."""
    if not getattr(config, "LOG_TO_FILE", True):
        return
    path = getattr(config, "LOG_PATH", "")
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with _LOCK:
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def log(msg: str) -> None:
    text = f"[TikTokPipeline] {msg}"
    with _LOCK:
        sinks = list(_LOG_SINKS)
    _write_to_file(text)
    if sinks:
        for sink in sinks:
            try:
                sink(text)
            except Exception:  # noqa: BLE001 — приёмник не должен ронять пайплайн
                pass
    else:
        print(text, flush=True)


def require(obj, message: str):
    """Явная проверка на None с понятной ошибкой."""
    if obj is None:
        raise RuntimeError(message)
    return obj
