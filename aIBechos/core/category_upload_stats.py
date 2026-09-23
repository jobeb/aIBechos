"""
Ranking de subidas por usuario, DESGLOSADO POR CATEGORÍA -- un panel de
Estadísticas por cada categoría configurada (Series, Películas,
SeriesPeques, Documentales...), cada uno con su propio top de subidores,
en vez de un único ranking global (core/upload_stats.py, que sigue
existiendo aparte para el panel "Top 10 subidores" de conjunto y no se
toca aquí).

Formato: {category_id: {"category_name": "...", "uploaders": {person_key:
{"display_name", "total_bytes", "total_files", "first_upload_ts",
"last_upload_ts"}, ...}}} -- cada "uploaders" tiene EXACTAMENTE la misma
forma que el dict de nivel superior de core/upload_stats.py, así que
core.upload_stats.top_uploaders()/add_upload() se reutilizan tal cual
sobre data[category_id]["uploaders"], sin duplicar esa lógica de sumar/
ordenar/limitar aquí.
"""

import json

from core.appdirs import app_data_dir
from core.upload_stats import add_upload

_FILENAME = "category_upload_stats.json"


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


def add_category_upload(data: dict, category_id: str, category_name: str,
                         person: str, size_bytes: int, ts: float = None) -> dict:
    """Devuelve un dict NUEVO (no muta `data`) con la subida sumada al
    ranking de esa categoría concreta -- delega en
    core.upload_stats.add_upload para la parte de "sumar a esta persona",
    solo añade el nivel de categoría por encima."""
    result = dict(data)
    cat_entry = dict(result.get(category_id) or {"category_name": category_name, "uploaders": {}})
    cat_entry["category_name"] = category_name
    cat_entry["uploaders"] = add_upload(cat_entry.get("uploaders", {}), person, size_bytes, ts)
    result[category_id] = cat_entry
    return result
