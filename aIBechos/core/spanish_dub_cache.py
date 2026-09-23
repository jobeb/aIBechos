"""
Caché persistente del interruptor "Ocultar sin doblaje ES" en Episodios que
faltan (ver gui/app.py::_start_spanish_dub_check) -- separada de
core/missing_episodes_cache.py porque se rellena bajo demanda, solo al
activar el interruptor, no en cada escaneo normal de huecos.

Formato: {tmdb_id_como_texto: {"spanish_available": bool|None,
"episodes": {"{temporada}x{episodio:02d}": bool}, "checked_at": epoch_segundos,
"eldoblaje": {"cutoff": {"{temporada}": ultimo_episodio_doblado}, "checked_at": epoch_segundos}}}

"spanish_available" es el resultado (cacheado, una vez por serie) de
/tv/{id}/watch/providers para la región "ES" -- evita repetir esa consulta
por cada episodio de la misma serie.

"eldoblaje" es el corte de doblaje {temporada: último_episodio_doblado}
extraído automáticamente del texto libre de eldoblaje.com (ver
core/eldoblaje.py::parse_dub_cutoff, 0 = nada doblado de esa temporada),
con su propio "checked_at" para no re-preguntar al sitio en cada chequeo
-- manda sobre el texto de TMDB para sus temporadas (ver
App._start_spanish_dub_check). Las claves de "cutoff" salen de json como
texto tras recargar: quien lo lee acepta int y str (ver el atajo por
episodio en el worker). Sin ficha en eldoblaje.com, sin corte parseable o
con fallo de red, "cutoff" queda en {} y se sigue con TMDB como siempre.

"checked_at" (epoch de la última vez que se comprobó ALGÚN episodio de
esta serie, ver App._start_spanish_dub_check) sirve para caducar la
entrada -- ver is_stale()/MAX_AGE_DAYS más abajo. Sin esto, una entrada
guardada aquí no se volvía a comprobar NUNCA, aunque TMDB cambiara
(nueva plataforma disponible, texto traducido añadido más tarde...) o el
propio resultado fuera de entrada un falso positivo (ver
core/missing_episodes.py::episode_has_spanish_text). Entradas de antes de
que existiera este campo no tienen "checked_at" -- se tratan como
caducadas (None cuenta como "hace más de MAX_AGE_DAYS"), no como un
error.

Este chequeo basado en TMDB es el comportamiento POR DEFECTO del
interruptor -- se sabe que puede dar falsos positivos (TMDB traduce el
texto de episodios sin relación con si hay audio doblado, ver el caso real
de Bleach en gui/app.py::_visible_missing_ep_row), pero es gratis y
automático. Si el usuario pulsa "🤖 Preguntar a la IA" para una serie
concreta, el veredicto de la IA (ver core/missing_episodes_ai.py) SUSTITUYE
a este para esa serie -- nunca al revés.
"""

import json

from core.appdirs import app_data_dir

_FILENAME = "spanish_dub_cache.json"
_cache: dict | None = None

MAX_AGE_DAYS = 30.0   # mismo valor que DEFAULT_HALF_LIFE_DAYS de core/trending.py, por convención, no por relación funcional


def is_stale(entry: dict, now: float) -> bool:
    """True si *entry* (una entrada de serie de este caché) no se ha
    comprobado en más de MAX_AGE_DAYS, o nunca se comprobó (sin
    "checked_at" -- entradas de antes de que existiera este campo)."""
    checked_at = entry.get("checked_at")
    if checked_at is None:
        return True
    return (now - checked_at) / 86400 > MAX_AGE_DAYS


def _path():
    return app_data_dir() / _FILENAME


def _read_from_disk() -> dict:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = _read_from_disk()
    return _cache


def save_cache(cache: dict) -> None:
    global _cache
    _cache = cache
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None
