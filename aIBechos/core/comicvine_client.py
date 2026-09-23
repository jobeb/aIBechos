"""
ComicVine API client -- identifica cómics/manga escaneados (cbz, cbr) para
aIBechos. A diferencia de Google Books (core/book_client.py), ComicVine
SIEMPRE exige: una api_key válida (401 sin ella), format=json explícito
(su formato por defecto es XML) y un User-Agent que no sea el genérico de
requests (lo rechaza). Cuota mucho más ajustada que TMDB -- se limita de
forma conservadora en vez de asumir los márgenes de TMDBClient.
"""

import re
import threading
import time
from collections import deque

import requests

from core.api_client import MediaInfo
from core.applog import get_logger

COMICVINE_BASE = "https://comicvine.gamespot.com/api"

_log = get_logger("aIBechos.comicvine", "app.log")


class ComicVineClient:
    # Ventana de autolimitado -- antes 150 peticiones / 3600s (1 hora): un
    # margen "conservador" pero nunca medido contra el servicio real (ver
    # historial). Bug real: una identificación en bloque de una colección
    # de ~25 cómics (más reintentos/AI de traducción) agotaba esas 150 en
    # minutos, y CUALQUIER búsqueda posterior se quedaba bloqueada en
    # _throttle() -- sin sleep, sin log, sin nada visible -- hasta que se
    # liberase hueco en la ventana de una hora entera. Desde fuera (botón
    # "Buscar" de la GUI) eso se veía como "Buscando..." colgado
    # indefinidamente, sin ningún error ni forma de saber por qué ni
    # cuánto quedaba. 60 peticiones / 60s (1 por segundo de media) es la
    # guía pública real de ComicVine -- la espera máxima ahora es de
    # ~60s en vez de hasta ~3600s, y se deja constancia en el log (ver
    # más abajo) para que una futura espera larga sea diagnosticable sin
    # tener que adivinarlo.
    _MAX_REQUESTS_PER_WINDOW = 60
    _WINDOW_SECONDS = 60.0

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.session = requests.Session()
        # ComicVine bloquea el User-Agent por defecto de requests
        # ("python-requests/x.x") -- exige uno que identifique la app.
        self.session.headers["User-Agent"] = "aIBechos/1.0 (+https://github.com/)"
        self._request_times: deque = deque()
        self._rate_lock = threading.Lock()

    def set_api_key(self, key: str):
        self.api_key = key

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
            _log.warning("ComicVine: limite propio de %d peticiones/%ds alcanzado, "
                         "esperando %.1fs antes de la siguiente busqueda",
                         self._MAX_REQUESTS_PER_WINDOW, self._WINDOW_SECONDS, wait)
            time.sleep(wait)

    def _get(self, endpoint: str, **params) -> dict:
        params["api_key"] = self.api_key
        params["format"] = "json"
        self._throttle()
        try:
            r = self.session.get(f"{COMICVINE_BASE}{endpoint}", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Sin conexión a internet.")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise ValueError("API Key de ComicVine inválida.")
            raise
        if data.get("status_code") not in (1, None):
            # ComicVine devuelve 200 con su propio código de error dentro
            # del cuerpo (p.ej. 100 = "Invalid API Key") en vez de un HTTP
            # 4xx -- sin esto, una key inválida parecería una búsqueda sin
            # resultados en vez de un error real.
            raise ValueError(data.get("error", "Error de ComicVine"))
        return data

    def search_volumes(self, query: str) -> list:
        data = self._get("/search/", query=query, resources="volume")
        return data.get("results", []) or []

    def get_volume(self, volume_id) -> dict:
        return self._get(f"/volume/4050-{volume_id}/").get("results", {})

    def validate_key(self) -> bool:
        """ComicVine no tiene un endpoint dedicado a "solo comprobar la
        key" (a diferencia de TMDB con /configuration) -- una búsqueda
        mínima sirve igual, ya que _get() ya distingue una key inválida
        (status_code 100, ver más arriba) de cualquier otro error."""
        try:
            self._get("/search/", query="test", resources="volume")
            return True
        except Exception:
            return False

    def build_comic_info(self, result: dict, episode: int = None) -> MediaInfo:
        """episode: número de emisión de ESTE archivo concreto, extraído
        localmente del nombre de archivo (ver detect_episode(is_comic=True)
        en core/api_client.py) -- ComicVine identifica la COLECCIÓN
        ("volume"), no sabe qué número es el archivo que se está subiendo,
        igual que TMDBClient.build_media_info recibe season/episode desde
        fuera en vez de adivinarlos."""
        title = (result.get("volume") or {}).get("name") or result.get("name", "")
        poster_url = (result.get("image") or {}).get("small_url")
        overview = _strip_html(result.get("description", "") or "")
        return MediaInfo(
            tmdb_id=result.get("id", ""),
            media_type="libro",
            title=title,
            original_title=title,
            year=str(result.get("start_year", "") or ""),
            poster_url=poster_url,
            season=None,
            episode=episode,
            episode_title=None,
            overview=overview,
            genres=[],
            genre_ids=["comic"],
        )


def _strip_html(text: str) -> str:
    """Las descripciones de ComicVine vienen en HTML crudo (<p>, <a>...) --
    limpieza simple para mostrarlas como texto plano en el panel de
    detalles, no un parser HTML completo."""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()
