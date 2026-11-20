"""Генерация субтитров через faster-whisper -> SRT (без DaVinci Resolve)."""

import os
import threading
from typing import List, Optional

import config
from applog import log
from cancellation import check
from srt_tools import write_srt

# Модель грузится секунды, а прогонов за сессию обычно несколько — кэшируем.
_MODELS: dict = {}
_MODEL_LOCK = threading.Lock()
# Устройства, которые уже не сработали (нет CUDA/драйвера): второй раз не пробуем.
_BROKEN_DEVICES: set = set()


def _load_whisper_model(model_size: str):
    """Ленивая загрузка faster-whisper с понятной ошибкой, если нет пакета.

    По умолчанию CPU + int8: на системах без CUDA попытка использовать GPU падает
    с ошибкой вида "cublas64_12.dll is not found". Если в конфиге явно задан GPU,
    пробуем его, а при неудаче автоматически откатываемся на CPU.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper не установлен. Выполни: pip install faster-whisper"
        ) from exc

    device = config.WHISPER_DEVICE or "cpu"
    compute_type = config.WHISPER_COMPUTE_TYPE or "int8"
    if device != "cpu" and device in _BROKEN_DEVICES:
        log(f"Устройство {device} уже не запускалось — сразу CPU/int8.")
        device, compute_type = "cpu", "int8"
    key = (model_size, device, compute_type)

    with _MODEL_LOCK:
        cached = _MODELS.get(key)
        if cached is not None:
            log(f"Модель Whisper '{model_size}' уже загружена — переиспользую.")
            return cached

        log(f"Загрузка модели Whisper '{model_size}' "
            f"(device={device}, compute_type={compute_type})...")
        try:
            model = WhisperModel(model_size, device=device,
                                 compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001 — нет CUDA/драйвера и т.п.
            if device == "cpu":
                raise
            log(f"Whisper на {device} не запустился ({exc}) — "
                "переключаюсь на CPU/int8.")
            _BROKEN_DEVICES.add(device)
            key = (model_size, "cpu", "int8")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

        _MODELS[key] = model
        return model


def transcribe_audio(
    audio_path: str,
    output_srt: Optional[str] = None,
    model_size: Optional[str] = None,
    language: Optional[str] = None,
) -> List[dict]:
    """Распознаёт речь и пишет SRT-файл.

    Возвращает список сегментов [{"start","end","text"}], а также сохраняет
    их в output_srt (по умолчанию config.SUBTITLES_PATH).
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Аудио не найдено: {audio_path}")

    output_srt = output_srt or config.SUBTITLES_PATH
    model_size = model_size or config.WHISPER_MODEL
    language = language or config.WHISPER_LANGUAGE

    model = _load_whisper_model(model_size)
    log(f"Распознавание речи ({language})...")

    # vad_filter=True выкидывает тишину -> меньше "пустых" субтитров.
    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        vad_filter=True,
        beam_size=5,
    )
    log(f"Язык определён: {getattr(info, 'language', language)}")

    segments: List[dict] = []
    for seg in segments_iter:  # генератор — обрабатываем потоково
        check()  # отмена во время распознавания
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": text})

    if not segments:
        raise RuntimeError("Whisper не вернул ни одного сегмента — проверь аудио.")

    write_srt(segments, output_srt)
    log(f"Субтитры сохранены: {output_srt} ({len(segments)} реплик).")
    return segments