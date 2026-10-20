"""Весь монтаж через FFmpeg: склейка/обрезка клипов, punch-in zoom,
вжигание субтитров и микс аудио. Без DaVinci Resolve."""

import os
import random
import tempfile
from typing import List, Optional

import config
from applog import log
from audio_utils import find_ffmpeg, get_audio_duration_ffprobe, run_process
from cancellation import check

TARGET_W = config.TARGET_WIDTH
TARGET_H = config.TARGET_HEIGHT
TARGET_FPS = int(round(config.TARGET_FPS))


# ---------------------------------------------------------------------------
# ЗАПУСК FFMPEG (с поддержкой отмены)
# ---------------------------------------------------------------------------
def _run(cmd: List[str], desc: str = "ffmpeg") -> None:
    """Запускает ffmpeg с поддержкой отмены (см. audio_utils.run_process)."""
    run_process(cmd, desc)


def _list_videos(clips_dir: str) -> List[str]:
    if not os.path.isdir(clips_dir):
        raise FileNotFoundError(f"Папка с клипами не найдена: {clips_dir}")
    files = [
        os.path.join(clips_dir, name)
        for name in os.listdir(clips_dir)
        if os.path.splitext(name)[1].lower() in config.VIDEO_EXTENSIONS
    ]
    files.sort()
    return files


def pick_random_bgm(music_dir: str) -> Optional[str]:
    """Случайный трек из папки с музыкой (или None, если папки/треков нет)."""
    if not music_dir or not os.path.isdir(music_dir):
        return None
    tracks = [
        os.path.join(music_dir, name)
        for name in os.listdir(music_dir)
        if os.path.splitext(name)[1].lower() in config.AUDIO_EXTENSIONS
    ]
    if not tracks:
        return None
    chosen = random.choice(tracks)
    log(f"Фоновая музыка (случайно): {os.path.basename(chosen)}")
    return chosen


# ---------------------------------------------------------------------------
# ШАГ 4: СКЛЕЙКА И ОБРЕЗКА КЛИПОВ ПОД ДЛИНУ АУДИО
# ---------------------------------------------------------------------------
def concat_and_trim_clips(clips_dir: str, audio_duration: float,
                          output_video_path: str, work_dir: Optional[str] = None) -> str:
    """Приводит клипы к 1080x1920@30 H.264, зацикливает/обрезает под длину
    аудио и склеивает в один немой видеофайл. Возвращает путь."""
    ffmpeg = find_ffmpeg()
    clips = _list_videos(clips_dir)
    if not clips:
        raise RuntimeError(f"В папке нет видео: {clips_dir}")

    audio_duration = float(audio_duration)
    if audio_duration <= 0:
        raise RuntimeError("Длительность аудио должна быть больше нуля.")

    work_dir = work_dir or os.path.join(tempfile.gettempdir(), "tiktok_montage")
    seg_dir = os.path.join(work_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)

    durations: dict = {}

    def clip_duration(path: str) -> float:
        if path not in durations:
            durations[path] = get_audio_duration_ffprobe(path)
        return durations[path]

    # Приведение к вертикали: масштаб с заполнением + центральный кроп + fps.
    vf = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
          f"crop={TARGET_W}:{TARGET_H},setsar=1,fps={TARGET_FPS}")

    seg_files: List[str] = []
    remaining = audio_duration
    cursor = 0
    guard = 0
    while remaining > 1e-3:
        check()
        clip = clips[cursor % len(clips)]
        take = min(clip_duration(clip), remaining)
        if take <= 1e-3:
            cursor += 1
            guard += 1
            if guard > len(clips) * 1000:
                raise RuntimeError("Не удалось набрать видеоряд: клипы нулевой длины?")
            continue

        seg = os.path.join(seg_dir, f"seg_{len(seg_files):04d}.mp4")
        _run([
            ffmpeg, "-y", "-i", clip, "-t", f"{take:.3f}",
            "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", seg,
        ], f"сегмент {len(seg_files) + 1} ({take:.2f}s)")

        seg_files.append(seg)
        remaining -= take
        cursor += 1

    # Конкатенация одинаково закодированных сегментов (без перекодирования).
    list_path = os.path.join(work_dir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for seg in seg_files:
            safe = seg.replace("\\", "/").replace("'", "'\\''")
            fh.write(f"file '{safe}'\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
    _run([
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", output_video_path,
    ], "склейка видеоряда")

    log(f"Немой видеоряд собран: {output_video_path} "
        f"({len(seg_files)} сегментов, ~{audio_duration:.2f}s).")
    return output_video_path


# ---------------------------------------------------------------------------
# ШАГ 5: ДИНАМИЧЕСКИЙ ZOOM (PUNCH-IN)
# ---------------------------------------------------------------------------
def apply_dynamic_zoom(input_video: str, output_video: str,
                       intensity: Optional[float] = None,
                       step: Optional[float] = None) -> str:
    """Плавный нарастающий zoom через фильтр zoompan."""
    ffmpeg = find_ffmpeg()
    intensity = float(intensity if intensity is not None else config.ZOOM_INTENSITY)
    step = float(step if step is not None else config.ZOOM_STEP)

    zoompan = (
        f"zoompan=z='min(zoom+{step:.6f},{intensity})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={TARGET_W}x{TARGET_H}:fps={TARGET_FPS}"
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_video)), exist_ok=True)
    _run([
        ffmpeg, "-y", "-i", input_video, "-vf", zoompan,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", output_video,
    ], "punch-in zoom")
    log(f"Zoom {intensity}x применён: {output_video}")
    return output_video


