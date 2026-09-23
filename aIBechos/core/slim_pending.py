"""
Reemplazos pendientes de "Adelgazar" en Liberar espacio (ver
gui/app.py::_slim_download_one/_slim_download_many y
core/auto_watcher.py::_upload_to_ftp).

Al lanzar la descarga de una versión ligera se registra de qué gordo es
reemplazo; cuando la ligera llega a la carpeta vigilada, AutoWatcher
borra el gordo del servidor ANTES de subirla (sin esto, la ligera se
omitiría como duplicada y el gordo quedaría para siempre).

Seguridad (el gordo no se toca salvo que TODO se cumpla):
- La ligera ya está completa y estabilizada en local (el watcher espera
  a STABLE_WAIT igual que siempre antes de llegar aquí).
- La ligera pesa <= max_light_size (85% del gordo, el mismo tope con el
  que se descargó): si no, no hay match y no se borra nada.
- Si la descarga nunca llega o falla, no se registra nada (solo se
  registra al lanzarse de verdad) y el pendiente caduca a los 30 días.
- Si el borrado del gordo falla, el pendiente se conserva y el archivo
  NO se marca como procesado: se reintenta en el próximo ciclo con el
  gordo intacto.

Fichero slim_pending.json en app_data_dir(): [{media_type ("tv" o
"movie"), key_norm (título normalizado de serie o película), season,
episode (solo tv), year (solo movie, "" si se desconoce),
heavy_remote_file (ruta remota completa del gordo), heavy_size,
max_light_size, query (informativo), added_ts, added_by}].
"""

from __future__ import annotations

import json
import threading
import time

from core.appdirs import app_data_dir

_FILENAME = "slim_pending.json"
_MAX_AGE_SECS = 30 * 24 * 3600

# Los pendientes los tocan la GUI (al lanzar ⬇) y el watcher (al
# emparejar/consumir): leer-modificar-escribir bajo lock, mismo motivo
# que _DB_LOCK de auto_processed.json.
_LOCK = threading.Lock()


def _path():
    return app_data_dir() / _FILENAME


def norm_key(name: str) -> str:
    """Misma normalización en quien registra (GUI, nombre de la
    candidata) y quien empareja (watcher, título TMDB)."""
    try:
        from core.series_match import normalize_series_name
        return normalize_series_name(name or "")
    except Exception:
        return (name or "").strip().lower()


def _entry_key(entry: dict) -> tuple:
    e = entry or {}
    return (e.get("media_type") or "", e.get("key_norm") or "",
            e.get("season"), e.get("episode"), e.get("year") or "")


def _prune(entries: list) -> list:
    now = time.time()
    return [e for e in (entries or [])
            if isinstance(e, dict) and now - float(e.get("added_ts") or 0) < _MAX_AGE_SECS]


def load_pending() -> list:
    """Lee los pendientes, quitando caducados (persistiendo la limpieza
    solo si cayó alguno). Nunca lanza excepción."""
    try:
        with _LOCK:
            p = _path()
            if not p.exists():
                return []
            data = json.loads(p.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else []
            pruned = _prune(entries)
            if len(pruned) != len(entries):
                _save_locked(pruned)
            return pruned
    except Exception:
        return []


def _save_locked(entries: list) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries or [], ensure_ascii=False, indent=2),
                 encoding="utf-8")


def save_pending(entries: list) -> None:
    try:
        with _LOCK:
            _save_locked(entries)
    except Exception:
        pass


def upsert(entries: list, entry: dict) -> list:
    """Devuelve lista nueva con *entry* (reemplaza el pendiente con su
    misma clave si existía: re-lanzar un ⬇ no duplica). Pura, testeable."""
    key = _entry_key(entry)
    return [e for e in (entries or []) if _entry_key(e) != key] + [dict(entry)]


def record(entry: dict) -> dict:
    """Registra un reemplazo (lee + upsert + guarda, atómico bajo lock).
    Devuelve *entry*."""
    try:
        with _LOCK:
            p = _path()
            entries = []
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    entries = _prune(data if isinstance(data, list) else [])
                except Exception:
                    entries = []
            _save_locked(upsert(entries, entry))
    except Exception:
        pass
    return entry


def consume(entry: dict) -> None:
    """Elimina el pendiente con la clave de *entry* (tras borrar el
    gordo con éxito, o si el gordo ya no está). Atómico bajo lock."""
    key = _entry_key(entry)
    try:
        with _LOCK:
            p = _path()
            entries = []
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    entries = data if isinstance(data, list) else []
                except Exception:
                    entries = []
            _save_locked([e for e in entries if _entry_key(e) != key])
    except Exception:
        pass


def find_match(entries: list, media_type: str, title: str,
               season=None, episode=None, year: str = "",
               local_size: int = 0) -> dict | None:
    """¿Es esta llegada la ligera de algún gordo pendiente? Pura,
    testeable. Exige identidad (título normalizado + temporada/episodio
    o año) Y que pese <= max_light_size del pendiente."""
    if media_type not in ("tv", "movie"):
        return None
    try:
        local_size = int(local_size or 0)
    except (TypeError, ValueError):
        return None
    if local_size <= 0:
        return None
    want = norm_key(title)
    if not want:
        return None
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        if e.get("media_type") != media_type:
            continue
        if (e.get("key_norm") or "") != want:
            continue
        if media_type == "tv":
            try:
                if int(e.get("season")) != int(season) or int(e.get("episode")) != int(episode):
                    continue
            except (TypeError, ValueError):
                continue
        else:
            want_year = str(year or "").strip()
            if e.get("year") and want_year and str(e.get("year")) != want_year:
                continue
        try:
            max_light = int(e.get("max_light_size") or 0)
        except (TypeError, ValueError):
            continue
        if max_light <= 0 or local_size > max_light:
            continue
        if not e.get("heavy_remote_file"):
            continue
        return e
    return None
