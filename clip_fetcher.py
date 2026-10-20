"""Clip.Cafe API: поиск и легальное скачивание клипов из фильмов.

Документация: https://clip.cafe/api-docs/
  База:      https://api.clip.cafe/
  Поиск:     GET /?api_key=...&<query params>  -> JSON (Elasticsearch-like, hits.hits[])
  Скачать:   GET /?api_key=...&slug=...&key=... -> бинарный поток
  Download-key генерируется при поиске и живёт 5 минут — качаем сразу после search.
"""

import os
import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import requests

import config
from cancellation import CancelledError, check
from applog import log


class ClipCafeError(RuntimeError):
    """Базовая ошибка Clip.Cafe."""


class ClipCafeAuthError(ClipCafeError):
    """401/403 — проблема с ключом/доступом."""


class ClipCafeRateLimitError(ClipCafeError):
    """429 — исчерпан лимит запросов."""


# ---------------------------------------------------------------------------
# HTTP-ХЕЛПЕРЫ
# ---------------------------------------------------------------------------
def _handle_http_error(exc: requests.HTTPError, context: str) -> ClipCafeError:
    """Превращает HTTP-ошибку в понятное доменное исключение."""
    status = exc.response.status_code if exc.response is not None else None
    if status in (401, 403):
        return ClipCafeAuthError(
            f"{context}: неверный или отозванный API-ключ (HTTP {status}). "
            "Проверь CLIP_CAFE_API_KEY."
        )
    if status == 429:
        retry_after = None
        if exc.response is not None:
            retry_after = exc.response.headers.get("Retry-After")
        hint = f" Повтори через ~{retry_after} сек." if retry_after else ""
        return ClipCafeRateLimitError(
            f"{context}: лимит запросов исчерпан (HTTP 429).{hint} "
            "Квоты фиксированы тарифом ключа."
        )
    return ClipCafeError(f"{context}: HTTP {status} — {exc}")


