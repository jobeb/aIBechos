"""
Términos "basura" (calidad, codec, créditos de subida...) aprendidos del
fallback de IA (ver core/ai_title_fallback.py) cuando la detección local no
encuentra resultados en TMDB. Se guardan aquí para que la próxima vez que
aparezca ese mismo término, se reconozca solo — sin volver a llamar a la IA.
"""

import json
import re

from core.appdirs import app_data_dir

_FILENAME = "learned_junk_terms.json"
_cache: list[str] | None = None


def _path():
    return app_data_dir() / _FILENAME


def _read_from_disk() -> list[str]:
    path = _path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [t for t in data if isinstance(t, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_learned_terms() -> list[str]:
    global _cache
    if _cache is None:
        _cache = _read_from_disk()
    return list(_cache)


def _save(terms: list[str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(terms, f, ensure_ascii=False, indent=2)


def add_learned_terms(new_tokens: list[str]) -> list[str]:
    """Añade tokens nuevos (sin duplicar y descartando ruido obvio como
    extensiones de archivo coladas por error) y los persiste. Devuelve la
    lista completa resultante.

    Los tokens de menos de 3 caracteres se descartan -- bug real: la IA
    aprendió "LA" como término basura (probablemente de un marcador de
    idioma pegado, p.ej. "...LAT.mkv"), y como los términos aprendidos se
    usan como \\b(termino)\\b insensible a mayúsculas (ver
    core/api_client.py::_learned_junk_pattern), "LA" cortaba el título de
    CUALQUIER archivo futuro que contuviera esa palabra suelta -- p.ej.
    "Avatar LA Busqueda" se quedaba truncado en solo "Avatar", con
    consecuencias serias (serie equivocada al buscar). Un término de 1-2
    letras es casi siempre demasiado genérico para ser un marcador técnico
    de verdad; los de 3+ (p.ej. "TS", "NF" YA están en la lista estática
    JUNK_MARKERS, revisada a mano, no aquí) tienen muchas menos
    probabilidades de coincidir por casualidad con parte de un título."""
    existing = load_learned_terms()
    existing_lower = {t.lower() for t in existing}
    for tok in new_tokens or []:
        tok = (tok or "").strip()
        if not tok:
            continue
        if len(tok) < 3:
            continue
        if re.fullmatch(r"\.[a-zA-Z0-9]{2,4}", tok):   # extensión colada por error (".mkv"...)
            continue
        if tok.lower() in existing_lower:
            continue
        existing.append(tok)
        existing_lower.add(tok.lower())
    global _cache
    _cache = existing
    _save(existing)
    return existing


def set_learned_terms(terms: list[str]) -> list[str]:
    """Reemplaza la lista entera (usado al importar una copia de seguridad
    de la configuración) -- a diferencia de add_learned_terms, no fusiona
    con lo que ya hubiera, deja exactamente lo que se pase aquí."""
    cleaned = []
    seen_lower = set()
    for tok in terms or []:
        tok = (tok or "").strip()
        if not tok or tok.lower() in seen_lower:
            continue
        cleaned.append(tok)
        seen_lower.add(tok.lower())
    global _cache
    _cache = cleaned
    _save(cleaned)
    return cleaned


def remove_learned_term(term: str) -> list[str]:
    """Quita un término aprendido (p.ej. si resultó ser un error de la IA
    y forma parte de algún título real). Devuelve la lista resultante."""
    existing = load_learned_terms()
    remaining = [t for t in existing if t.lower() != (term or "").strip().lower()]
    global _cache
    _cache = remaining
    _save(remaining)
    return remaining


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None
