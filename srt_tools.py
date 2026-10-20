"""Парсинг/запись SRT и TikTok-нарезка длинных реплик на короткие фразы."""

import os
import re
from typing import Iterable, List, Optional

# Таймкод SRT: 00:00:01,250  (допускаем и точку, и разный порядок цифр)
_TS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")


# ---------------------------------------------------------------------------
# ТАЙМКОДЫ
# ---------------------------------------------------------------------------
def srt_timestamp(seconds: float) -> str:
    """Секунды -> 'HH:MM:SS,mmm' (формат, который ждёт SRT/Resolve)."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    secs, millis = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def frames_to_timecode(frames: int, fps: float) -> str:
    """Кадры -> 'HH:MM:SS:FF' для Timeline.SetCurrentTimecode."""
    frames = max(0, int(frames))
    total_seconds, rem = divmod(frames, int(round(fps)))
    hours, rem_sec = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem_sec, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{rem:02d}"


def _ts_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


# ---------------------------------------------------------------------------
# ПАРСИНГ / ЗАПИСЬ
# ---------------------------------------------------------------------------
def parse_srt(path: str) -> List[dict]:
    """Читает SRT -> [{'start': float, 'end': float, 'text': str}, ...]."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SRT не найден: {path}")

    raw = open(path, "r", encoding="utf-8-sig", errors="replace").read()
    blocks = re.split(r"\n\s*\n", raw.strip())

    segments: List[dict] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if timing_idx < 0:
            continue
        times = _TS_RE.findall(lines[timing_idx])
        if len(times) < 2:
            continue
        start = _ts_to_seconds(*times[0])
        end = _ts_to_seconds(*times[1])
        text = " ".join(lines[timing_idx + 1:]).strip()
        if text and end > start:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def write_srt(segments: Iterable[dict], output_srt: str) -> None:
    """Пишет сегменты в SRT (UTF-8 без BOM, LF)."""
    os.makedirs(os.path.dirname(os.path.abspath(output_srt)), exist_ok=True)
    with open(output_srt, "w", encoding="utf-8", newline="\n") as fh:
        for idx, seg in enumerate(segments, start=1):
            fh.write(f"{idx}\n")
            fh.write(f"{srt_timestamp(seg['start'])} --> {srt_timestamp(seg['end'])}\n")
            fh.write(f"{seg['text']}\n\n")


# ---------------------------------------------------------------------------
# ВЫДЕЛЕНИЕ КЛЮЧЕВЫХ СЛОВ
# ---------------------------------------------------------------------------
def _apply_highlight(chunk: str, keywords: Optional[set], mode: str) -> str:
    """Ключевые слова -> КАПС (SRT не гарантирует цвет при импорте)."""
    if not keywords or mode != "upper":
        return chunk
    result = []
    for word in chunk.split():
        clean = word.strip(".,!?;:—-\"'()[]«»").lower()
        result.append(word.upper() if clean in keywords else word)
    return " ".join(result)


# ---------------------------------------------------------------------------
# НАРЕЗКА ДЛИННЫХ РЕПЛИК
# ---------------------------------------------------------------------------
def _chunk_segment(seg: dict, max_words: int, keywords: Optional[set],
                   highlight: str) -> List[dict]:
    """Дробит одну реплику на фразы <= max_words, деля время по длине текста."""
    words = seg["text"].split()
    if not words:
        return []
    if len(words) <= max_words:
        chunks = [" ".join(words)]
    else:
        chunks = [
            " ".join(words[i:i + max_words])
            for i in range(0, len(words), max_words)
        ]

    # Вес фразы ~ суммарная длина её символов: длинные фразы держатся дольше.
    weights = [max(1, len(c)) for c in chunks]
    total_weight = sum(weights)
    duration = seg["end"] - seg["start"]

    out: List[dict] = []
    cursor = seg["start"]
    for chunk, weight in zip(chunks, weights):
        chunk_dur = duration * weight / total_weight
        out.append({
            "start": cursor,
            "end": cursor + chunk_dur,
            "text": _apply_highlight(chunk, keywords, highlight),
        })
        cursor += chunk_dur
    return out


def format_srt_for_tiktok(
    input_srt_path: str,
    output_srt_path: str,
    max_words: int = 3,
    keywords: Optional[set] = None,
    highlight: str = "upper",
) -> List[dict]:
    """Превращает 'длинный' SRT от Whisper в динамичный TikTok-SRT.

    Таймкоды исходных реплик сохраняются как границы, а время внутри реплики
    делится между фразами пропорционально их длине. Возвращает новый список
    сегментов и пишет результат в output_srt_path.
    """
    max_words = max(1, min(int(max_words), 6))
    source = parse_srt(input_srt_path)
    if not source:
        raise RuntimeError(f"В SRT нет валидных реплик: {input_srt_path}")

    formatted: List[dict] = []
    for seg in source:
        formatted.extend(_chunk_segment(seg, max_words, keywords, highlight))

    if not formatted:
        raise RuntimeError("После нарезки не осталось субтитров.")

    write_srt(formatted, output_srt_path)
    return formatted