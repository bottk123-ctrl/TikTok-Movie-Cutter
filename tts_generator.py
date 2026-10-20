"""Генерация озвучки через edge-tts (нейроголоса Microsoft) — сразу в MP3.

Без тяжёлых моделей и без torch: edge-tts обращается к сервису Microsoft,
поэтому нужен доступ в интернет. Идеально собирается в .exe.

Устойчивость: чистка текста, ретраи с бэкоффом, фолбэк без rate/volume и
разбивка длинного текста на части (ошибка "No audio was received" обычно
означает пустой/битый текст или разовый сбой API).
"""

import asyncio
import os
import re
import time
from typing import List, Optional

import config
from applog import log

# Самые естественные русские голоса edge-tts.
VOICES: List[str] = [
    "ru-RU-DmitryNeural",    # мужской
    "ru-RU-SvetlanaNeural",  # женский
    "ru-RU-DariyaNeural",    # женский
]

# Совместимость со старыми именами (Silero) из GUI/настроек.
_ALIASES = {
    "aidar": "ru-RU-DmitryNeural",
    "eugene": "ru-RU-DmitryNeural",
    "baya": "ru-RU-SvetlanaNeural",
    "xenia": "ru-RU-SvetlanaNeural",
    "kseniya": "ru-RU-DariyaNeural",
    "dmitry": "ru-RU-DmitryNeural",
    "svetlana": "ru-RU-SvetlanaNeural",
    "dariya": "ru-RU-DariyaNeural",
}

_VALID_MOD = re.compile(r"^[+-]\d{1,3}%$")
_MAX_CHUNK_CHARS = 2500  # длинный текст режем по предложениям


def list_voices() -> List[str]:
    """Доступные голоса (полные имена edge-tts)."""
    return list(VOICES)


def resolve_voice(speaker: str) -> str:
    """Приводит имя голоса к формату edge-tts (с учётом старых алиасов)."""
    if not speaker:
        return VOICES[0]
    if speaker in VOICES:
        return speaker
    if speaker in _ALIASES:
        return _ALIASES[speaker]
    if speaker.startswith("ru-RU-"):
        return speaker
    log(f"Неизвестный голос '{speaker}', использую {VOICES[0]}.")
    return VOICES[0]


# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНОЕ
# ---------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    """Убирает управляющие символы и лишние пробелы, чистит по краям."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # оставляем перевод строки/табуляцию, выкидываем прочие управляющие символы
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    # не даём тексту быть "пустым" (одни знаки препинания) для сервиса
    if not re.search(r"[^\W\d_]", text, flags=re.UNICODE):
        return ""
    return text.strip()


def _valid_mod(value: str) -> Optional[str]:
    """rate/volume должны быть вида '+0%'/'-10%'; иначе не передаём."""
    return value if value and _VALID_MOD.match(value) else None


def _split_chunks(text: str, limit: int = _MAX_CHUNK_CHARS) -> List[str]:
    """Делит длинный текст на части по предложениям/строкам."""
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    chunks: List[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        if len(current) + len(part) + 1 <= limit:
            current = f"{current} {part}".strip()
        else:
            if current:
                chunks.append(current)
            # часть сама длиннее лимита — режем жёстко
            while len(part) > limit:
                chunks.append(part[:limit])
                part = part[limit:]
            current = part
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# СИНТЕЗ
# ---------------------------------------------------------------------------
async def _synthesize(text: str, voice: str, output_path: str,
                      rate: Optional[str], volume: Optional[str]) -> None:
    import edge_tts  # type: ignore
    kwargs = {}
    if rate:
        kwargs["rate"] = rate
    if volume:
        kwargs["volume"] = volume
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    await communicate.save(output_path)


def _try_once(text: str, voice: str, output_path: str,
              rate: Optional[str], volume: Optional[str]) -> None:
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
    asyncio.run(_synthesize(text, voice, output_path, rate, volume))
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("edge-tts не создал аудиофайл.")


def _synthesize_with_retry(text: str, voice: str, output_path: str,
                           rate: Optional[str], volume: Optional[str],
                           retries: int = 4, base_delay: float = 2.0) -> None:
    """Повторяет запрос с экспоненциальным бэкоффом.

    "No audio was received" у edge-tts почти всегда = разовый сбой/лимит API,
    поэтому агрессивные быстрые повторы только усугубляют — делаем паузы.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            _try_once(text, voice, output_path, rate, volume)
            return
        except Exception as exc:  # noqa: BLE001 — API может сбоить разово
            last_exc = exc
            log(f"edge-tts: попытка {attempt}/{retries} не удалась: {exc}")
            if attempt < retries:
                delay = base_delay * (2 ** (attempt - 1))  # 2, 4, 8 с
                log(f"  пауза {delay:.0f}с перед повтором...")
                time.sleep(delay)
    raise RuntimeError(f"edge-tts не смог сгенерировать озвучку: {last_exc}")


def _concat_files(parts: List[str], output_path: str) -> None:
    """Склеивает MP3-части в один файл (MP3-фреймы конкатенируются)."""
    with open(output_path, "wb") as out:
        for part in parts:
            with open(part, "rb") as fh:
                while True:
                    buf = fh.read(1024 * 256)
                    if not buf:
                        break
                    out.write(buf)


def generate_voice(text: str, speaker: str = "ru-RU-DmitryNeural",
                   output_path: str = "", log_callback=None,
                   rate: str = "", volume: str = "") -> str:
    """Генерирует озвучку текста голосом edge-tts и сохраняет в MP3."""
    text = _clean_text(text)
    if not text:
        raise ValueError("Пустой текст для озвучки (или только пробелы/знаки).")

    output_path = output_path or config.AUDIO_PATH
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    voice = resolve_voice(speaker)
    rate = _valid_mod(rate or config.TTS_RATE)
    volume = _valid_mod(volume or config.TTS_VOLUME)

    log(f"Генерация Edge TTS (голос {voice}, {len(text)} симв.)...")

    chunks = _split_chunks(text)
    # Для одной части пишем прямо в output_path; для нескольких — во временные.
    if len(chunks) == 1:
        targets = [output_path]
    else:
        targets = [output_path + f".part{i}.mp3" for i in range(len(chunks))]

    try:
        for idx, (chunk, target) in enumerate(zip(chunks, targets), start=1):
            if len(chunks) > 1:
                log(f"  часть {idx}/{len(chunks)} ({len(chunk)} симв.)")
            if idx > 1:
                time.sleep(1.0)  # не частим запросами к API
            try:
                # Сначала штатные параметры; при сбое — без rate/volume.
                try:
                    _synthesize_with_retry(chunk, voice, target, rate, volume)
                except RuntimeError:
                    log("  повтор без rate/volume...")
                    _synthesize_with_retry(chunk, voice, target, None, None)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "edge-tts не смог озвучить текст. Обычно это разовое "
                    "ограничение API Microsoft — подожди минуту и попробуй снова. "
                    f"Детали: {exc}"
                ) from exc

        if len(chunks) > 1:
            _concat_files(targets, output_path)
            for part in targets:
                try:
                    os.remove(part)
                except OSError:
                    pass
    finally:
        for part in targets:
            if part != output_path and os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("edge-tts не создал аудиофайл (проверь интернет).")

    log(f"Озвучка сохранена: {output_path}")
    return output_path