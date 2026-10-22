# tts_generator.py (новая версия с Qwen3-TTS)
"""Генерация озвучки через Qwen3-TTS (локально, на GPU) с последующей конвертацией в MP3.
Использует faster-qwen3-tts для ускорения на RTX 50-й серии.
Модель загружается лениво, один раз при первом вызове.
"""

import asyncio
import os
import re
import time
from typing import List, Optional

import config
from applog import log

# --- НОВЫЕ ИМПОРТЫ ---
import torch
import soundfile as sf
from faster_qwen3_tts import FasterQwen3TTS

os.environ["HF_HOME"] = r"F:\huggingface_cache"

# --- ГОЛОСА QWEN3-TTS (CustomVoice) ---
# Это предустановленные голоса. Для русского они могут звучать с легким акцентом.
# Для максимального качества лучше использовать режим клонирования (см. ниже).
VOICES: List[str] = [
    "serena",  # женский
    "aiden",   # мужской
    "ryan",    # мужской
    "vivian",  # женский
]

# Совместимость со старыми именами из GUI/настроек.
_ALIASES = {
    "aidar": "aiden",
    "eugene": "aiden",
    "baya": "serena",
    "xenia": "serena",
    "kseniya": "vivian",
    "dmitry": "aiden",
    "svetlana": "serena",
    "dariya": "vivian",
}

_VALID_MOD = re.compile(r"^[+-]\d{1,3}%$")
_MAX_CHUNK_CHARS = 2500  # длинный текст режем по предложениям

# Глобальная переменная для ленивой загрузки модели
_model = None

def _patch_qwen_rope_compat() -> None:
    """Лечит несовместимость qwen-tts-hf с transformers 5.x.

    qwen_tts при импорте регистрирует в ROPE_INIT_FUNCTIONS глобальный обработчик
    "default", который читает config.rope_theta. В transformers 5.x этот обработчик
    перекрывает штатный module.compute_default_rope_parameters для всех моделей, а у
    MimiConfig (аудиокодек) атрибута rope_theta больше нет — значение переехало в
    config.rope_parameters["rope_theta"]. В итоге загрузка падает с
    "'MimiConfig' object has no attribute 'rope_theta'".
    Заменяем обработчик на совместимый с обоими форматами.
    """
    try:
        import qwen_tts  # noqa: F401  (регистрирует свой обработчик "default")
        from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

        def _default_rope_parameters(config, device=None, seq_len=None, layer_type=None):
            del seq_len, layer_type
            base = getattr(config, "rope_theta", None)
            if base is None:
                rope_parameters = getattr(config, "rope_parameters", None)
                if isinstance(rope_parameters, dict):
                    base = rope_parameters.get("rope_theta")
                elif rope_parameters is not None:
                    base = getattr(rope_parameters, "rope_theta", None)
            if base is None:
                base = 10000.0
            partial_rotary_factor = getattr(config, "partial_rotary_factor", 1.0) or 1.0
            head_dim = getattr(config, "head_dim", None) or config.hidden_size // config.num_attention_heads
            dim = int(head_dim * partial_rotary_factor)
            inv_freq = 1.0 / (
                base
                ** (
                    torch.arange(0, dim, 2, dtype=torch.int64).to(device=device, dtype=torch.float)
                    / dim
                )
            )
            return inv_freq, 1.0

        ROPE_INIT_FUNCTIONS["default"] = _default_rope_parameters
        log("Применён патч совместимости qwen-tts и transformers 5.x.")
    except Exception as exc:
        log(f"Не удалось применить патч совместимости qwen-tts: {exc}")

def _get_model() -> FasterQwen3TTS:
    """Ленивая загрузка модели Qwen3-TTS (1.7B CustomVoice для предустановленных голосов)."""
    global _model
    if _model is None:
        _patch_qwen_rope_compat()
        log("Загрузка модели Qwen3-TTS (1.7B) на GPU... Это может занять некоторое время.")
        # Для предустановленных голосов (generate_custom_voice) нужна именно
        # CustomVoice-модель: у Base-модели tts_model_type="base" и нет голосов.
        _model = FasterQwen3TTS.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
            device="cuda",
        )
        log("Модель Qwen3-TTS успешно загружена.")
    return _model

def list_voices() -> List[str]:
    """Доступные голоса."""
    return list(VOICES)

def resolve_voice(speaker: str) -> str:
    """Приводит имя голоса к формату Qwen3-TTS (с учётом старых алиасов)."""
    if not speaker:
        return VOICES[0]
    if speaker in VOICES:
        return speaker
    if speaker in _ALIASES:
        return _ALIASES[speaker]
    log(f"Неизвестный голос '{speaker}', использую {VOICES[0]}.")
    return VOICES[0]

# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНОЕ (оставлено без изменений)
# ---------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    # ... (код без изменений) ...
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    if not re.search(r"[^\W\d_]", text, flags=re.UNICODE):
        return ""
    return text.strip()

def _valid_mod(value: str) -> Optional[str]:
    # ... (код без изменений) ...
    return value if value and _VALID_MOD.match(value) else None

def _split_chunks(text: str, limit: int = _MAX_CHUNK_CHARS) -> List[str]:
    # ... (код без изменений) ...
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
            while len(part) > limit:
                chunks.append(part[:limit])
                part = part[limit:]
            current = part
    if current:
        chunks.append(current)
    return chunks

