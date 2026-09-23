"""
OpenLibrary API client -- identifica ebooks de texto (pdf, epub, mobi, azw3)
para aIBechos, alternativa a Google Books (core/book_client.py). No
necesita ninguna API Key en ningún momento (a diferencia de ComicVine, y a
diferencia de Google Books cuando se agota su cuota anónima compartida) --
por eso es el proveedor PRINCIPAL para libros de texto desde que Google
Books empezó a fallar de forma intermitente y persistente (ver
core/book_identify.py, que prueba este cliente primero y solo cae a Google
Books si este falla o no encuentra nada).
"""

import threading
import time
from collections import deque

import requests

from core.api_client import MediaInfo

OPENLIBRARY_BASE = "https://openlibrary.org"
OPENLIBRARY_COVERS = "https://covers.openlibrary.org"


class OpenLibraryUnavailableError(Exception):
    """5xx persistente del propio servidor de OpenLibrary -- distinta de
    una excepción genérica para que quien llame pueda mostrar un mensaje
    claro en vez del texto crudo de requests."""
    pass


class OpenLibraryClient:
    # Límite deliberadamente conservador de este lado -- OpenLibrary no
    # publica una cuota documentada para /search.json, esto es solo
    # precaución propia para no abusar de un servicio público y gratuito
    # (mismo criterio ya usado en ComicVineClient/GoogleBooksClient).
    _MAX_REQUESTS_PER_WINDOW = 60
    _WINDOW_SECONDS = 60.0
    # Reintento corto ante un 5xx -- mismo criterio defensivo que
    # GoogleBooksClient, aunque en las pruebas que motivaron este cliente
    # OpenLibrary respondió sin fallos.
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
                r = self.session.get(f"{OPENLIBRARY_BASE}{endpoint}", params=params, timeout=10)
                if r.status_code >= 500 and attempt < self._MAX_5XX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.ConnectionError:
                raise ConnectionError("Sin conexión a internet.")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code >= 500:
                    raise OpenLibraryUnavailableError(
                        "El servicio de OpenLibrary no está disponible ahora mismo "
                        "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
                raise

    def search_volumes(self, query: str) -> list:
        data = self._get("/search.json", q=query, limit=20)
        return data.get("docs", []) or []

    def get_work_description(self, work_key: str) -> str:
        """/search.json (usado por search_volumes) NO trae una sinopsis de
        verdad -- como mucho author_name/first_sentence (ver
        build_book_info). La descripción real solo está en el detalle de la
        obra, /works/{id}.json, y llega como string suelto o como
        {"type": ..., "value": "..."} según el registro -- de ahí que se
        pida aparte, en segundo plano, solo cuando hace falta mostrarla (ver
        gui/app.py::_maybe_load_openlibrary_description), en vez de en cada
        resultado de una búsqueda con potencialmente 20 candidatos.
        work_key: tal cual lo da /search.json en "key" (p.ej. "/works/OL1815415W").
        Devuelve "" si no hay descripción o si la petición falla -- nunca lanza."""
        try:
            data = self._get(work_key + ".json")
        except Exception:
            return ""
        description = data.get("description", "")
        if isinstance(description, dict):
            description = description.get("value", "")
        return description or ""

    def build_book_info(self, result: dict) -> MediaInfo:
        title = result.get("title", "")
        authors = result.get("author_name") or []
        year = str(result.get("first_publish_year", "") or "")
        cover_i = result.get("cover_i")
        poster_url = f"{OPENLIBRARY_COVERS}/b/id/{cover_i}-L.jpg" if cover_i else None
        # Sin llamada extra a /works/{id}.json solo por la sinopsis -- no es
        # esencial para identificar/renombrar, y evitar una segunda petición
        # por resultado mantiene la búsqueda rápida. first_sentence, cuando
        # existe, ya viene incluida en la propia respuesta de /search.json.
        first_sentence = result.get("first_sentence")
        if isinstance(first_sentence, list):
            first_sentence = first_sentence[0] if first_sentence else ""
        first_sentence = first_sentence or ""
        overview = (", ".join(authors) + ("\n\n" + first_sentence if first_sentence else "")).strip() \
            if authors else first_sentence
        return MediaInfo(
            tmdb_id=result.get("key", ""),
            media_type="libro",
            title=title,
            original_title=title,
            year=year,
            poster_url=poster_url,
            season=None,
            episode=None,
            episode_title=None,
            overview=overview,
            genres=[],
            genre_ids=["ebook"],
        )
