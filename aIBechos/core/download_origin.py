"""
Qué es cada descarga que eligió la app (Episodios, ⚡ autocompletado,
solicitudes web, Recomendado): al lanzarla se apunta el nombre EXACTO del
archivo de aMule junto con la ficha TMDB que se buscaba, y cuando ese
archivo llega a la carpeta vigilada AutoWatcher lo identifica con esa
ficha en vez de volver a adivinarla por el nombre.

Real: "DragonBall Daima 1x10(Castellano).mp4" se eligió buscando Dragon
Ball Daima 1x10, pero al subirlo TMDB no encontraba "DragonBall Daima"
(todo junto) y el archivo se quedaba sin identificar.

Solo para descargas que eligió la app: lo que el usuario baja a mano desde
la pestaña Descargas no se apunta y se identifica como siempre.

Fichero download_origins.json en app_data_dir(): [{name, hash, tmdb_id,
media_type ("tv"/"movie"), title, season, episode (None en packs de
temporada y películas), year, added_ts}]. Caduca a los 30 días y se
guardan como mucho MAX_ENTRIES (las más recientes).
"""

from __future__ import annotations

import json
import threading
import time

from core.appdirs import app_data_dir
from core.applog import get_logger
from core.atomic_write import write_json_atomic

_log = get_logger("aIBechos.download_origin", "app.log")

_FILENAME = "download_origins.json"
MAX_AGE_SECS = 30 * 24 * 3600
MAX_ENTRIES = 500

# Lo escriben los hilos de descarga y lo lee el watcher.
_LOCK = threading.Lock()


def _path():
    return app_data_dir() / _FILENAME


def _norm_name(name: str) -> str:
    return (name or "").strip().casefold()


def _load() -> list:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        _log.debug("download_origins.json ilegible, se ignora", exc_info=True)
        return []


def _prune(entries: list, now: float) -> list:
    fresh = [e for e in entries if now - float(e.get("added_ts") or 0) < MAX_AGE_SECS]
    return fresh[-MAX_ENTRIES:]


def record(name: str, tmdb_id, media_type: str, title: str = "", season=None,
           episode=None, year=None, hash_hex: str = "") -> bool:
    """Apunta que el archivo *name* (nombre del resultado de aMule) es
    *tmdb_id*/*media_type* (y temporada/episodio). Sustituye una entrada
    anterior con el mismo nombre. Nunca lanza; True si se guardó."""
    try:
        tid = int(tmdb_id or 0)
    except (TypeError, ValueError):
        tid = 0
    if not name or tid <= 0 or media_type not in ("tv", "movie"):
        return False
    entry = {
        "name": name, "hash": (hash_hex or "").lower(), "tmdb_id": tid,
        "media_type": media_type, "title": title or "",
        "season": int(season) if season is not None else None,
        "episode": int(episode) if episode is not None and media_type == "tv" else None,
        "year": str(year or ""), "added_ts": time.time(),
    }
    try:
        with _LOCK:
            key = _norm_name(name)
            entries = [e for e in _load() if _norm_name(e.get("name")) != key]
            entries.append(entry)
            write_json_atomic(_path(), _prune(entries, entry["added_ts"]))
        _log.info("Origen de descarga: %r -> %s %s %s%s", name, media_type, tid, title,
                  f" {season}x{episode:02d}" if media_type == "tv" and season is not None
                  and episode is not None else "")
        return True
    except Exception:
        _log.warning("No se pudo guardar el origen de %r", name, exc_info=True)
        return False


def find(filename: str, detected: dict | None = None) -> dict | None:
    """La entrada apuntada para *filename* (mismo nombre, sin distinguir
    mayúsculas), o None. Con *detected* (detect_episode del archivo), si el
    nombre declara una temporada/episodio distinta de la apuntada no se
    fía y devuelve None. En un pack de temporada (episodio sin apuntar) el
    episodio sale del nombre. Nunca lanza."""
    try:
        key = _norm_name(filename)
        if not key:
            return None
        with _LOCK:
            entries = _prune(_load(), time.time())
        match = None
        for e in reversed(entries):
            if _norm_name(e.get("name")) == key:
                match = dict(e)
                break
        if match is None:
            return None
        det = detected or {}
        if match.get("media_type") == "tv":
            ds, de = det.get("season"), det.get("episode")
            if match.get("season") is not None and ds is not None and int(ds) != int(match["season"]):
                return None
            if match.get("episode") is not None and de is not None and int(de) != int(match["episode"]):
                return None
            if match.get("season") is None:
                match["season"] = ds
            if match.get("episode") is None:
                match["episode"] = de
            if match.get("season") is None or match.get("episode") is None:
                return None
        return match
    except Exception:
        _log.debug("find(%r) falló", filename, exc_info=True)
        return None
