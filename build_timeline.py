"""Оркестратор пайплайна TikTok/Shorts — только Python + FFmpeg (без Resolve).

Использование:
  * как модуль:  from build_timeline import run_pipeline
                 run_pipeline(query, api_key, output_dir, log_callback,
                              whisper_model, cancel_check, progress_callback,
                              video_url, apply_zoom, burn_subtitles,
                              voice_text, voice_speaker)
  * как CLI:      python build_timeline.py --query "Matrix" --voice-text-file text.txt
                  python build_timeline.py --check   (только предпроверка окружения)

6 шагов: клипы -> TTS -> Whisper SRT -> склейка -> zoom -> субтитры+аудио.
"""

import argparse
import importlib.util
import json
import os
import threading
import time
from typing import List, Optional, Tuple

import applog
import cancellation
import config
import ffmpeg_montage as fm
from audio_utils import find_ffmpeg, find_ffprobe, get_audio_duration_ffprobe
from clip_fetcher import fetch_clips_for_pipeline, fetch_yt_clips
from srt_tools import format_srt_for_tiktok
from subtitles import transcribe_audio
from text_utils import normalize_text

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
# ПРЕДПРОВЕРКА ОКРУЖЕНИЯ
# ---------------------------------------------------------------------------
def _has_local_clips() -> bool:
    """Есть ли готовые клипы в локальной папке."""
    if not os.path.isdir(config.VIDEO_DIR):
        return False
    return any(
        os.path.splitext(name)[1].lower() in config.VIDEO_EXTENSIONS
        for name in os.listdir(config.VIDEO_DIR)
    )


def preflight(video_url: str = "", query: str = "", voice_text: str = "",
              api_key: Optional[str] = None,
              need_subtitles: Optional[bool] = None) -> List[str]:
    """Проверяет окружение ДО запуска. Возвращает список проблем (пусто = всё ок).

    Смысл: падать сразу с понятным текстом, а не на середине монтажа.
    need_subtitles — нужны ли субтитры в этом прогоне (по умолчанию ENABLE_SUBTITLES).
    """
    problems: List[str] = []

    try:
        find_ffmpeg()
    except Exception as exc:  # noqa: BLE001
        problems.append(str(exc))
    try:
        find_ffprobe()
    except Exception as exc:  # noqa: BLE001
        problems.append(str(exc))

    key = config.CLIP_CAFE_API_KEY if api_key is None else api_key
    key_available = config.AUTO_FETCH_CLIPS and bool(key)
    yt_requested = config.AUTO_FETCH_CLIPS and bool(
        video_url or (query and config.YT_SEARCH_FALLBACK)
    )
    yt_present = importlib.util.find_spec("yt_dlp") is not None
    has_local = _has_local_clips()

    # yt-dlp — фолбэк после Clip.Cafe. Без него не стартуем только если он
    # остался единственным источником клипов.
    if yt_requested and not yt_present:
        if key_available or has_local:
            applog.log("ПРЕДУПРЕЖДЕНИЕ: yt-dlp не установлен — "
                       "фолбэк на видео/поиск не сработает.")
        else:
            problems.append(
                "yt-dlp не установлен — нечем качать видео. "
                "Установи: pip install yt-dlp"
            )

    if not (key_available or has_local or (yt_requested and yt_present)) \
            and not any("yt-dlp" in p for p in problems):
        problems.append(
            "Нет источника клипов: укажи фильм/запрос, ссылку, API-ключ Clip.Cafe "
            f"или положи видео в {config.VIDEO_DIR}."
        )

    has_voice = bool(normalize_text(voice_text or "")) or os.path.isfile(config.AUDIO_PATH)
    if not has_voice:
        problems.append(
            f"Нет озвучки: задай текст (Qwen3-TTS) или положи файл {config.AUDIO_PATH}."
        )

    subtitles_needed = config.ENABLE_SUBTITLES if need_subtitles is None else need_subtitles
    if subtitles_needed and importlib.util.find_spec("faster_whisper") is None:
        problems.append(
            "faster-whisper не установлен — субтитры не сделать. "
            "Установи: pip install faster-whisper"
        )

    return problems


def _log_problems(problems: List[str]) -> None:
    for problem in problems:
        applog.log(f"ПРЕДПРОВЕРКА: {problem}")


# ---------------------------------------------------------------------------
# ШАГ 1: КЛИПЫ (Clip.Cafe -> yt-dlp -> локальная папка)
# ---------------------------------------------------------------------------
def _prepare_clip_dir(query: str = "", video_url: str = "") -> Tuple[str, Optional[List[str]]]:
    """Возвращает (папка, явный список клипов или None = все видео из папки).

    Явный список нужен, чтобы в монтаж не попали клипы прошлых роликов.
    """
    if config.AUTO_FETCH_CLIPS and config.CLIP_CAFE_API_KEY:
        try:
            paths = fetch_clips_for_pipeline()
            if paths:
                applog.log(f"Clip.Cafe: использую {len(paths)} скачанных клипов.")
                return config.CLIP_DOWNLOAD_DIR, paths
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
                return config.CLIP_DOWNLOAD_DIR, clips
            applog.log("yt-dlp не дал клипов — фолбэк на локальную папку.")
        except cancellation.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            applog.log(f"Ошибка yt-dlp: {exc} — фолбэк на локальную папку.")

    applog.log("Использую локальные клипы из VIDEO_DIR.")
    return config.VIDEO_DIR, None


