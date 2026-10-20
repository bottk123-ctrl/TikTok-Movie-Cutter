"""Оркестратор пайплайна TikTok/Shorts — только Python + FFmpeg (без Resolve).

Использование:
  * как модуль:  from build_timeline import run_pipeline
                 run_pipeline(query, api_key, output_dir, log_callback,
                              whisper_model, cancel_check, progress_callback,
                              video_url, apply_zoom, burn_subtitles,
                              voice_text, voice_speaker)
  * как CLI:      python build_timeline.py

6 шагов: клипы -> TTS -> Whisper SRT -> склейка -> zoom -> субтитры+аудио.
"""

import os
import threading
import time

import applog
import cancellation
import config
import ffmpeg_montage as fm
from audio_utils import get_audio_duration_ffprobe
from clip_fetcher import fetch_clips_for_pipeline, fetch_yt_clips
from srt_tools import format_srt_for_tiktok
from subtitles import transcribe_audio
from tts_generator import generate_voice

TOTAL_STEPS = 6


def _emit(progress_callback, step: int, message: str) -> None:
    applog.log(f"== Шаг {step}/{TOTAL_STEPS}: {message}")
    if not progress_callback:
        return
    try:
        progress_callback(step, TOTAL_STEPS, message)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# ШАГ 1: КЛИПЫ (Clip.Cafe -> yt-dlp -> локальная папка)
# ---------------------------------------------------------------------------
def _prepare_clip_dir(query: str = "", video_url: str = "") -> str:
    if config.AUTO_FETCH_CLIPS and config.CLIP_CAFE_API_KEY:
        try:
            paths = fetch_clips_for_pipeline()
            if paths:
                applog.log(f"Clip.Cafe: использую {len(paths)} скачанных клипов.")
                return config.CLIP_DOWNLOAD_DIR
            applog.log("Clip.Cafe не вернул клипов — пробую yt-dlp.")
        except cancellation.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            applog.log(f"Ошибка Clip.Cafe: {exc} — пробую yt-dlp.")

    if config.AUTO_FETCH_CLIPS and (video_url or (query and config.YT_SEARCH_FALLBACK)):
        try:
            clips = fetch_yt_clips(video_url=video_url, search_query=query)
            if clips:
                applog.log(f"yt-dlp: использую {len(clips)} клипов.")
                return config.CLIP_DOWNLOAD_DIR
            applog.log("yt-dlp не дал клипов — фолбэк на локальную папку.")
        except cancellation.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            applog.log(f"Ошибка yt-dlp: {exc} — фолбэк на локальную папку.")

    applog.log("Использую локальные клипы из VIDEO_DIR.")
    return config.VIDEO_DIR


# ---------------------------------------------------------------------------
# ШАГ 2: ОЗВУЧКА (Silero TTS)
# ---------------------------------------------------------------------------
def _prepare_voice(voice_text: str = "", voice_speaker: str = "") -> float:
    """Гарантирует наличие озвучки; возвращает её длительность в секундах."""
    if voice_text and voice_text.strip():
        generate_voice(voice_text, voice_speaker or config.TTS_VOICE, config.AUDIO_PATH)
    if not os.path.isfile(config.AUDIO_PATH):
        raise RuntimeError(
            f"Озвучка не найдена: {config.AUDIO_PATH}. "
            "Сгенерируй её кнопкой «Сгенерировать озвучку» в GUI или задай текст."
        )
    duration = get_audio_duration_ffprobe(config.AUDIO_PATH)
    applog.log(f"Озвучка: {config.AUDIO_PATH} ({duration:.3f} сек).")
    return duration


# ---------------------------------------------------------------------------
# ШАГ 3: СУБТИТРЫ (Whisper -> TikTok SRT)
# ---------------------------------------------------------------------------
def _run_subtitles_stage() -> str:
    if not config.ENABLE_SUBTITLES:
        applog.log("Субтитры отключены (ENABLE_SUBTITLES=0).")
        return ""
    try:
        transcribe_audio(config.AUDIO_PATH, output_srt=config.SUBTITLES_PATH)
        format_srt_for_tiktok(
            config.SUBTITLES_PATH,
            config.SUBTITLES_TIKTOK_PATH,
            max_words=config.SUBTITLE_MAX_WORDS,
            keywords=config.SUBTITLE_KEYWORDS,
            highlight=config.SUBTITLE_HIGHLIGHT,
        )
        applog.log(f"TikTok-субтитры готовы: {config.SUBTITLES_TIKTOK_PATH}")
        return config.SUBTITLES_TIKTOK_PATH
    except cancellation.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        applog.log(f"ПРЕДУПРЕЖДЕНИЕ: субтитры не сгенерированы: {exc}")
        return ""


