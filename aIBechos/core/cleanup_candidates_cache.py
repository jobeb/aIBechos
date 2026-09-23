"""
Caché de la lista de candidatas de "Liberar espacio" (ver
gui/app.py::_scan_cleanup_candidates). El análisis completo puede tardar
más de un minuto en un servidor grande (consulta a Jellyfin/Plex de todos
los usuarios + recorrido del FTP) -- esto guarda el resultado para que no
haya que repetirlo cada vez que se abre la app; solo hace falta pulsar
"Analizar servidor" a mano para refrescarlo de verdad.

Además del mirror LOCAL de siempre, el mismo resultado se comparte entre
clientes vía un archivo en el FTP (ver gui/app.py::
_push_cleanup_candidates_to_ftp/_sync_cleanup_candidates_from_ftp) -- así
que cuando UNA persona pulsa "Analizar servidor", el resto de clientes del
mismo servidor ven ese resultado sin tener que repetir el análisis ellos
mismos. A diferencia de core/category_stats.py (que suma/resta
incrementalmente), aquí no hace falta ninguna versión de escaneo ni
bootstrap: un análisis fresco SIEMPRE reemplaza la lista compartida
entera -- acaba de consultar el estado real del servidor, es la fuente
más fiable posible, no hay nada que "recalcular desde cero" aparte.

Formato en disco/remoto: {"items": [dict de cada CleanupItem, ...],
"last_scan_ts": float, "scanned_by": str}
"""

import json
from dataclasses import asdict

from core.appdirs import app_data_dir

_FILENAME = "cleanup_candidates_cache.json"


def _path():
    return app_data_dir() / _FILENAME


def _items_to_json_list(items: list) -> list:
    return [asdict(it) for it in items]


def _items_from_json_list(raw: list):
    """Lista de CleanupItem, o None si *raw* no tiene la forma esperada
    (formato antiguo/incompatible, o dato remoto corrupto) -- nunca
    lanza, para no romper la app ni el hilo de sincronización con un solo
    campo mal formado."""
    from core.cleanup_candidates import CleanupItem
    try:
        return [CleanupItem(**d) for d in raw]
    except (TypeError, AttributeError):
        return None


def load_cache() -> dict:
    """{"items": [CleanupItem, ...], "last_scan_ts": float, "scanned_by":
    str}, o {} si no hay caché todavía o está corrupta/de un formato
    antiguo."""
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    items = _items_from_json_list(data.get("items", []))
    if items is None:
        return {}   # formato antiguo/incompatible -- se descarta, no se rompe la app
    return {"items": items, "last_scan_ts": data.get("last_scan_ts"), "scanned_by": data.get("scanned_by", "")}


def save_cache(items: list, last_scan_ts: float, scanned_by: str = "") -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"items": _items_to_json_list(items), "last_scan_ts": last_scan_ts, "scanned_by": scanned_by}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def wrap_for_remote(items: list, last_scan_ts: float, scanned_by: str) -> dict:
    """Mismo formato que save_cache -- función aparte solo para dejar
    claro en gui/app.py cuál de los dos usos es (local vs. compartido),
    aunque el formato en sí sea idéntico."""
    return {"items": _items_to_json_list(items), "last_scan_ts": last_scan_ts, "scanned_by": scanned_by}


def unwrap_from_remote(payload) -> "dict | None":
    """Contrario de wrap_for_remote(). None si *payload* no es un dict
    con una lista de items válida -- corrupto, vacío, o de una versión
    del formato con la que CleanupItem ya no coincide."""
    if not isinstance(payload, dict):
        return None
    items = _items_from_json_list(payload.get("items", []))
    if items is None:
        return None
    return {"items": items, "last_scan_ts": payload.get("last_scan_ts"), "scanned_by": payload.get("scanned_by", "")}
