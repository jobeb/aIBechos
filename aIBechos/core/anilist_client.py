"""
AniList API client -- identifica manga/cómic escaneado para aIBechos,
alternativa a ComicVine/MangaDex. AniList usa GraphQL en vez de REST (un
único endpoint POST), no necesita ninguna API Key para consultas de solo
lectura, y trae sinónimos multi-idioma por serie -- útil cuando el título
detectado localmente (a menudo en castellano) no coincide con el romaji/
inglés/nativo que usa como títulos principales.
"""

import re
import threading
import time
from collections import deque

import requests

from core.api_client import MediaInfo

ANILIST_BASE = "https://graphql.anilist.co"

_SEARCH_QUERY = """
query ($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: MANGA) {
      id
      title { romaji english native }
      synonyms
      description(asHtml: false)
      coverImage { large }
      startDate { year }
      format
    }
  }
}
"""


class AniListUnavailableError(Exception):
    """5xx del propio servidor de AniList -- mismo criterio que
    MangaDexUnavailableError/OpenLibraryUnavailableError."""
    pass


class AniListRateLimitError(Exception):
    """429 -- AniList ha tenido temporadas de límite degradado (~30
    peticiones/minuto) anunciadas fuera de banda (Discord/Twitter), distinta
    del autolimitado propio de este cliente (_throttle, que solo evita que
    NOSOTROS lo saturemos, no un 429 real del servidor)."""
    pass


class AniListClient:
    # Ventana CORTA (mismo criterio que ComicVineClient/MangaDexClient esta
    # sesión) -- conservadora porque AniList ha degradado su límite público a
    # ~30/min en el pasado; ajustable si en la práctica hay margen de sobra.
    _MAX_REQUESTS_PER_WINDOW = 30
    _WINDOW_SECONDS = 60.0

    def __init__(self):
        self.session = requests.Session()
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

    def _post(self, query: str, variables: dict) -> dict:
        self._throttle()
        try:
            r = self.session.post(ANILIST_BASE, json={"query": query, "variables": variables}, timeout=10)
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Sin conexión a internet.")
        if r.status_code == 429:
            raise AniListRateLimitError(
                "Límite de peticiones de AniList alcanzado -- espera un minuto e inténtalo de nuevo.")
        if r.status_code >= 500:
            raise AniListUnavailableError(
                "El servicio de AniList no está disponible ahora mismo "
                "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise ValueError(data["errors"][0].get("message", "Error de AniList"))
        return data.get("data", {}) or {}

    def search_volumes(self, query: str) -> list:
        data = self._post(_SEARCH_QUERY, {"search": query, "perPage": 20})
        return (data.get("Page", {}) or {}).get("media", []) or []

    def build_manga_info(self, result: dict, episode: int = None) -> MediaInfo:
        titles = result.get("title", {}) or {}
        title = titles.get("english") or titles.get("romaji") or titles.get("native") or ""
        year = str((result.get("startDate") or {}).get("year", "") or "")
        overview = result.get("description") or ""
        overview = re.sub(r"<[^>]+>", " ", overview)
        overview = re.sub(r"\s{2,}", " ", overview).strip()
        poster_url = (result.get("coverImage") or {}).get("large")
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
