# TikTok Movie Cutter

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)]()
[![build](https://github.com/bottk123-ctrl/TikTok-Movie-Cutter/actions/workflows/build.yml/badge.svg)](https://github.com/bottk123-ctrl/TikTok-Movie-Cutter/actions/workflows/build.yml)

Автономный пайплайн для сборки вертикальных видео (TikTok / Shorts / Reels)
из нарезок фильмов — **только Python + FFmpeg**, без DaVinci Resolve и без
тяжёлого видеоредактора: скачивание клипов, озвучка, распознавание речи,
субтитры, зум и финальный рендер в один клик, с графическим интерфейсом и сборкой в `.exe`.

## Возможности

- **GUI** на `customtkinter`: поля запроса и ссылок, выбор папки вывода, прогресс по шагам, лог в реальном времени, кнопки «Отмена» и «Открыть папку», контекстное меню и горячие клавиши в текстовых полях.
- **Clip.Cafe** — легальный поиск/скачивание клипов; при отсутствии ключа — фолбэк.
- **yt-dlp** — скачивание по ссылке (YouTube и др.) или поиск первого видео по запросу, авто-нарезка на клипы.
- **Озвучка (edge-tts)** — нейроголоса Microsoft: `ru-RU-DmitryNeural`, `ru-RU-SvetlanaNeural`, `ru-RU-DariyaNeural`; MP3 сразу, без тяжёлых моделей (нужен интернет). При сбое API — авто-повтор с паузами.
- **Whisper** (`faster-whisper`) — распознавание речи (модели `base` / `small` / `medium`).
- **TikTok-субтитры** — нарезка на короткие фразы, выделение ключевых слов, вжигание в видео (ASS-стиль: Impact, белый/чёрная обводка).
- **Динамика** — плавный punch-in zoom (`zoompan`).
- **Аудио** — микс голоса (100%) и музыки (20%) через `amix`.
- **Отмена** тяжёлых операций (ffmpeg, скачивание, распознавание) через thread-safe флаг.

## Требования

- **Python 3.10+**
- **ffmpeg и ffprobe** в PATH (или `ffmpeg.exe`/`ffprobe.exe` рядом с приложением / в `bin\`).
- **Whisper-модель** скачивается автоматически при первом запуске распознавания.

## Установка

```powershell
pip install -r requirements.txt
# ffmpeg (вместе с ffprobe), если ещё не установлен:
winget install Gyan.FFmpeg
```

## Запуск

```powershell
python gui.py            # графический интерфейс
python build_timeline.py # консольный режим
```

## Пайплайн (6 шагов)

1. **Клипы** — Clip.Cafe → yt-dlp (ссылка/поиск) → локальная папка `assets\clips`.
2. **Озвучка** — Silero TTS (или готовая `assets\voice.mp3`).
3. **Субтитры** — Whisper → сырой SRT → TikTok SRT (короткие фразы).
4. **Склейка** — `ffmpeg`: клипы приводятся к 1080x1920@30, зацикливаются/обрезаются под длину аудио, склеиваются.
5. **Zoom** — плавный punch-in (`zoompan`), отключается чекбоксом.
6. **Финал** — вжигание субтитров (`subtitles` + `force_style`) и микс голоса+музыки → `output\final_video.mp4`.

## Сборка в .exe

```powershell
build_exe.bat
# результат: dist\TikTokMovieCutter\TikTokMovieCutter.exe
```

Положи `ffmpeg.exe` и `ffprobe.exe` рядом с `.exe` (или в `bin\`, или в PATH).
Папки `assets\` (`voice.mp3`, `music.mp3`, `clips\`) и `output\` создаются рядом с приложением.

## Настройки (`.env` рядом с приложением)

GUI сохраняет автоматически: `CLIP_CAFE_API_KEY`, `TIKTOK_OUTPUT_DIR`,
`WHISPER_MODEL`, `TTS_VOICE`, `APPLY_ZOOM`, `BURN_SUBTITLES`, `LAST_QUERY`, `YT_VIDEO_URL`.

Основные переменные окружения: `TIKTOK_AUDIO`, `TIKTOK_CLIPS`, `TIKTOK_BGM`,
`WHISPER_MODEL`, `WHISPER_LANGUAGE`, `ENABLE_SUBTITLES`, `SUBTITLE_MAX_WORDS`,
`SUBTITLE_KEYWORDS`, `SUBTITLE_FONT`, `SUBTITLE_FILL_COLOR`, `SUBTITLE_OUTLINE_COLOR`,
`SUBTITLE_ASS_FONT_SIZE`, `SUBTITLE_ALIGNMENT`, `ZOOM_INTENSITY`, `ZOOM_STEP`,
`APPLY_ZOOM`, `BGM_VOLUME`, `VOICE_VOLUME`, `BURN_SUBTITLES`, `AUTO_FETCH_CLIPS`.

## Структура проекта

| Файл | Назначение |
|------|-----------|
| `gui.py` | Десктопный интерфейс |
| `build_timeline.py` | Оркестратор (`run_pipeline`) |
| `ffmpeg_montage.py` | Склейка, zoom, субтитры, аудио-микс (FFmpeg) |
| `audio_utils.py` | ffprobe/ffmpeg, длительность, запуск процессов |
| `subtitles.py` | Whisper → SRT |
| `srt_tools.py` | Нарезка/парсинг SRT |
| `tts_generator.py` | Озвучка (edge-tts) |
| `clip_fetcher.py` | Clip.Cafe API + yt-dlp фолбэк |
| `yt_fetcher.py` | Скачивание (yt-dlp) и нарезка клипов |
| `cancellation.py` | Флаг отмены |
| `applog.py` | Лог и приёмники (для GUI) |
| `config.py` | Конфигурация и пути |

## Правовая заметка

Clip.Cafe API закрыт для новых ключей. Соблюдайте условия сервисов и авторское
право; при отсутствии ключа пайплайн работает с локальными видеофайлами.