# ---------------------------------------------------------------------------
# ЯДРО ПАЙПЛАЙНА
# ---------------------------------------------------------------------------
def _execute_pipeline(progress_callback=None, video_url: str = "",
                      search_query: str = "", apply_zoom: bool = True,
                      burn_subtitles: bool = True, voice_text: str = "",
                      voice_speaker: str = "") -> bool:
    started = time.time()
    os.makedirs(config.WORK_DIR, exist_ok=True)

    # --- 1. Клипы ---
    _emit(progress_callback, 1, "Поиск и скачивание клипов")
    clip_dir = _prepare_clip_dir(query=search_query, video_url=video_url)

    # --- 2. Озвучка (TTS) ---
    _emit(progress_callback, 2, "Озвучка (Silero TTS)")
    audio_duration = _prepare_voice(voice_text, voice_speaker)

    # --- 3. Субтитры (Whisper) ---
    _emit(progress_callback, 3, "Распознавание речи (Whisper)")
    srt_file = _run_subtitles_stage()

    # --- 4. Склейка и обрезка клипов ---
    _emit(progress_callback, 4, "Склейка видеоряда (ffmpeg)")
    temp_video = os.path.join(config.WORK_DIR, "temp_video.mp4")
    fm.concat_and_trim_clips(clip_dir, audio_duration, temp_video)

    # --- 5. Динамический zoom ---
    if apply_zoom:
        _emit(progress_callback, 5, "Динамический zoom (punch-in)")
        zoomed = os.path.join(config.WORK_DIR, "zoomed.mp4")
        fm.apply_dynamic_zoom(temp_video, zoomed)
        video_for_final = zoomed
    else:
        _emit(progress_callback, 5, "Zoom отключён")
        video_for_final = temp_video

    # --- 6. Субтитры + аудио ---
    _emit(progress_callback, 6, "Субтитры и аудио-микс")
    bgm = fm.pick_random_bgm(config.MUSIC_DIR)
    if not bgm and config.BGM_PATH and os.path.isfile(config.BGM_PATH):
        bgm = config.BGM_PATH  # фолбэк на одиночный файл
    fm.add_subtitles_and_audio(
        video_for_final,
        srt_file if burn_subtitles else "",
        config.AUDIO_PATH,
        bgm,
        config.OUTPUT_PATH,
    )

    applog.log(f"ГОТОВО за {time.time() - started:.1f} сек. "
               f"Видео: {config.OUTPUT_PATH}")
    return True


def _watch_cancel(cancel_check, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            if cancel_check():
                cancellation.request_cancel()
                return
        except Exception:  # noqa: BLE001
            return
        stop_event.wait(0.3)


def run_pipeline(query: str = "", api_key: str = "",
                 output_dir: str = "", log_callback=None,
                 whisper_model=None, cancel_check=None, progress_callback=None,
                 video_url=None, apply_zoom: bool = True,
                 burn_subtitles: bool = True,
                 voice_text: str = "", voice_speaker: str = "") -> bool:
    """Точка входа для GUI/скриптов."""
    if api_key is not None:
        config.CLIP_CAFE_API_KEY = (api_key or "").strip()
    if query:
        config.CLIP_SEARCH_MOVIE_TITLE = query.strip()
    if output_dir:
        config.OUTPUT_DIR = output_dir
        config.OUTPUT_PATH = os.path.join(output_dir, "final_video.mp4")
    if whisper_model:
        config.WHISPER_MODEL = whisper_model
    if video_url is not None:
        config.YT_VIDEO_URL = (video_url or "").strip()

    cancellation.reset()
    if log_callback:
        applog.register_log_sink(log_callback)

    stop_watcher = threading.Event()
    if cancel_check:
        threading.Thread(target=_watch_cancel,
                         args=(cancel_check, stop_watcher), daemon=True).start()

    try:
        return _execute_pipeline(
            progress_callback=progress_callback,
            video_url=config.YT_VIDEO_URL,
            search_query=config.CLIP_SEARCH_MOVIE_TITLE,
            apply_zoom=apply_zoom,
            burn_subtitles=burn_subtitles,
            voice_text=voice_text,
            voice_speaker=voice_speaker,
        )
    except cancellation.CancelledError as exc:
        applog.log(f"ОТМЕНЕНО: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        applog.log(f"ОШИБКА: {exc}")
        return False
    finally:
        stop_watcher.set()
        if log_callback:
            applog.unregister_log_sink(log_callback)


def main() -> int:
    applog.log("Запуск пайплайна (CLI).")
    return 0 if run_pipeline() else 1


if __name__ == "__main__":
    raise SystemExit(main())