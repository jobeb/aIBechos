"""
Veredictos de doblaje castellano (solo los obtenidos por IA, ver
gui/app.py::_ask_ai_about_current_missing_ep_show) compartidos entre todos
los clientes de aIBechos que apuntan al mismo servidor FTP -- mismo
motivo y mismo patrón que core/favorites.py/core/reservations.py: este
módulo solo tiene el mirror local en disco y la función pura para fijar el
veredicto de una serie, sin tocar la red (ver gui/app.py para la
sincronización real).

Deliberadamente NO se comparte el chequeo automático de TMDB (ver
core/spanish_dub_cache.py) -- ese es gratis y cada cliente ya lo repite
solo (caduca cada 30 días); lo que de verdad merece la pena compartir es
el resultado de una consulta a la IA que alguien ya pagó, para que el
resto de clientes del mismo servidor no tengan que repetirla.

El contenido "de verdad" vive en un único JSON en el FTP (ruta
configurable, config.py::DEFAULTS["shared_data_ftp_path"]); este archivo
local es solo un mirror para poder mostrar el último veredicto conocido
sin esperar a una conexión FTP.

Formato: {"1234": {"veredicto": "hueco_real"|"numeracion_distinta",
"motivo": "...", "doblaje_castellano": {"2": 2}, "checked_at": epoch,
"checked_by": "nombre"}} -- mismo objeto que devuelve
core.missing_episodes_ai.analyze_missing_episodes por serie, con
checked_at/checked_by añadidos.
"""

import json
import time

from core.appdirs import app_data_dir

_FILENAME = "shared_dub_verdicts.json"


def _path():
    return app_data_dir() / _FILENAME


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


def set_verdict(data: dict, tmdb_id: int, verdict: dict, checked_by: str,
                 checked_at: float = None) -> dict:
    """Devuelve un dict NUEVO con el veredicto de *tmdb_id* fijado (no muta
    `data`), para que el llamador pueda fusionarlo sobre el contenido
    remoto recién descargado -- mismo patrón que add_favorite/
    add_reservation. Sobrescribe sin comprobar nada: el que gana es
    siempre el último en escribir, que en el flujo real (descargar
    remoto fresco, aplicar esto, subir) es equivalente a "el veredicto
    más reciente gana"."""
    result = dict(data)
    result[str(tmdb_id)] = {
        **verdict,
        "checked_at": checked_at if checked_at is not None else time.time(),
        "checked_by": checked_by,
    }
    return result
