"""
Favoritos compartidos entre todos los clientes de aIBechos que apuntan al
mismo servidor FTP (ver gui/app.py para la sincronización real -- este
módulo solo tiene el mirror local en disco y las funciones puras de
add/remove/is_favorite, sin tocar la red).

El contenido "de verdad" vive en un único JSON en el FTP (ruta configurable,
config.py::DEFAULTS["shared_data_ftp_path"]); este archivo local es solo un
mirror para poder mostrar el último estado conocido sin esperar a una
conexión FTP (arranque de la app, pestaña abierta sin red, etc.).

Formato: {"serie:1234": {"media_type": "tv", "tmdb_id": 1234, "name": "...",
"added_by": "Jose", "added_at": 1789700000}}

"added_by"/"added_at" (quién lo marcó y cuándo, epoch) son solo
informativos para los tooltips (ver gui/app.py): los favoritos siguen
siendo un concepto compartido sin dueño -- cualquiera puede desmarcar.
Las entradas de antes de que existieran estos campos no los tienen y se
muestran sin atribución.
"""

import json

from core.appdirs import app_data_dir

_FILENAME = "favorites.json"


def _path():
    return app_data_dir() / _FILENAME


def _favorite_key(media_type: str, tmdb_id: int) -> str:
    return f"{media_type}:{tmdb_id}"


def load_local_cache() -> dict:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_local_cache(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_favorite(data: dict, media_type: str, tmdb_id: int, name: str,
                 by: str = "", at: int = 0) -> dict:
    """Devuelve un dict NUEVO con el favorito añadido (no muta `data`), para
    que el llamador pueda fusionarlo sobre el contenido remoto recién
    descargado sin arrastrar cambios de otro cliente que ya no aplican.
    *by*/*at* (quién/cuándo) son solo informativos para tooltips."""
    result = dict(data)
    result[_favorite_key(media_type, tmdb_id)] = {
        "media_type": media_type, "tmdb_id": tmdb_id, "name": name,
        "added_by": by or "", "added_at": int(at or 0),
    }
    return result


def remove_favorite(data: dict, media_type: str, tmdb_id: int) -> dict:
    result = dict(data)
    result.pop(_favorite_key(media_type, tmdb_id), None)
    return result


def is_favorite(data: dict, media_type: str, tmdb_id: int) -> bool:
    return _favorite_key(media_type, tmdb_id) in data


def favorite_info(data: dict, media_type: str, tmdb_id: int) -> dict:
    """La entrada cruda del favorito (con added_by/added_at si se marcó
    con ellos) o {} si no es favorito / la entrada no es un dict."""
    entry = data.get(_favorite_key(media_type, tmdb_id))
    return entry if isinstance(entry, dict) else {}
