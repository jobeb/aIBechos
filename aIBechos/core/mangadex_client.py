"""
MangaDex API client -- identifica manga/cómic escaneado (cbz, cbr) para
aIBechos, alternativa a ComicVine cuando su catálogo (mayoritariamente
occidental, en inglés) falla con manga. No necesita ninguna API Key para
buscar. A diferencia de ComicVine, MangaDex suele traer ya títulos
alternativos en varios idiomas (incluido español -- "es"/"es-la") en el
propio resultado, lo que en muchos casos evita tener que traducir el título
detectado localmente vía IA antes de buscar.
"""

import threading
import time
from collections import deque

import requests

from core.api_client import MediaInfo

MANGADEX_BASE = "https://api.mangadex.org"
MANGADEX_COVERS = "https://uploads.mangadex.org/covers"


class MangaDexUnavailableError(Exception):
    """5xx del propio servidor de MangaDex -- distinta de una excepción
    genérica para que quien llame pueda mostrar un mensaje claro en vez del
    texto crudo de requests (mismo criterio que OpenLibraryUnavailableError/
    GoogleBooksUnavailableError)."""
    pass


class MangaDexClient:
    # Ventana de autolimitado CORTA (mismo criterio ya corregido en
    # ComicVineClient esta misma sesión: una ventana larga hace que, una vez
    # agotada, cualquier búsqueda posterior se quede bloqueada en _throttle()
    # durante minutos/horas sin ningún aviso -- mejor una ventana corta,
    # aunque implique volver a esperar más veces).
    _MAX_REQUESTS_PER_WINDOW = 30
    _WINDOW_SECONDS = 30.0
    _MAX_5XX_RETRIES = 2

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

    def _get(self, endpoint: str, **params) -> dict:
        for attempt in range(self._MAX_5XX_RETRIES + 1):
            self._throttle()
            try:
                r = self.session.get(f"{MANGADEX_BASE}{endpoint}", params=params, timeout=10)
                if r.status_code >= 500 and attempt < self._MAX_5XX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.ConnectionError:
                raise ConnectionError("Sin conexión a internet.")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code >= 500:
                    raise MangaDexUnavailableError(
                        "El servicio de MangaDex no está disponible ahora mismo "
                        "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
                raise

    def search_volumes(self, query: str) -> list:
        # includes[]=cover_art: expande la relación "cover_art" con sus
        # atributos (fileName) directamente en la respuesta -- sin esto
        # haría falta una petición aparte por resultado solo para la
        # portada (ver build_manga_info).
        data = self._get("/manga", title=query, limit=20, **{"includes[]": "cover_art"})
        return data.get("data", []) or []

    @staticmethod
    def _first_localized(d: dict) -> str:
        """attributes.title/description/etc. de MangaDex son diccionarios
        {idioma: texto} sin un orden garantizado -- probar inglés primero
        (el más probable de estar presente y de servir para buscar
        después), si no el primer valor que haya, sea el idioma que sea."""
        if not d:
            return ""
        if "en" in d:
            return d["en"]
        return next(iter(d.values()), "")

    def build_manga_info(self, result: dict, episode: int = None) -> MediaInfo:
        attrs = result.get("attributes", {}) or {}
        title = self._first_localized(attrs.get("title", {}))
        if not title:
            # Sin título en "attributes.title" (raro, pero altTitles es la
            # lista de respaldo real que usa el propio MangaDex) -- probar
            # el primer alt-título disponible antes de rendirse.
            for alt in attrs.get("altTitles", []) or []:
                title = self._first_localized(alt)
                if title:
                    break
        year = str(attrs.get("year", "") or "")
        overview = self._first_localized(attrs.get("description", {}))
        poster_url = None
        for rel in result.get("relationships", []) or []:
            if rel.get("type") == "cover_art":
                file_name = (rel.get("attributes") or {}).get("fileName")
                if file_name:
                    poster_url = f"{MANGADEX_COVERS}/{result.get('id', '')}/{file_name}.256.jpg"
                break
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
