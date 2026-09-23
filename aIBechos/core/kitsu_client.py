"""
Kitsu API client -- identifica manga/cómic escaneado para aIBechos,
alternativa a ComicVine/MangaDex/AniList. Sigue la especificación JSON:API
(cabecera Accept obligatoria), no necesita ninguna API Key para búsquedas de
solo lectura.
"""

import threading
import time
from collections import deque

import requests

from core.api_client import MediaInfo

KITSU_BASE = "https://kitsu.io/api/edge"


class KitsuUnavailableError(Exception):
    """5xx del propio servidor de Kitsu -- mismo criterio que
    MangaDexUnavailableError/AniListUnavailableError."""
    pass


class KitsuClient:
    # Ventana CORTA -- mismo criterio que el resto de clientes nuevos esta
    # sesión (ver ComicVineClient, corregido tras un cuelgue real).
    _MAX_REQUESTS_PER_WINDOW = 30
    _WINDOW_SECONDS = 30.0
    _MAX_5XX_RETRIES = 2

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/vnd.api+json"
        self._request_times: deque = deque()
        self._rate_lock = threading.Lock()

    def _throttle(self):
        while True:
            with self._rate_lock:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] > self._WINDOW_SECONDS:
                    self._request_times.popleft()
                if len(self._request_times) < self._MAX_REQUESTS_PER_WINDOW:
                    self._request_times.append(now)
                    return
                wait = self._WINDOW_SECONDS - (now - self._request_times[0]) + 0.05
            time.sleep(wait)

    def _get(self, endpoint: str, **params) -> dict:
        for attempt in range(self._MAX_5XX_RETRIES + 1):
            self._throttle()
            try:
                r = self.session.get(f"{KITSU_BASE}{endpoint}", params=params, timeout=10)
                if r.status_code >= 500 and attempt < self._MAX_5XX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.ConnectionError:
                raise ConnectionError("Sin conexión a internet.")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code >= 500:
                    raise KitsuUnavailableError(
                        "El servicio de Kitsu no está disponible ahora mismo "
                        "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
                raise

    def search_volumes(self, query: str) -> list:
        data = self._get("/manga", **{"filter[text]": query, "page[limit]": 20})
        return data.get("data", []) or []

    def build_manga_info(self, result: dict, episode: int = None) -> MediaInfo:
        attrs = result.get("attributes", {}) or {}
        title = attrs.get("canonicalTitle") or (attrs.get("titles") or {}).get("en") or ""
        year = (attrs.get("startDate") or "")[:4]
        overview = attrs.get("synopsis") or attrs.get("description") or ""
        poster = attrs.get("posterImage") or {}
        poster_url = poster.get("small") or poster.get("original")
        return MediaInfo(
            tmdb_id=result.get("id", ""),
            media_type="libro",
            title=title,
            original_title=title,
            year=year,
            poster_url=poster_url,
            season=None,
            episode=episode,
            episode_title=None,
            overview=overview,
            genres=[],
            genre_ids=["comic"],
        )