# ---------------------------------------------------------------------------
# СИНТЕЗ (НОВАЯ ЛОГИКА)
# ---------------------------------------------------------------------------
def _synthesize_qwen(text: str, voice: str, output_wav_path: str) -> None:
    """Синтезирует текст с помощью Qwen3-TTS и сохраняет в WAV."""
    model = _get_model()
    
    # Используем предустановленный голос (CustomVoice).
    # Для клонирования голоса нужно будет передать ref_audio и ref_text.
    # Пример с клонированием (если решите использовать):
    # audio_list, sr = model.generate_voice_clone(
    #     text=text, language="Russian", 
    #     ref_audio="path/to/ref.wav", ref_text="Текст образца"
    # )
    
    audio_list, sr = model.generate_custom_voice(
        text=text,
        language="Russian",
        speaker=voice,
    )
    
    # Qwen3-TTS возвращает список numpy-массивов. Берем первый.
    sf.write(output_wav_path, audio_list[0], sr)

def _try_once(text: str, voice: str, output_wav_path: str) -> None:
    """Одна попытка синтеза с Qwen3-TTS."""
    if os.path.exists(output_wav_path):
        try:
            os.remove(output_wav_path)
        except OSError:
            pass
    
    _synthesize_qwen(text, voice, output_wav_path)
    
    if not os.path.isfile(output_wav_path) or os.path.getsize(output_wav_path) == 0:
        raise RuntimeError("Qwen3-TTS не создал аудиофайл.")

def _synthesize_with_retry(text: str, voice: str, output_wav_path: str, retries: int = 3) -> None:
    """Повторяет запрос с паузами (на случай временных сбоев GPU/памяти)."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            _try_once(text, voice, output_wav_path)
            return
        except Exception as exc:
            last_exc = exc
            log(f"Qwen3-TTS: попытка {attempt}/{retries} не удалась: {exc}")
            if attempt < retries:
                delay = 2.0 * attempt
                log(f" пауза {delay:.0f}с перед повтором...")
                time.sleep(delay)
    raise RuntimeError(f"Qwen3-TTS не смог сгенерировать озвучку: {last_exc}")

def _concat_wavs_and_convert_to_mp3(parts: List[str], output_mp3_path: str) -> None:
    """Склеивает WAV-части в один файл и конвертирует в MP3 через ffmpeg."""
    import subprocess
    from audio_utils import find_ffmpeg  # используем ваш существующий поиск ffmpeg
    
    ffmpeg = find_ffmpeg()
    # Создаем временный файл со списком частей для ffmpeg
    list_file = output_mp3_path + ".list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for part in parts:
            # Экранируем одинарные кавычки для формата concat
            f.write(f"file '{part.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n")
    
    try:
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "libmp3lame", "-q:a", "2", output_mp3_path],
            check=True,
            capture_output=True
        )
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)
        for part in parts:
            try:
                os.remove(part)
            except OSError:
                pass

def generate_voice(text: str, speaker: str = "serena", output_path: str = "", log_callback=None, rate: str = "", volume: str = "") -> str:
    """Генерирует озвучку текста голосом Qwen3-TTS и сохраняет в MP3."""
    text = _clean_text(text)
    if not text:
        raise ValueError("Пустой текст для озвучки (или только пробелы/знаки).")
    output_path = output_path or config.AUDIO_PATH
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    voice = resolve_voice(speaker)
    # rate и volume пока игнорируются, так как Qwen3-TTS не поддерживает их напрямую.
    # При необходимости можно реализовать пост-обработку через ffmpeg.
    
    log(f"Генерация Qwen3-TTS (голос {voice}, {len(text)} симв.)...")
    chunks = _split_chunks(text)
    
    # Создаем временные WAV-файлы для каждой части
    if len(chunks) == 1:
        # Если одна часть, можно сразу генерировать в целевой WAV, а потом конвертировать
        temp_wav = output_path.replace(".mp3", ".wav")
        targets = [temp_wav]
    else:
        targets = [output_path + f".part{i}.wav" for i in range(len(chunks))]
    
    try:
        for idx, (chunk, target) in enumerate(zip(chunks, targets), start=1):
            if len(chunks) > 1:
                log(f" часть {idx}/{len(chunks)} ({len(chunk)} симв.)")
            # Небольшая пауза, чтобы GPU "остыл" между запросами (опционально)
            if idx > 1:
                time.sleep(0.5)
            _synthesize_with_retry(chunk, voice, target)
        
        # Конвертируем WAV в MP3
        if len(chunks) == 1:
            # Одна часть: просто конвертируем WAV в MP3
            import subprocess
            from audio_utils import find_ffmpeg
            ffmpeg = find_ffmpeg()
            subprocess.run(
                [ffmpeg, "-y", "-i", targets[0], "-c:a", "libmp3lame", "-q:a", "2", output_path],
                check=True,
                capture_output=True
            )
            os.remove(targets[0])
        else:
            # Несколько частей: склеиваем WAV и конвертируем
            _concat_wavs_and_convert_to_mp3(targets, output_path)
            
    finally:
        # Убираем временные файлы
        for part in targets:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
    
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Qwen3-TTS не создал аудиофайл (проверь драйверы/CUDA).")
    
    log(f"Озвучка сохранена: {output_path}")
    return output_path