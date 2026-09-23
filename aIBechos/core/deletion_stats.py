"""
Ranking de borrados por usuario ("Liberar espacio"), compartido entre
todos los clientes de aIBechos que apuntan al mismo servidor FTP --
mismo patrón que core/upload_stats.py, mismo motivo (mirror local en
disco + función pura para sumar un borrado, sin tocar la red -- ver
gui/app.py para la sincronización real).

Deliberadamente un archivo APARTE del ranking de subidas, no el mismo
contador restado: mezclar "sube" y "borra" en un único total no tendría
sentido (¿cuánto "aporta" alguien que sube 500 GB y luego borra 400 GB
de contenido de otra persona?). Aquí cada persona acumula sus propios
bytes liberados, sin relación con lo que haya subido.

Formato: {"jose": {"display_name": "Jose", "total_bytes": 123456789,
"total_items": 12, "first_deletion_ts": epoch, "last_deletion_ts": epoch}}
-- la clave es el nombre normalizado (ver _normalize_key), mismo criterio
que core/upload_stats.py para que "Jose"/"José" sumen al mismo total.
"""

import json
import time
import unicodedata

from core.appdirs import app_data_dir

_FILENAME = "deletion_stats.json"


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


def add_deletion(data: dict, person: str, size_bytes: int, ts: float = None) -> dict:
    """Devuelve un dict NUEVO con el borrado sumado (no muta `data`) --
    mismo patrón que core.upload_stats.add_upload. Sin person (nadie
    configuró "Tu nombre" en Ajustes), no hay a quién sumarle el borrado
    -- se devuelve data tal cual."""
    key = _normalize_key(person)
    if not key:
        return data
    if ts is None:
        ts = time.time()
    result = dict(data)
    entry = dict(result.get(key) or {
        "display_name": person.strip(), "total_bytes": 0,
        "total_items": 0, "first_deletion_ts": ts, "last_deletion_ts": ts,
    })
    entry["display_name"] = person.strip()   # última grafía vista gana la forma mostrada
    entry["total_bytes"] += size_bytes
    entry["total_items"] += 1
    entry["last_deletion_ts"] = ts
    result[key] = entry
    return result


def top_deleters(data: dict, limit: int = 10) -> list:
    return sorted(data.values(), key=lambda e: e.get("total_bytes", 0), reverse=True)[:limit]