# ---------------------------------------------------------------------------
# ШАГ 6: СУБТИТРЫ + МИКС АУДИО
# ---------------------------------------------------------------------------
def _hex_to_ass(color: str) -> str:
    """'#RRGGBB' -> ASS-цвет '&H00BBGGRR'."""
    value = (color or "#FFFFFF").lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except (ValueError, IndexError):
        r = g = b = 255
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _escape_filter_path(path: str) -> str:
    """Экранирование пути для фильтра subtitles (Windows-диск 'C:\\')."""
    p = os.path.abspath(path).replace("\\", "/")
    return p.replace(":", "\\:")


def _force_style() -> str:
    """Стиль субтитров: жирный Impact, белый с чёрной обводкой, по центру."""
    fill = _hex_to_ass(config.SUBTITLE_FILL_COLOR)
    outline = _hex_to_ass(config.SUBTITLE_OUTLINE_COLOR)
    return (
        f"FontName={config.SUBTITLE_FONT},FontSize={config.SUBTITLE_ASS_FONT_SIZE},"
        f"PrimaryColour={fill},OutlineColour={outline},BorderStyle=1,"
        f"Outline={config.SUBTITLE_OUTLINE_WIDTH},Bold=1,"
        f"Alignment={config.SUBTITLE_ALIGNMENT},MarginV={config.SUBTITLE_MARGIN_V}"
    )


def add_subtitles_and_audio(video_path: str, srt_path: str, voice_path: str,
                            bgm_path: Optional[str], final_output_path: str,
                            bgm_volume: Optional[float] = None,
                            voice_volume: Optional[float] = None) -> str:
    """Вжигает SRT (если есть) и миксует голос + музыку -> финальный MP4.

    Голос — 100%, музыка — 20% (настраивается). Если музыки нет, используется
    только голос.
    """
    ffmpeg = find_ffmpeg()
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Видео не найдено: {video_path}")
    if not os.path.isfile(voice_path):
        raise FileNotFoundError(f"Голос не найден: {voice_path}")

    bgm_volume = config.BGM_VOLUME if bgm_volume is None else float(bgm_volume)
    voice_volume = config.VOICE_VOLUME if voice_volume is None else float(voice_volume)
    has_bgm = bool(bgm_path) and os.path.isfile(bgm_path)
    has_srt = bool(srt_path) and os.path.isfile(srt_path)

    inputs = ["-i", video_path, "-i", voice_path]
    if has_bgm:
        # -stream_loop -1: музыка короче видео -> зацикливаем на всю длину.
        inputs += ["-stream_loop", "-1", "-i", bgm_path]

    filters: List[str] = []
    if has_srt:
        subs = (f"subtitles=filename='{_escape_filter_path(srt_path)}':"
                f"force_style='{_force_style()}'")
        filters.append(f"[0:v]{subs}[v]")
    else:
        filters.append("[0:v]null[v]")

    if has_bgm:
        filters.append(f"[1:a]volume={voice_volume}[voice]")
        filters.append(f"[2:a]volume={bgm_volume}[bgm]")
        filters.append("[voice][bgm]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        filters.append(f"[1:a]volume={voice_volume}[a]")

    os.makedirs(os.path.dirname(os.path.abspath(final_output_path)), exist_ok=True)
    cmd = [
        ffmpeg, "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", final_output_path,
    ]
    _run(cmd, "субтитры + аудио-микс")
    log(f"Финальное видео готово: {final_output_path}")
    return final_output_path