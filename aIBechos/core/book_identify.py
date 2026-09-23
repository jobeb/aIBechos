"""
Identificación de un libro/cómic (título ya extraído localmente, ver
core/api_client.py::detect_episode(is_book=True)) vía OpenLibrary/Google
Books o ComicVine -- extraído de gui/app.py::_search_book_entry (antes solo
lo usaba el panel manual de la pestaña Archivos) para que
core/auto_watcher.py (Modo Automático) reutilice EXACTAMENTE la misma
lógica, incluida la traducción de título vía IA para ComicVine
(core/ai_title_fallback.py) y su caché (core/learned_comic_titles.py), en
vez de duplicarla.

Para libros de texto (no cómics), OpenLibrary es el proveedor PRINCIPAL
(gratis, sin ninguna API Key, y más fiable que Google Books desde que su
backend empezó a fallar de forma intermitente y persistente -- ver
core/book_client.py) y Google Books queda como apoyo automático: si
OpenLibrary falla (error de red/servidor) O no encuentra nada, se reintenta
con Google Books sin que el usuario tenga que hacer nada.
"""

import difflib
from dataclasses import dataclass
from typing import Optional

from core.api_client import MediaInfo


@dataclass
class BookIdentifyResult:
    media_info: Optional[MediaInfo] = None
    confidence: int = 0
    error: Optional[str] = None
    used_query: str = ""
    translated_via_ai: bool = False
    # Qué catálogo identificó el archivo -- "comicvine", "openlibrary" o
    # "google_books" -- para que quien llame pueda registrarlo en su log
    # (mismo espíritu que translated_via_ai).
    provider: str = ""


def identify_book_or_comic(det: dict, is_comic: bool,
                            comicvine_client, book_client, openlibrary_client=None,
                            ai_fallback_enabled: bool = False, ai_api_key: str = "") -> BookIdentifyResult:
    """det: resultado de detect_episode(is_book=True, is_comic=...) -- usa
    det["title"] (y det["episode"] para el número de emisión de un cómic).

    Para cómics, si ComicVine no encuentra nada con el título detectado
    localmente (a menudo en castellano -- su catálogo es mayoritariamente en
    inglés), reintenta con una traducción vía IA, cacheada para no repetir
    la consulta con la misma serie.

    Para libros de texto: OpenLibrary primero (si se pasa openlibrary_client),
    Google Books como apoyo si OpenLibrary falla o no encuentra nada.
    openlibrary_client=None (valor por defecto) salta directamente a Google
    Books, para no romper llamadores/tests que todavía no lo pasan.

    Nunca lanza -- cualquier fallo (sin resultados, error de red/API...) se
    devuelve en BookIdentifyResult.error en vez de propagar una excepción.
    No registra nada en ningún log -- eso es responsabilidad de quien
    llama (gui/app.py::_search_book_entry, core/auto_watcher.py), cada uno
    con su propio logger/archivo de log."""
    query = det.get("title", "")
    if not query:
        return BookIdentifyResult(error="No se pudo detectar el nombre")

    used_query = query
    translated_via_ai = False
    provider = ""

    if is_comic:
        try:
            from core.learned_comic_titles import get_cached_translation, add_comic_title_translation
            cached = get_cached_translation(query)
            used_query = cached or query
            results = comicvine_client.search_volumes(used_query)
            if not results and not cached and ai_fallback_enabled and ai_api_key:
                from core.ai_title_fallback import guess_original_comic_title_via_ai
                translated = guess_original_comic_title_via_ai(query, ai_api_key)
                if translated:
                    retry_results = comicvine_client.search_volumes(translated)
                    if retry_results:
                        results           = retry_results
                        used_query        = translated
                        translated_via_ai = True
                        add_comic_title_translation(query, translated)
        except Exception as e:
            return BookIdentifyResult(error=f"Error de ComicVine: {e}", used_query=used_query)
        if not results:
            return BookIdentifyResult(error="Sin resultados en ComicVine", used_query=used_query)
        provider = "comicvine"
    else:
        results = []
        ol_error = None
        ol_attempted = openlibrary_client is not None
        if openlibrary_client is not None:
            try:
                results = openlibrary_client.search_volumes(query)
                if results:
                    provider = "openlibrary"
            except Exception as e:
                ol_error = str(e)
        if not results and book_client is not None:
            try:
                results = book_client.search_volumes(query)
                if results:
                    provider = "google_books"
            except Exception as e:
                msg = f"Error de Google Books: {e}"
                if ol_error:
                    msg = f"Error de OpenLibrary: {ol_error}; {msg}"
                return BookIdentifyResult(error=msg, used_query=used_query)
        if not results:
            if ol_error:
                return BookIdentifyResult(
                    error=f"Error de OpenLibrary: {ol_error}; sin resultados en Google Books",
                    used_query=used_query)
            if ol_attempted:
                # OpenLibrary SÍ se consultó (a diferencia del caso de abajo)
                # y respondió sin fallo, solo sin coincidencias -- mencionarlo
                # es honesto sobre qué se intentó de verdad.
                return BookIdentifyResult(
                    error="Sin resultados en OpenLibrary ni en Google Books", used_query=used_query)
            return BookIdentifyResult(error="Sin resultados en Google Books", used_query=used_query)

    top = results[0]
    if is_comic:
        result_title = ((top.get("volume") or {}).get("name") or top.get("name", "")).lower()
    elif provider == "openlibrary":
        result_title = (top.get("title", "") or "").lower()
    else:
        result_title = (top.get("volumeInfo", {}) or {}).get("title", "").lower()
    confidence = round(difflib.SequenceMatcher(None, used_query.lower(), result_title).ratio() * 100)

    if is_comic:
        info = comicvine_client.build_comic_info(top, episode=det.get("episode"))
    elif provider == "openlibrary":
        info = openlibrary_client.build_book_info(top)
    else:
        info = book_client.build_book_info(top)

    return BookIdentifyResult(media_info=info, confidence=confidence, used_query=used_query,
                               translated_via_ai=translated_via_ai, provider=provider)
