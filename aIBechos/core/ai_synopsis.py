"""Sinopsis en castellano vía IA (Groq) para las fichas.

TMDB a menudo no trae overview en español: la ficha mostraba "Sin
sinopsis disponible" aunque en inglés sí hubiera. Con IA activada (la
misma key e interruptor que el resto: ai_fallback_enabled + ai_api_key,
nada a terceros sin activarlo), se traduce una vez y se cachea en
ai_overview_cache.json {kind:id: {"es": texto}} para no pagar dos veces
la misma traducción. Nunca lanza: "" si no hay nada que mostrar.
"""

import json
import time

import requests

from core.ai_title_fallback import DEFAULT_MODEL, GROQ_URL
from core.appdirs import app_data_dir
from core.applog import get_logger

TMDB_BASE = "https://api.themoviedb.org/3"

_log = get_logger("aIBechos.ai_sinopsis", "ai_fallback.log")


def ai_key_if_enabled(config) -> str:
    """API key de Groq si el usuario activó la IA, "" si no (las fichas
    lo miran antes de traducir nada)."""
    try:
        return (config.get("ai_api_key", "") or "") if config.get("ai_fallback_enabled") else ""
    except Exception:
        return ""


def _cache_path():
    try:
        return app_data_dir() / "ai_overview_cache.json"
    except Exception:
        return None


def _load_cache() -> dict:
    try:
        p = _cache_path()
        if p is not None and p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        p = _cache_path()
        if p is not None:
            p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def es_overview(kind: str, tmdb_id: int, tmdb_api_key: str, ai_api_key: str,
                model: str = DEFAULT_MODEL, timeout: int = 20) -> str:
    """Sinopsis en castellano de (kind, tmdb_id): caché, si no la inglesa
    de TMDB traducida por IA. "" si no hay sinopsis en ningún idioma o
    falla algo. kind: "tv"|"movie" (los libros no pasan por aquí)."""
    try:
        key = f"{kind}:{int(tmdb_id)}"
    except (TypeError, ValueError):
        return ""
    if kind not in ("tv", "movie") or not tmdb_id:
        return ""
    cache = _load_cache()
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("es"):
        return hit["es"]
    if not tmdb_api_key or not ai_api_key:
        return ""
    try:
        resp = requests.get(
            f"{TMDB_BASE}/{kind}/{int(tmdb_id)}",
            params={"api_key": tmdb_api_key, "language": "en-US"},
            timeout=10)
        resp.raise_for_status()
        english = (resp.json().get("overview") or "").strip()
    except Exception as e:
        _log.warning("Sinopsis: no se pudo leer la inglesa de %s: %s", key, e)
        return ""
    if not english:
        return ""
    _log.info("Sinopsis: traduciendo %s (%d caracteres)", key, len(english))
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {ai_api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system",
                     "content": "Traduces sinopsis de películas y series del inglés al castellano "
                                "de España (película, serie, ordenador... nunca latino). Responde SOLO "
                                "con un JSON de una línea: {\"es\": \"<traducción>\"}, sin texto adicional."},
                    {"role": "user", "content": english},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout)
        resp.raise_for_status()
        text = str(json.loads(resp.json()["choices"][0]["message"]["content"]).get("es") or "").strip()
    except Exception as e:
        _log.warning("Sinopsis: Groq falló para %s: %s", key, e)
        return ""
    if not text:
        return ""
    cache[key] = {"es": text, "ts": time.time()}
    _save_cache(cache)
    _log.info("Sinopsis: traducida %s (%d caracteres)", key, len(text))
    return text
