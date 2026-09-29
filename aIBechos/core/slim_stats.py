"""
Ranking de adelgazamientos por usuario ("Liberar espacio" → "Adelgazar"),
compartido entre todos los clientes de aIBechos que apuntan al mismo
servidor FTP -- mismo patrón que core/upload_stats.py y
core/deletion_stats.py, mismo motivo (mirror local en disco + función
pura para sumar un reemplazo, sin tocar la red -- ver gui/app.py para
la sincronización real).

Cada reemplazo completado (gordo borrado del servidor + ligera subida,
ver core/slim_pending.py y el evento "slim_replaced" de
core/auto_watcher.py) SUMA los bytes ahorrados (gordo − ligera) a quien
lo lanzó (`added_by` del pendiente, que sale de "Tu nombre" en Ajustes).
Deliberadamente un archivo APARTE, no el mismo contador que subidas o
borrados: adelgazar no es ni subir contenido nuevo ni borrarlo sin más.

Formato: {"jose": {"display_name": "Jose", "total_bytes": 123456789,
"total_items": 12, "first_slim_ts": epoch, "last_slim_ts": epoch}}
-- la clave es el nombre normalizado (ver _normalize_key), mismo criterio
que core/upload_stats.py para que "Jose"/"José" sumen al mismo total.
"""

import json
import time
import unicodedata

from core.appdirs import app_data_dir

_FILENAME = "slim_stats.json"


def _path():
    return app_data_dir() / _FILENAME


def _normalize_key(person: str) -> str:
    """Minúsculas y sin acentos -- mismo criterio que
    core.upload_stats._normalize_key, ver ahí el porqué."""
    text = unicodedata.normalize("NFKD", person or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip().casefold()


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


def add_slim(data: dict, person: str, saved_bytes: int, ts: float = None) -> dict:
    """Devuelve un dict NUEVO con el adelgazamiento sumado (no muta `data`)
    -- mismo patrón que core.upload_stats.add_upload. Sin person (nadie
    configuró "Tu nombre" en Ajustes), no hay a quién sumarle el ahorro
    -- se devuelve data tal cual."""
    key = _normalize_key(person)
    if not key:
        return data
    if ts is None:
        ts = time.time()
    result = dict(data)
    entry = dict(result.get(key) or {
        "display_name": person.strip(), "total_bytes": 0,
        "total_items": 0, "first_slim_ts": ts, "last_slim_ts": ts,
    })
    entry["display_name"] = person.strip()   # última grafía vista gana la forma mostrada
    entry["total_bytes"] += saved_bytes
    entry["total_items"] += 1
    entry["last_slim_ts"] = ts
    result[key] = entry
    return result


def top_slimmers(data: dict, limit: int = 10) -> list:
    return sorted(data.values(), key=lambda e: e.get("total_bytes", 0), reverse=True)[:limit]
