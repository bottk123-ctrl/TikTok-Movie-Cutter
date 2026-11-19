@echo off
setlocal

REM ==========================================================================
REM  Сборка GUI в dist\TikTokMovieCutter\ (Python + FFmpeg, без DaVinci).
REM  Вся конфигурация сборки — в TikTokMovieCutter.spec (единый источник
REM  правды, его же использует CI). Здесь только запуск.
REM ==========================================================================

pyinstaller --noconfirm TikTokMovieCutter.spec

if errorlevel 1 (
  echo.
  echo Сборка упала.
  endlocal
  exit /b 1
)

echo.
echo Готово: dist\TikTokMovieCutter\TikTokMovieCutter.exe
echo Нужен ffmpeg/ffprobe: положи ffmpeg.exe и ffprobe.exe рядом с .exe, в bin\ или в PATH.
endlocal
pause
