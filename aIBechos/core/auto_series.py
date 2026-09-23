"""
Series con el rayo ⚡ (autocompletado) activo, compartidas entre todos los
clientes de aIBechos que apuntan al mismo servidor FTP (ver gui/app.py para
la sincronización real -- este módulo solo tiene el mirror local en disco
y las funciones puras, sin tocar la red). Mismo patrón que
core/favorites.py y core/reservations.py.

OJO: esto NO cambia qué descarga cada equipo -- el autocompletado sigue
siendo personal de cada instalación (config.json, ver
gui/app.py::_auto_complete_series). Lo compartido es solo informativo:
quién tiene cada serie en auto y desde cuándo, para avisar al activar un
rayo que otro equipo ya tiene puesto y mostrarlo en el tooltip.

Cada serie lleva un dict de dueños {nombre: epoch}, no un dueño único:
varios equipos pueden tener la misma serie en auto a la vez, y al
desactivar uno solo se borra su nombre (la entrada desaparece cuando no
queda ningún dueño).

El contenido "de verdad" vive en un único JSON en el FTP (ruta derivada de
config.py::DEFAULTS["shared_data_ftp_path"], ver gui/app.py
::_auto_series_remote_path); este archivo local es solo un mirror para
poder mostrar el último estado conocido sin esperar a una conexión FTP.

Formato: {"tv:1234": {"media_type": "tv", "tmdb_id": 1234, "name": "...",
"owners": {"Jose": 1789700000}}}
"""

import json

from core.appdirs import app_data_dir

_FILENAME = "auto_series.json"


def _path():
    return app_data_dir() / _FILENAME


def _auto_key(media_type: str, tmdb_id: int) -> str:
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


def _owners_of(entry) -> dict:
    """{dueño: epoch} de una entrada, {} si no trae dueños válidos
    (entradas antiguas o corruptas no rompen nada)."""
    if not isinstance(entry, dict):
        return {}
    owners = entry.get("owners")
    if not isinstance(owners, dict):
        return {}
    clean = {}
    for owner, ts in owners.items():
        if not owner:
            continue
        try:
            clean[str(owner)] = int(ts or 0)
        except (TypeError, ValueError):
            clean[str(owner)] = 0
    return clean


def add_auto_owner(data: dict, media_type: str, tmdb_id: int, name: str,
                   user: str, at: int = 0) -> dict:
    """Devuelve un dict NUEVO con *user* como dueño del auto de la serie
    (no muta `data`). Mantiene a los demás dueños: activar el rayo no
    pisa el de otro equipo. *at* (epoch) es para el tooltip."""
    result = dict(data)
    key = _auto_key(media_type, tmdb_id)
    owners = _owners_of(result.get(key))
    owners[user] = int(at or 0)
    result[key] = {
        "media_type": media_type, "tmdb_id": tmdb_id, "name": name,
        "owners": owners,
    }
    return result


def remove_auto_owner(data: dict, media_type: str, tmdb_id: int, user: str) -> dict:
    """Devuelve un dict NUEVO sin *user* como dueño del auto de la serie.
    Si no quedan dueños, la entrada desaparece del todo. Los demás dueños
    no se tocan: desactivar el rayo propio no apaga el ajeno."""
    result = dict(data)
    key = _auto_key(media_type, tmdb_id)
    owners = _owners_of(result.get(key))
    owners.pop(user, None)
    if owners:
        entry = result.get(key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["owners"] = owners
        result[key] = entry
    else:
        result.pop(key, None)
    return result


def auto_owners(data: dict, media_type: str, tmdb_id: int) -> dict:
    """{dueño: epoch} del auto de la serie, {} si nadie la tiene."""
    return _owners_of(data.get(_auto_key(media_type, tmdb_id)))


def other_owners(data: dict, media_type: str, tmdb_id: int, user: str) -> dict:
    """Dueños del auto de la serie que NO son *user* -- para avisar al
    activar ("Ana ya lo tiene puesto") sin contarse a uno mismo."""
    return {o: ts for o, ts in auto_owners(data, media_type, tmdb_id).items()
            if o != user}


def transfer_auto_owner(data: dict, old_owner: str, new_owner: str) -> dict:
    """Devuelve un dict NUEVO con la propiedad del auto de *old_owner*
    reasignada a *new_owner* -- para cuando alguien cambia "Tu nombre" en
    Ajustes (ver gui/app.py::_resolve_app_user_name_change). Si el nombre
    nuevo ya era dueño de esa serie, se conserva su fecha (no se pisa)."""
    result = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            result[key] = entry
            continue
        owners = _owners_of(entry)
        if old_owner in owners:
            ts = owners.pop(old_owner)
            owners.setdefault(new_owner, ts)
            entry = dict(entry)
            entry["owners"] = owners
        result[key] = entry
    return result


def remove_all_auto_by_owner(data: dict, owner: str) -> dict:
    """Devuelve un dict NUEVO sin ninguna propiedad del auto de *owner* --
    la otra opción al cambiar de nombre ("desproteger todas"). Las series
    con más dueños se quedan con los demás."""
    result = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            result[key] = entry
            continue
        owners = _owners_of(entry)
        if owner not in owners:
            result[key] = entry
            continue
        owners.pop(owner)
        if not owners:
            continue
        entry = dict(entry)
        entry["owners"] = owners
        result[key] = entry
    return result
