"""
Caché de traducciones de título de cómic/manga vía IA (ver
core/ai_title_fallback.py::guess_original_comic_title_via_ai) -- ComicVine
es un catálogo mayoritariamente en inglés, así que el título detectado
localmente (a menudo en castellano) falla casi siempre; una vez la IA
traduce una serie con éxito (la búsqueda con el título traducido SÍ
encontró resultado en ComicVine), se guarda aquí para no volver a llamar a
la IA con la misma serie.
"""

import json

from core.appdirs import app_data_dir

_FILENAME = "learned_comic_titles.json"
_cache: dict[str, str] | None = None


def _path():
    return app_data_dir() / _FILENAME


def _normalize(title: str) -> str:
    return (title or "").strip().lower()


def _read_from_disk() -> dict[str, str]:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def load_comic_title_cache() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = _read_from_disk()
    return dict(_cache)


def _save(cache: dict[str, str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_cached_translation(local_title: str) -> str | None:
    """Busca por clave normalizada (strip + lower) -- el mismo título local
    puede llegar con distintos espacios/mayúsculas entre archivos."""
    key = _normalize(local_title)
    if not key:
        return None
    return load_comic_title_cache().get(key)


def add_comic_title_translation(local_title: str, original_title: str) -> dict[str, str]:
    """Guarda local_title -> original_title (clave normalizada) y persiste.
    Devuelve la caché completa resultante."""
    key = _normalize(local_title)
    original_title = (original_title or "").strip()
    if not key or not original_title:
        return load_comic_title_cache()
    existing = load_comic_title_cache()
    existing[key] = original_title
    global _cache
    _cache = existing
    _save(existing)
    return existing


def remove_comic_title_translation(local_title: str) -> dict[str, str]:
    """Quita una traducción cacheada (p.ej. si la IA se equivocó). Devuelve
    la caché resultante."""
    key = _normalize(local_title)
    existing = load_comic_title_cache()
    existing.pop(key, None)
    global _cache
    _cache = existing
    _save(existing)
    return existing


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None