def _request(method: str, url: str, context: str, **kwargs) -> requests.Response:
    """Обёртка над requests с обработкой таймаутов и HTTP-ошибок."""
    try:
        response = requests.request(method, url, timeout=config.HTTP_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response
    except requests.HTTPError as exc:
        raise _handle_http_error(exc, context) from exc
    except requests.Timeout as exc:
        raise ClipCafeError(f"{context}: таймаут {config.HTTP_TIMEOUT}s — {exc}") from exc
    except requests.RequestException as exc:
        raise ClipCafeError(f"{context}: сетевая ошибка — {exc}") from exc


# ---------------------------------------------------------------------------
# ПОИСК
# ---------------------------------------------------------------------------
def _extract_hits(payload: dict) -> List[dict]:
    """Достаёт массив клипов из ответа Elasticsearch-структуры."""
    try:
        hits = payload["hits"]["hits"]
    except (KeyError, TypeError):
        return []
    clips: List[dict] = []
    for hit in hits:
        source = dict(hit.get("_source", {}))
        source.setdefault("_id", hit.get("_id"))
        clips.append(source)
    return clips


def search_clips(query_params: dict, api_key: str) -> List[dict]:
    """GET-поиск клипов. Возвращает список метаданных (dict).

    query_params может содержать title, movie_title, transcript, actors,
    clipID, duration, size, from и т.д. api_key добавляется автоматически.
    """
    if not api_key:
        raise ClipCafeAuthError("Не задан CLIP_CAFE_API_KEY.")

    params = dict(query_params or {})
    params["api_key"] = api_key
    params.setdefault("size", config.CLIP_SEARCH_LIMIT)

    response = _request("GET", config.CLIP_CAFE_BASE_URL, "Поиск клипов", params=params)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ClipCafeError("Поиск клипов: ответ не является JSON.") from exc

    clips = _extract_hits(payload)
    log(f"Clip.Cafe: найдено клипов — {len(clips)}.")
    return clips


# ---------------------------------------------------------------------------
# СКАЧИВАНИЕ
# ---------------------------------------------------------------------------
def _extract_slug_and_key(meta: dict):
    """Извлекает slug и download-key из метаданных (имена полей различаются).

    Поддерживаем прямую ссылку на скачивание — из неё вытаскиваем slug/key.
    """
    slug = meta.get("slug") or meta.get("clipSlug") or meta.get("clip_slug")
    key = meta.get("key") or meta.get("downloadKey") or meta.get("download_key")

    url = meta.get("download") or meta.get("downloadUrl") or meta.get("download_url")
    if isinstance(url, str) and url.startswith("http") and (not slug or not key):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        slug = slug or (qs.get("slug", [None])[0])
        key = key or (qs.get("key", [None])[0])
        if not slug:
            slug = parsed.path.rstrip("/").split("/")[-1] or None

    return slug, key


def _safe_filename(meta: dict) -> str:
    """Собирает безопасное имя файла из clipID/slug."""
    clip_id = meta.get("clipID") or meta.get("_id") or "clip"
    slug = str(meta.get("slug") or meta.get("title") or "clip")
    slug = re.sub(r"[^\w\-]+", "_", slug).strip("_")[:60] or "clip"
    return f"{clip_id}_{slug}.mp4"


def download_clip(clip_metadata: dict, download_dir: str, api_key: str) -> str:
    """Качает клип в download_dir и возвращает путь к .mp4.

    Важно: download-key живёт 5 минут, поэтому качать нужно сразу после поиска.
    """
    if not api_key:
        raise ClipCafeAuthError("Не задан CLIP_CAFE_API_KEY.")

    clip_id = clip_metadata.get("clipID") or clip_metadata.get("_id")
    slug, key = _extract_slug_and_key(clip_metadata)
    if not slug or not key:
        raise ClipCafeError(
            f"В метаданных клипа {clip_id} нет slug/download-key — "
            "скачать нельзя (возможно, ключ истёк, повтори поиск)."
        )

    os.makedirs(download_dir, exist_ok=True)
    filepath = os.path.join(download_dir, _safe_filename(clip_metadata))

    params = {"api_key": api_key, "slug": slug, "key": key}
    # stream=True — качаем потоком, не держим видео целиком в памяти.
    response = _request("GET", config.CLIP_CAFE_BASE_URL,
                        f"Скачивание клипа {clip_id}", params=params, stream=True)

    with open(filepath, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            check()  # отмена во время скачивания большого файла
            if chunk:
                fh.write(chunk)

    if os.path.getsize(filepath) == 0:
        os.remove(filepath)
        raise ClipCafeError(f"Скачанный файл пуст: {clip_id}")

    log(f"  скачан клип {clip_id} -> {filepath}")
    return filepath


def fetch_clips_for_pipeline(query_params: Optional[dict] = None,
                             api_key: Optional[str] = None,
                             download_dir: Optional[str] = None,
                             limit: Optional[int] = None) -> List[str]:
    """Поиск + скачивание топ-N клипов. Возвращает список путей.

    Любая ошибка -> пустой список (оркестратор уйдёт в фолбэк на локальную папку).
    """
    api_key = api_key if api_key is not None else config.CLIP_CAFE_API_KEY
    download_dir = download_dir or config.CLIP_DOWNLOAD_DIR
    limit = limit or config.CLIP_SEARCH_LIMIT
    query_params = query_params or _default_query()

    if not api_key:
        log("Clip.Cafe: API-ключ не задан — пропускаю скачивание.")
        return []

    try:
        clips = search_clips(query_params, api_key)
    except ClipCafeError as exc:
        log(f"Clip.Cafe недоступен: {exc}")
        return []

    paths: List[str] = []
    for meta in clips[:limit]:
        check()  # отмена между загрузками
        try:
            paths.append(download_clip(meta, download_dir, api_key))
        except ClipCafeError as exc:
            log(f"  пропуск клипа: {exc}")

    log(f"Clip.Cafe: скачано {len(paths)}/{min(len(clips), limit)} файлов.")
    return paths


def fetch_yt_clips(video_url: str = "", search_query: str = "",
                   output_dir: Optional[str] = None,
                   clip_length: Optional[int] = None) -> List[str]:
    """yt-dlp: скачать видео (ссылка или поиск) и нарезать на клипы.

    Приоритет: прямая ссылка -> поиск. Любая ошибка -> [] (фолбэк в оркестраторе).
    """
    output_dir = output_dir or config.CLIP_DOWNLOAD_DIR
    clip_length = clip_length or config.YT_CLIP_LENGTH
    try:
        from yt_fetcher import (download_by_search, download_from_url,
                                split_video_into_clips)
    except Exception as exc:  # noqa: BLE001 — нет yt-dlp/ffmpeg
        log(f"yt-dlp недоступен: {exc}")
        return []

    try:
        if video_url:
            video_path = download_from_url(video_url, config.YT_RAW_DIR)
        elif search_query and config.YT_SEARCH_FALLBACK:
            video_path = download_by_search(search_query, config.YT_RAW_DIR)
        else:
            return []
        clips = split_video_into_clips(video_path, output_dir, clip_length)
        log(f"yt-dlp: подготовлено клипов — {len(clips)}.")
        return clips
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — не роняем пайплайн
        log(f"Ошибка yt-dlp: {exc}")
        return []


def _default_query() -> dict:
    """Собирает параметры поиска из config (movie title / transcript)."""
    query: dict = {}
    if config.CLIP_SEARCH_MOVIE_TITLE:
        query["movie_title"] = config.CLIP_SEARCH_MOVIE_TITLE
    if config.CLIP_SEARCH_TRANSCRIPT:
        query["transcript"] = config.CLIP_SEARCH_TRANSCRIPT
    return query