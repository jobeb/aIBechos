"""
Google Books API client -- identifica ebooks de texto (pdf, epub, mobi,
azw3) para aIBechos, igual que TMDBClient identifica series/películas.

No hace falta API key para búsquedas básicas -- PERO, a diferencia de lo
que se pensó al principio, la cuota anónima NO es "de sobra": Google la
comparte globalmente entre cualquiera que llame sin key (no es un cupo por
IP/equipo), así que puede devolver 429 incluso con una única búsqueda si
otros usuarios en cualquier parte la están agotando en ese momento -- visto
de verdad: 429 al primer intento del día, sin haber hecho ninguna otra
búsqueda antes. El autolimitado de abajo (_throttle, mismo criterio que
ComicVineClient._MAX_REQUESTS_PER_WINDOW) ayuda contra saturarla nosotros
mismos en un "Buscar todos" con muchos archivos seguidos, pero NO puede
evitar un 429 causado por el resto de usuarios anónimos del mundo -- la
única forma real de librarse de eso es una key propia (gratis en
console.cloud.google.com, activando "Books API"), que da cuota per-proyecto
en vez de la anónima compartida. Por eso sí existe google_books_api_key en
Ajustes (a diferencia de la primera versión de este módulo, que no tenía
campo para ella asumiendo que nunca haría falta).
"""

import threading
import time
from collections import deque

import requests

from core.api_client import MediaInfo

GOOGLE_BOOKS_BASE = "https://www.googleapis.com/books/v1"


class GoogleBooksRateLimitError(Exception):
    """429 -- distinta de ValueError (key inválida) para que validate_key()
    no confunda "cuota agotada ahora mismo" con "la key está mal"."""
    pass


class GoogleBooksUnavailableError(Exception):
    """5xx -- fallo del propio servidor de Google Books (visto de verdad:
    503 "backendFailed"/"Service temporarily unavailable" incluso con una
    API Key propia y válida, nada que ver con cuota ni con la key). Distinta
    de ValueError por el mismo motivo que GoogleBooksRateLimitError."""
    pass


class GoogleBooksClient:
    _MAX_REQUESTS_PER_WINDOW = 90
    _WINDOW_SECONDS = 100.0
    # Reintentos cortos ante un 5xx -- visto de verdad: el propio backend de
    # Google Books falla de forma INTERMITENTE (503 "backendFailed") incluso
    # con una API Key propia y válida, con una tasa de fallo real de
    # aproximadamente 1 de cada 2 peticiones en rachas -- no es un pico
    # puntual que se resuelva solo "en unas horas". Sin reintento, cada
    # búsqueda tenía una probabilidad alta de fallar aunque el servicio en
    # conjunto SÍ estuviera respondiendo la mayoría de las veces. No se
    # reintenta un 429 (límite de cuota) -- esperar 1-3s no libera cuota
    # diaria agotada, así que reintentarlo no ayudaría y solo alargaría la
    # espera antes de mostrar el error real.
    _MAX_5XX_RETRIES = 2

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.session = requests.Session()
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
            time.sleep(wait)

    def _get(self, endpoint: str, **params) -> dict:
        if self.api_key:
            params["key"] = self.api_key
        for attempt in range(self._MAX_5XX_RETRIES + 1):
            self._throttle()
            try:
                r = self.session.get(f"{GOOGLE_BOOKS_BASE}{endpoint}", params=params, timeout=10)
                if r.status_code >= 500 and attempt < self._MAX_5XX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.ConnectionError:
                raise ConnectionError("Sin conexión a internet.")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 401:
                    raise ValueError("API Key de Google Books inválida.")
                if e.response.status_code == 400 and self.api_key:
                    # Google Books devuelve 400 (no 401) cuando la key en sí
                    # es inválida -- solo se trata como key mala si HAY una
                    # key puesta, para no confundir un 400 anónimo (query
                    # rara) con una key incorrecta.
                    raise ValueError("API Key de Google Books inválida.")
                if e.response.status_code == 429:
                    # El autolimitado de arriba solo evita que NOSOTROS
                    # saturemos la cuota anónima -- no puede evitar un 429
                    # causado por el resto de usuarios anónimos del mundo (ver
                    # docstring del módulo). Un mensaje claro en vez del texto
                    # crudo de requests, y una key propia en Ajustes es la
                    # solución real si esto pasa a menudo.
                    raise GoogleBooksRateLimitError(
                        "Límite de peticiones de Google Books alcanzado (cuota anónima "
                        "compartida) -- espera unos minutos, o configura tu propia API "
                        "Key gratuita en Ajustes para tener cuota propia.")
                if e.response.status_code >= 500:
                    # Ya se reintentó _MAX_5XX_RETRIES veces sin éxito -- fallo
                    # persistente del propio servidor de Google, no de la
                    # cuota ni de la key. No hay nada que el usuario pueda
                    # configurar para evitarlo, solo esperar.
                    raise GoogleBooksUnavailableError(
                        "El servicio de Google Books no está disponible ahora mismo "
                        "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
                raise

    def search_volumes(self, query: str) -> list:
        data = self._get("/volumes", q=query)
        return data.get("items", []) or []

    def get_volume(self, volume_id: str) -> dict:
        return self._get(f"/volumes/{volume_id}")

    def validate_key(self) -> bool:
        """Mismo criterio que ComicVineClient.validate_key -- no hay un
        endpoint dedicado a "solo comprobar la key", una búsqueda mínima
        sirve igual, ya que _get() distingue una key inválida (ver arriba)
        de cualquier otro error."""
        try:
            self._get("/volumes", q="test")
            return True
        except ValueError:
            return False
        except Exception:
            return True   # error de red/cuota, no de la key en sí -- no reportar "inválida"

    def build_book_info(self, result: dict) -> MediaInfo:
        info = result.get("volumeInfo", {}) or {}
        title = info.get("title", "")
        poster_url = (info.get("imageLinks", {}) or {}).get("thumbnail")
        if poster_url and poster_url.startswith("http://"):
            # Google Books devuelve las miniaturas en http:// -- subirlas a
            # https:// evita contenido mixto al cargarlas desde la app.
            poster_url = "https://" + poster_url[len("http://"):]
        authors = info.get("authors") or []
        description = info.get("description", "") or ""
        overview = (", ".join(authors) + "\n\n" + description).strip() if authors else description
        return MediaInfo(
            tmdb_id=result.get("id", ""),
            media_type="libro",
            title=title,
            original_title=title,
            year=(info.get("publishedDate", "") or "")[:4],
            poster_url=poster_url,
            season=None,
            episode=None,
            episode_title=None,
            overview=overview,
            genres=[],
            genre_ids=["ebook"],
        )
