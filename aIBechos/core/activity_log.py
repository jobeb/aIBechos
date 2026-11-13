"""
Historial de actividad compartido (subidas y borrados de todos los clientes,
aIBechos_actividad.json en la carpeta compartida): retención y fusión.

Antes se recortaba a los últimos 500 registros. Con ~150 subidas en un solo
día eso cubría menos de dos semanas y el gráfico "Subidas en los últimos 30
días" (y las rachas, core/streaks.py) empezaba a mitad de mes. Ahora se
guarda por ANTIGÜEDAD (RETENTION_DAYS) con un tope de seguridad
(MAX_ENTRIES) para que el archivo no crezca sin fin.

Los clientes con una versión anterior siguen recortando a 500 al subir algo.
Para que eso no vuelva a borrar historia, cada cliente nuevo guarda una copia
local (ARCHIVE_FILE) de todo lo que ha visto y la fusiona con la remota al
leerla y al escribirla: lo que un cliente viejo recorte se repone en la
siguiente subida de uno nuevo. Funciones puras salvo load/save_archive.
"""

import json
import os
import time

from core.appdirs import app_data_dir
from core.applog import get_logger

_log = get_logger("aIBechos.activity_log", "app.log")

RETENTION_DAYS = 90
MAX_ENTRIES = 10000
ARCHIVE_FILE = "activity_archive.json"


def _key(e: dict) -> tuple:
    """Identidad de un registro: el mismo evento llega igual desde el
    remoto y desde la copia local, así que basta con sus campos propios."""
    try:
        ts = round(float(e.get("ts", 0) or 0), 3)
    except (TypeError, ValueError):
        ts = 0.0
    return (e.get("kind", ""), ts, e.get("person", ""),
            e.get("filename", "") or e.get("name", ""), e.get("size", 0),
            e.get("remote", ""))


def merge(*lists) -> list:
    """Une listas de registros sin duplicados, ordenada por ts (antiguo
    primero, como el archivo remoto). Ignora lo que no sea un dict."""
    seen = {}
    for lst in lists:
        for e in lst or []:
            if isinstance(e, dict):
                seen.setdefault(_key(e), e)

    def _ts(e):
        try:
            return float(e.get("ts", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    return sorted(seen.values(), key=_ts)


def prune(entries: list, now: float | None = None) -> list:
    """Quita lo más antiguo que RETENTION_DAYS y, si aun así sobran, se
    queda con los MAX_ENTRIES más recientes. *entries* ordenada por ts."""
    now = time.time() if now is None else now
    cutoff = now - RETENTION_DAYS * 86400
    kept = []
    for e in entries:
        try:
            if float(e.get("ts", 0) or 0) >= cutoff:
                kept.append(e)
        except (TypeError, ValueError):
            continue
    return kept[-MAX_ENTRIES:]


def _archive_path() -> str:
    return os.path.join(str(app_data_dir()), ARCHIVE_FILE)


def load_archive() -> list:
    try:
        with open(_archive_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        _log.warning("Historial de actividad: copia local ilegible (%s), se ignora", e)
        return []


def save_archive(entries: list) -> None:
    path = _archive_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        _log.warning("Historial de actividad: no se pudo guardar la copia local: %s", e)


def reconcile(remote: list, now: float | None = None) -> list:
    """Fusiona *remote* con la copia local, aplica la retención, guarda la
    copia local y devuelve la lista resultante (lo que se enseña y, al
    escribir, lo que se sube)."""
    result = prune(merge(remote, load_archive()), now)
    save_archive(result)
    return result
