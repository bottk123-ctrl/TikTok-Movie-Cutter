"""Единый лог с приёмниками (для GUI) — без зависимости от DaVinci Resolve."""

import threading

_LOG_SINKS: list = []
_LOCK = threading.Lock()


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


def log(msg: str) -> None:
    text = f"[TikTokPipeline] {msg}"
    with _LOCK:
        sinks = list(_LOG_SINKS)
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