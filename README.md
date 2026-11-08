# 🎬 TikTok Movie Cutter

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)]()
[![build](https://github.com/bottk123-ctrl/TikTok-Movie-Cutter/actions/workflows/build.yml/badge.svg)](https://github.com/bottk123-ctrl/TikTok-Movie-Cutter/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-contained pipeline for building vertical videos (**TikTok / Shorts /
Reels**) from movie clips — **pure Python + FFmpeg**, no DaVinci Resolve and no
heavy editor required: clip fetching, voice-over, speech recognition, subtitles,
zoom, and the final render in a single click, with a GUI and a `.exe` build.

> 📸 *Add a demo GIF or screenshot here — it makes the project much more
> convincing to reviewers and clients.*

## ✨ Features

- **GUI** built with `customtkinter`: query and link fields, output-folder picker,
  step-by-step progress, a live log, "Cancel" and "Open folder" buttons, plus a
  context menu and keyboard shortcuts in the text fields.
- **Clip.Cafe** — legal clip search and download; falls back automatically when no
  API key is set.
- **yt-dlp** — download by link (YouTube and more) or search the first matching
  video by query, then auto-slice it into clips.
- **Voice-over (Qwen3-TTS)** — local, GPU-accelerated neural TTS via
  `faster-qwen3-tts` with the *CustomVoice* model (`serena`, `aiden`, `ryan`,
  `vivian`; legacy voice names are mapped to aliases). Exports MP3 directly, no
  cloud service required.
- **Whisper** (`faster-whisper`) — speech recognition (`tiny` / `base` / `small` /
  `medium`, CPU by default).
- **TikTok subtitles** — split into short phrases, keyword highlighting, and burned
  into the video (ASS style: Impact, white fill with a black outline).
- **Motion** — smooth punch-in zoom (`zoompan`), toggleable.
- **Audio** — mixes voice (100%) and background music (15%) via `amix`.
- **Cancellation** — heavy operations (ffmpeg, download, recognition) can be
  aborted through a thread-safe flag.

## 🧩 Requirements

- **Python 3.10+**
- **ffmpeg and ffprobe** in `PATH` (or `ffmpeg.exe` / `ffprobe.exe` next to the
  app, or in `bin\`).
- **NVIDIA GPU with CUDA** for the Qwen3-TTS voice-over (the model runs on
  `cuda`). If you only need subtitles, the rest of the pipeline works on CPU.
- **Whisper model** — downloaded automatically on the first recognition run.

## 📦 Install

```powershell
pip install -r requirements.txt
# ffmpeg (with ffprobe), if not installed yet:
winget install Gyan.FFmpeg
# PyTorch for your CUDA version (required by Qwen3-TTS), for example:
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

## ▶ Usage

```powershell
python gui.py            # graphical interface
python build_timeline.py # console mode
```

## 🔄 Pipeline (6 steps)

1. **Clips** — Clip.Cafe → yt-dlp (link / search) → local `assets\clips` folder.
2. **Voice-over** — Qwen3-TTS (or drop in a ready `assets\voice.mp3`).
3. **Subtitles** — Whisper → raw SRT → TikTok SRT (short phrases).
4. **Assembly** — `ffmpeg`: clips are normalized to 1080x1920@30, looped/trimmed to
   the audio length, and concatenated.
5. **Zoom** — smooth punch-in (`zoompan`), disabled by a checkbox.
6. **Final** — burn subtitles (`subtitles` + `force_style`) and mix voice + music →
   `output\final_video.mp4`.

## 🛠 Build a standalone .exe

```powershell
build_exe.bat
# output: dist\TikTokMovieCutter\TikTokMovieCutter.exe
```

Place `ffmpeg.exe` and `ffprobe.exe` next to the `.exe` (or in `bin\`, or in
`PATH`). The `assets\` (`voice.mp3`, `music.mp3`, `clips\`) and `output\` folders
are created next to the application.

## ⚙️ Configuration (`.env` next to the app)

The GUI saves automatically: `CLIP_CAFE_API_KEY`, `TIKTOK_OUTPUT_DIR`,
`WHISPER_MODEL`, `TTS_VOICE`, `APPLY_ZOOM`, `BURN_SUBTITLES`, `LAST_QUERY`,
`YT_VIDEO_URL`.

Main environment variables: `TIKTOK_AUDIO`, `TIKTOK_CLIPS`, `TIKTOK_BGM`,
`WHISPER_MODEL`, `WHISPER_LANGUAGE`, `ENABLE_SUBTITLES`, `SUBTITLE_MAX_WORDS`,
`SUBTITLE_KEYWORDS`, `SUBTITLE_FONT`, `SUBTITLE_FILL_COLOR`,
`SUBTITLE_OUTLINE_COLOR`, `SUBTITLE_ASS_FONT_SIZE`, `SUBTITLE_ALIGNMENT`,
`ZOOM_INTENSITY`, `ZOOM_STEP`, `APPLY_ZOOM`, `BGM_VOLUME`, `VOICE_VOLUME`,
`BURN_SUBTITLES`, `AUTO_FETCH_CLIPS`, `TTS_VOICE`, `HF_HOME`.

## 🏗 Project structure

| File | Purpose |
|------|---------|
| `gui.py` | Desktop interface |
| `build_timeline.py` | Orchestrator (`run_pipeline`) |
| `ffmpeg_montage.py` | Assembly, zoom, subtitles, audio mix (FFmpeg) |
| `audio_utils.py` | ffprobe/ffmpeg helpers, duration, process launching |
| `subtitles.py` | Whisper → SRT |
| `srt_tools.py` | SRT splitting / parsing |
| `tts_generator.py` | Voice-over (Qwen3-TTS) |
| `clip_fetcher.py` | Clip.Cafe API + yt-dlp fallback |
| `yt_fetcher.py` | Download (yt-dlp) and clip slicing |
| `cancellation.py` | Cancellation flag |
| `applog.py` | Logging and sinks (for the GUI) |
| `config.py` | Configuration and paths |

## ⚖️ Legal note

The Clip.Cafe API is closed to new keys. Respect the terms of service and
copyright; when no key is available, the pipeline works with local video files.

## 📜 License

MIT — see [LICENSE](LICENSE).

---

## 📫 Contact

Open to freelance work and collaborations.

- Email: **bottiktik123@gmail.com**
- GitHub: [@bottk123-ctrl](https://github.com/bottk123-ctrl)
- Portfolio: [github.com/bottk123-ctrl/portfolio](https://github.com/bottk123-ctrl/portfolio)
