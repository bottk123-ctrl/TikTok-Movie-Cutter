"""Глобальный флаг отмены пайплайна (thread-safe через threading.Event)."""

import threading


class CancelledError(RuntimeError):
    """Пользователь отменил операцию."""


_event = threading.Event()


def reset() -> None:
    """Сбросить флаг перед новым запуском."""
    _event.clear()


def request_cancel() -> None:
    """Запросить отмену (вызывается из GUI-потока)."""
    _event.set()


def is_cancelled() -> bool:
    return _event.is_set()


def check() -> None:
    """Бросает CancelledError, если запрошена отмена. Вызывать в тяжёлых циклах."""
    if _event.is_set():
        raise CancelledError("Операция отменена пользователем.")