"""
Ranking de subidas por usuario, compartido entre todos los clientes de
aIBechos que apuntan al mismo servidor FTP -- mismo motivo y mismo
patrón que core/favorites.py/core/shared_dub_verdicts.py: este módulo
solo tiene el mirror local en disco y la función pura para sumar una
subida, sin tocar la red (ver gui/app.py para la sincronización real).

Deliberadamente NO es lo mismo que el historial de actividad compartido
(aIBechos_actividad.json, ver gui/app.py) -- ese guarda solo los
últimos 500 registros (los antiguos se descartan), válido para "actividad
reciente" pero no para un ranking que de verdad importe a largo plazo: si
alguien lleva meses subiendo, su aportación de hace tres meses no debe
"desaparecer" del ranking solo porque el historial rotó. Este archivo
nunca rota -- cada subida SUMA a un total por persona, para siempre.

El contenido "de verdad" vive en un único JSON en el FTP (ruta
configurable, config.py::DEFAULTS["shared_data_ftp_path"]); este archivo
local es solo un mirror para poder mostrar el último ranking conocido sin
esperar a una conexión FTP.

Formato: {"jose": {"display_name": "Jose", "total_bytes": 123456789,
"total_files": 42, "first_upload_ts": epoch, "last_upload_ts": epoch}}
-- la clave es el nombre normalizado (ver _normalize_key) para que
"Jose"/"jose"/"José" sumen al mismo total en vez de fragmentarse;
display_name conserva la última grafía vista, para mostrar un nombre
real en vez de la clave normalizada.
"""

import json
import time
import unicodedata

from core.appdirs import app_data_dir

_FILENAME = "upload_stats.json"


def _path():
    return app_data_dir() / _FILENAME


def _normalize_key(person: str) -> str:
    """Minúsculas y sin acentos (mismo criterio que
    core.series_match.normalize_series_name) -- casefold() por sí solo NO
    quita acentos ("José".casefold() == "josé", no "jose"), así que sin
    esto "Jose"/"José" seguirían fragmentándose en dos entradas distintas
    del ranking."""
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


def add_upload(data: dict, person: str, size_bytes: int, ts: float = None) -> dict:
    """Devuelve un dict NUEVO con la subida sumada (no muta `data`), para
    que el llamador pueda fusionarlo sobre el contenido remoto recién
    descargado -- mismo patrón que add_favorite/set_verdict. Sin
    person (nadie configuró "Tu nombre" en Ajustes), no hay a quién
    sumarle la subida -- se devuelve data tal cual."""
    key = _normalize_key(person)
    if not key:
        return data
    if ts is None:
        ts = time.time()
    result = dict(data)
    entry = dict(result.get(key) or {
        "display_name": person.strip(), "total_bytes": 0,
        "total_files": 0, "first_upload_ts": ts, "last_upload_ts": ts,
    })
    entry["display_name"] = person.strip()   # última grafía vista gana la forma mostrada
    entry["total_bytes"] += size_bytes
    entry["total_files"] += 1
    entry["last_upload_ts"] = ts
    result[key] = entry
    return result


def top_uploaders(data: dict, limit: int = 10) -> list:
    return sorted(data.values(), key=lambda e: e.get("total_bytes", 0), reverse=True)[:limit]
