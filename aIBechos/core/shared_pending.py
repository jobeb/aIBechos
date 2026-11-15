"""Cambios de favoritos/reservas que aún no han llegado al servidor.

Al marcar un favorito o una reserva se aplica en local y se sube al JSON
compartido (descargar fresco, aplicar el cambio, volver a subir). Si esa
subida fallaba (sin red, FTP caído), la siguiente sincronización traía la
copia del servidor y la ponía encima: el cambio desaparecía sin aviso.

Ahora cada cambio se apunta aquí (en disco, sobrevive a cerrar la app)
hasta que una subida lo confirma, y toda sincronización vuelve a aplicar
los pendientes sobre lo que haya en el servidor antes de quedarse con ello.

Formato: {"favoritos": [{"op": "add", "args": [...]}, ...], "reservas": [...]}
Las operaciones son las funciones puras de core/favorites.py y
core/reservations.py, por nombre (ver OPS)."""

import json
import threading
import uuid

from core.appdirs import app_data_dir
from core.atomic_write import write_json_atomic

_FILENAME = "shared_pending.json"
_lock = threading.RLock()


def _ops():
    from core import favorites as fav, reservations as res
    return {
        "favoritos": {"add": fav.add_favorite, "remove": fav.remove_favorite},
        "reservas": {"add": res.add_reservation, "remove": res.remove_reservation},
    }


def _path():
    return app_data_dir() / _FILENAME


def _load() -> dict:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)


def add(key: str, op: str, args) -> None:
    with _lock:
        data = _load()
        data.setdefault(key, []).append({"id": uuid.uuid4().hex, "op": op, "args": list(args)})
        _save(data)


def pending(key: str) -> list:
    with _lock:
        items = _load().get(key) or []
        return [x for x in items if isinstance(x, dict)]


def confirm(key: str, items: list) -> None:
    """Quita de *key* los pendientes *items* (los que ya entraron en una
    subida correcta), por su id: los apuntados después, o los que otra
    subida en marcha aún no ha confirmado, se quedan."""
    done = {x.get("id") for x in items or [] if isinstance(x, dict)}
    if not done:
        return
    with _lock:
        data = _load()
        rest = [x for x in (data.get(key) or []) if not (isinstance(x, dict) and x.get("id") in done)]
        if rest:
            data[key] = rest
        else:
            data.pop(key, None)
        _save(data)


def replay(key: str, data: dict, items: list) -> dict:
    """*data* con los pendientes *items* aplicados en orden. Uno roto o
    desconocido se salta (nunca lanza)."""
    ops = _ops().get(key, {})
    out = dict(data or {})
    for item in items or []:
        fn = ops.get(item.get("op"))
        if fn is None:
            continue
        try:
            out = fn(out, *item.get("args", []))
        except Exception:
            continue
    return out
