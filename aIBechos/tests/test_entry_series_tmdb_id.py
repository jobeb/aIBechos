"""¿En qué filas de Archivos aparece el rayo (⚡) de autocompletado?

El autocompletado busca capítulos que falten, así que solo tiene sentido en
series ya identificadas. Estas comprobaciones son de gui/app.py, que necesita
customtkinter instalado -- se saltan solas donde no lo haya (el CI, a
propósito, no instala las dependencias de interfaz; ver
.github/workflows/tests.yml).
"""

import pytest

pytest.importorskip("customtkinter")

from core.api_client import MediaInfo
from gui.app import App


class _Entrada:
    """Lo único que mira _entry_series_tmdb_id de un FileEntry."""
    def __init__(self, media_info=None):
        self.media_info = media_info


def _info(media_type, tmdb_id):
    return MediaInfo(tmdb_id=tmdb_id, media_type=media_type, title="X",
                     original_title="X", year="2024")


def test_una_serie_identificada_lleva_rayo():
    assert App._entry_series_tmdb_id(_Entrada(_info("tv", 30984))) == 30984


def test_el_anime_tambien_es_una_serie():
    assert App._entry_series_tmdb_id(_Entrada(_info("anime", 1429))) == 1429


def test_una_pelicula_no_lleva_rayo():
    """No tiene capítulos que completar."""
    assert App._entry_series_tmdb_id(_Entrada(_info("movie", 438631))) is None


def test_un_libro_o_comic_tampoco():
    assert App._entry_series_tmdb_id(_Entrada(_info("libro", "OL123W"))) is None


def test_un_archivo_sin_identificar_tampoco():
    """Todavía no se sabe de qué serie es."""
    assert App._entry_series_tmdb_id(_Entrada(None)) is None


def test_una_serie_sin_id_de_tmdb_tampoco():
    """Sin id no hay nada que marcar para autocompletar."""
    assert App._entry_series_tmdb_id(_Entrada(_info("tv", 0))) is None
    assert App._entry_series_tmdb_id(_Entrada(_info("tv", None))) is None
