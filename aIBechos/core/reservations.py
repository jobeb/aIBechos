"""
Reservas de espacio compartidas entre todos los clientes de aIBechos que
apuntan al mismo servidor FTP (ver gui/app.py para la sincronización real --
este módulo solo tiene el mirror local en disco y las funciones puras de
add/remove/is_reserved/cuota, sin tocar la red). Mismo patrón que
core/favorites.py, pero cada entrada queda atada al usuario que la reservó
(campo "reserved_by") y cuenta contra su cuota individual de QUOTA_BYTES --
a diferencia de favoritos, que es un concepto compartido sin dueño.

El contenido "de verdad" vive en un único JSON en el FTP (ruta derivada de
config.py::DEFAULTS["shared_data_ftp_path"], ver gui/app.py
::_reservations_remote_path); este archivo local es solo un mirror para
poder mostrar el último estado conocido sin esperar a una conexión FTP.

Formato: {"tv:1234": {"media_type": "tv", "tmdb_id": 1234, "name": "...",
"size_bytes": 123456789, "reserved_by": "Jose", "reserved_at": 1789700000}}

"reserved_at" (cuándo, epoch) es solo informativo para los tooltips.
Las entradas de antes de que existiera este campo no lo tienen y se
muestran sin fecha.
"""

import json
from typing import Optional

from core.appdirs import app_data_dir

_FILENAME = "reservations.json"

QUOTA_BYTES = 100 * 1024 ** 3   # 100 GB por usuario


def _path():
    return app_data_dir() / _FILENAME


def _reservation_key(media_type: str, tmdb_id: int) -> str:
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


def add_reservation(data: dict, media_type: str, tmdb_id: int, name: str,
                      size_bytes: int, user: str, at: int = 0) -> dict:
    """Devuelve un dict NUEVO con la reserva añadida (no muta `data`), para
    que el llamador pueda fusionarlo sobre el contenido remoto recién
    descargado sin arrastrar cambios de otro cliente que ya no aplican.
    Sobrescribe cualquier reserva previa de la misma clave, incluso si era
    de otro usuario -- el llamador (gui/app.py) es quien decide si eso debe
    permitirse antes de llegar aquí (ver reserved_by). *at* (cuándo, epoch)
    es solo informativo para tooltips."""
    result = dict(data)
    result[_reservation_key(media_type, tmdb_id)] = {
        "media_type": media_type, "tmdb_id": tmdb_id, "name": name,
        "size_bytes": size_bytes, "reserved_by": user, "reserved_at": int(at or 0),
    }
    return result


def remove_reservation(data: dict, media_type: str, tmdb_id: int) -> dict:
    result = dict(data)
    result.pop(_reservation_key(media_type, tmdb_id), None)
    return result


def is_reserved(data: dict, media_type: str, tmdb_id: int) -> bool:
    return _reservation_key(media_type, tmdb_id) in data


def reserved_by(data: dict, media_type: str, tmdb_id: int) -> Optional[str]:
    entry = data.get(_reservation_key(media_type, tmdb_id))
    return entry.get("reserved_by") if entry else None


def reserved_at(data: dict, media_type: str, tmdb_id: int) -> int:
    """Cuándo (epoch) se hizo la reserva, 0 si no consta (entradas de
    antes de que existiera el campo)."""
    entry = data.get(_reservation_key(media_type, tmdb_id))
    if not isinstance(entry, dict):
        return 0
    try:
        return int(entry.get("reserved_at") or 0)
    except (TypeError, ValueError):
        return 0


def used_bytes(data: dict, user: str) -> int:
    """Bytes ya reservados por *user* -- suma de todas sus entradas, sin
    importar cuántos otros usuarios tengan reservas propias en el mismo
    JSON (cada uno cuenta solo contra su propia cuota)."""
    return sum(entry.get("size_bytes", 0) for entry in data.values()
               if entry.get("reserved_by") == user)


def remaining_bytes(data: dict, user: str, quota_bytes: int = QUOTA_BYTES) -> int:
    """quota_bytes por defecto es QUOTA_BYTES (100GB), pero el llamador
    puede pasar la cuota real configurada -- ver config.py::DEFAULTS
    ["reservation_quota_gb"], configuración de servidor en
    core/server_config.py -- en vez de asumir siempre el valor fijo."""
    return max(0, quota_bytes - used_bytes(data, user))


def fits_in_quota(data: dict, user: str, size_bytes: int, quota_bytes: int = QUOTA_BYTES) -> bool:
    """True si *user* todavía tiene sitio para reservar *size_bytes* más
    sin superar quota_bytes (100GB por defecto si el llamador no pasa la
    cuota configurada, ver remaining_bytes). No tiene en cuenta una
    reserva ya existente de la misma clave (el llamador debe restar su
    tamaño antes si el tamaño cambió, aunque en la práctica el tamaño de
    un ítem ya en el FTP no cambia entre un análisis y otro)."""
    return used_bytes(data, user) + size_bytes <= quota_bytes


def is_name_taken(data: dict, name: str, exclude: Optional[str] = None) -> bool:
    """True si *name* ya aparece como dueño de alguna reserva -- para
    evitar que dos personas distintas usando aIBechos contra el mismo
    servidor elijan sin querer el mismo "Tu nombre" (ver gui/app.py::
    _resolve_app_user_name_change) y acaben compartiendo cuota o
    bloqueándose mutuamente al intentar liberar una reserva ajena.
    *exclude* es el nombre anterior del propio usuario, si lo está
    cambiando -- sus propias reservas (todavía con el nombre viejo) no
    deben contar como "ya en uso"."""
    return any(entry.get("reserved_by") == name for entry in data.values()
               if entry.get("reserved_by") != exclude)


def transfer_reservations(data: dict, old_owner: str, new_owner: str) -> dict:
    """Devuelve un dict NUEVO con todas las reservas de *old_owner*
    reasignadas a *new_owner* -- para cuando alguien cambia "Tu nombre" en
    Ajustes y quiere conservar lo que ya tenía reservado (ver
    gui/app.py::_transfer_reservations)."""
    result = {}
    for key, entry in data.items():
        if entry.get("reserved_by") == old_owner:
            result[key] = {**entry, "reserved_by": new_owner}
        else:
            result[key] = entry
    return result


def remove_all_by_owner(data: dict, owner: str) -> dict:
    """Devuelve un dict NUEVO sin ninguna reserva de *owner* -- la otra
    opción al cambiar de nombre en Ajustes, "empezar de nuevo" en vez de
    traspasar (ver gui/app.py::_unprotect_all_reservations)."""
    return {key: entry for key, entry in data.items() if entry.get("reserved_by") != owner}