# ---------------------------------------------------------------------------
# ШАГ 2: ОЗВУЧКА (Qwen3-TTS)
# ---------------------------------------------------------------------------
def _prepare_voice(voice_text: str = "", voice_speaker: str = "") -> float:
    """Гарантирует наличие озвучки; возвращает её длительность в секундах."""
    text = (voice_text or "").strip()
    speaker = (voice_speaker or config.TTS_VOICE).strip()
    meta_path = config.AUDIO_PATH + ".json"
    if text:
        meta: dict = {}
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            meta = {}
        same = (meta.get("text") == normalize_text(text)
                and meta.get("speaker") == speaker)
        if os.path.isfile(config.AUDIO_PATH) and same:
            applog.log("Текст и голос не изменились — использую готовую озвучку.")
        else:
            # Ленивый импорт: tts_generator тянет torch, а в exe torch исключён.
            from tts_generator import generate_voice
            generate_voice(text, speaker, config.AUDIO_PATH)
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
def _run_subtitles_stage(burn_subtitles: bool = True) -> str:
    if not burn_subtitles:
        applog.log("Субтитры не вжигаются — Whisper пропущен.")
        return ""
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

    problems = preflight(video_url=video_url, query=search_query, voice_text=voice_text,
                         need_subtitles=burn_subtitles and config.ENABLE_SUBTITLES)
    if problems:
        _log_problems(problems)
        raise RuntimeError(
            "Предпроверка не пройдена: " + " | ".join(problems)
        )

    # --- 1. Клипы ---
    _emit(progress_callback, 1, "Поиск и скачивание клипов")
    clip_dir, clip_paths = _prepare_clip_dir(query=search_query, video_url=video_url)

    # --- 2. Озвучка (TTS) ---
    _emit(progress_callback, 2, "Озвучка (Qwen3-TTS)")
    audio_duration = _prepare_voice(voice_text, voice_speaker)

    # --- 3. Субтитры (Whisper) ---
    _emit(progress_callback, 3, "Распознавание речи (Whisper)")
    srt_file = _run_subtitles_stage(burn_subtitles)

    # --- 4. Склейка и обрезка клипов ---
    _emit(progress_callback, 4, "Склейка видеоряда (ffmpeg)")
    temp_video = os.path.join(config.WORK_DIR, "temp_video.mp4")
    fm.concat_and_trim_clips(clip_dir, audio_duration, temp_video, clips=clip_paths)

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
    # Пустой ключ НЕ затирает ключ из окружения/.env (иначе CLI остаётся без Clip.Cafe).
    if api_key and api_key.strip():
        config.CLIP_CAFE_API_KEY = api_key.strip()
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_timeline.py",
        description="Сборка вертикального видео (клипы -> TTS -> субтитры -> монтаж).",
    )
    parser.add_argument("-q", "--query", default="",
                        help="название фильма / поисковый запрос")
    parser.add_argument("-u", "--url", default="",
                        help="ссылка на видео для yt-dlp")
    parser.add_argument("--key", default="",
                        help="API-ключ Clip.Cafe (по умолчанию из .env)")
    parser.add_argument("-o", "--output", default="",
                        help="папка для final_video.mp4")
    parser.add_argument("--voice-text", default="",
                        help="текст озвучки (Qwen3-TTS)")
    parser.add_argument("--voice-text-file", default="",
                        help="файл с текстом озвучки (UTF-8)")
    parser.add_argument("--voice", default="",
                        help="голос: serena / aiden / ryan / vivian")
    parser.add_argument("--whisper", default="",
                        help="модель Whisper: tiny / base / small / medium")
    parser.add_argument("--no-zoom", action="store_true",
                        help="без punch-in zoom")
    parser.add_argument("--no-subtitles", action="store_true",
                        help="не вжигать субтитры")
    parser.add_argument("--check", action="store_true",
                        help="только предпроверка окружения, без монтажа")
    return parser


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    voice_text = args.voice_text or ""
    if args.voice_text_file:
        try:
            voice_text = _read_text_file(args.voice_text_file)
        except OSError as exc:
            applog.log(f"Не удалось прочитать текст озвучки: {exc}")
            return 2

    if args.check:
        applog.log("Предпроверка окружения (без монтажа).")
        # Проверяем то, что реально пойдёт в работу: аргумент -> .env/окружение.
        problems = preflight(
            video_url=args.url or config.YT_VIDEO_URL,
            query=args.query or config.CLIP_SEARCH_MOVIE_TITLE,
            voice_text=voice_text,
            api_key=args.key or None,
            need_subtitles=(not args.no_subtitles) and config.ENABLE_SUBTITLES,
        )
        if problems:
            _log_problems(problems)
            applog.log(f"Проблем найдено: {len(problems)}.")
            return 1
        applog.log("Предпроверка пройдена — можно запускать монтаж.")
        return 0

    applog.log("Запуск пайплайна (CLI).")
    ok = run_pipeline(
        query=args.query,
        api_key=args.key,
        output_dir=args.output,
        whisper_model=args.whisper or None,
        video_url=args.url or None,
        apply_zoom=not args.no_zoom,
        burn_subtitles=not args.no_subtitles,
        voice_text=voice_text,
        voice_speaker=args.voice,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())