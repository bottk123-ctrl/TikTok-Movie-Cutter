@echo off
setlocal

REM ==========================================================================
REM  Сборка GUI в dist\TikTokMovieCutter\ (Python + FFmpeg, без DaVinci).
REM  TTS — edge-tts (без torch/silero) => сборка лёгкая и быстрая.
REM
REM  Почему НЕ --collect-all ctranslate2:
REM    его подмодуль converters импортирует torch, из-за чего в сборку
REM    затягивался torch (~360 МБ). Нам нужны только нативные библиотеки
REM    ctranslate2 (движок faster-whisper) => collect-binaries + collect-data.
REM  Явные --exclude-module не дают torch/torchaudio попасть в exe.
REM ==========================================================================

pyinstaller --noconfirm --onedir --windowed --name TikTokMovieCutter ^
  --collect-all customtkinter ^
  --collect-all edge_tts ^
  --collect-all faster_whisper ^
  --collect-binaries ctranslate2 ^
  --collect-data ctranslate2 ^
  --collect-all yt_dlp ^
  --hidden-import=requests ^
  --hidden-import=yt_dlp ^
  --hidden-import=edge_tts ^
  --hidden-import=ctranslate2 ^
  --exclude-module=torch ^
  --exclude-module=torchaudio ^
  --exclude-module=torchvision ^
  --exclude-module=silero_tts ^
  gui.py

echo.
echo Готово: dist\TikTokMovieCutter\TikTokMovieCutter.exe
echo Нужен ffmpeg/ffprobe: положи ffmpeg.exe и ffprobe.exe рядом с .exe, в bin\ или в PATH.
endlocal
pause
