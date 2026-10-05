"""
Respaldo de doblaje cuando eldoblaje.com no da corte (sin ficha, sin
sección "Más información" o redactado no parseable -- ver
core/eldoblaje.py): wiki de doblaje en español vía MediaWiki API, que
devuelve texto plano (extracts) en vez de wikitext, sin dependencias
nuevas (mismo criterio que eldoblaje.py: requests + regex).

El parseo a {temporada: último_doblado} reutiliza
eldoblaje.parse_dub_cutoff (los mismos patrones en español valen para
el extracto). Nunca lanza: []/{}/"" al fallar, quien llama sigue como
si esta consulta no existiera.
"""

import requests

from core.applog import get_logger

_log = get_logger("aIBechos.doblaje_wiki", "ai_fallback.log")

# Wikipedia/Fandom responden 403 al User-Agent por defecto de requests
# (verificado en vivo el 2026-10-05) -- UA de navegador, mismo criterio
# que core/crunchyroll_client.py.
_WIKI_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0 Safari/537.36")}

# Doblaje Fandom primero (específica de doblaje al español), Wikipedia ES
# como respaldo general (sus artículos de series suelen detallar el
# doblaje por temporadas).
_APIS = (
    "https://doblaje.fandom.com/api.php",
    "https://es.wikipedia.org/w/api.php",
)


def search_series(title: str, timeout: int = 10) -> list[dict]:
    """[{"title": str, "api": url}, ...] -- artículos cuyo título encaja
    con *title*, una entrada por wiki consultada como mucho (el primer
    resultado de cada una). [] si nada o falla algo."""
    if not title:
        return []
    results = []
    for api in _APIS:
        try:
            resp = requests.get(api, params={
                "action": "query", "list": "search",
                "srsearch": title, "srlimit": 3, "format": "json",
            }, headers=_WIKI_HEADERS, timeout=timeout)
            resp.raise_for_status()
            hits = (resp.json().get("query") or {}).get("search", []) or []
        except Exception as e:
            _log.warning("wiki doblaje: fallo al buscar '%s' en %s: %s", title, api, e)
            continue
        for hit in hits:
            name = (hit.get("title") or "").strip()
            if name:
                results.append({"title": name, "api": api})
                break
    return results


def get_summary(page_title: str, api: str, timeout: int = 10) -> str:
    """Texto plano del artículo (extracto) -- "" si falla o no hay."""
    if not page_title or not api:
        return ""
    try:
        resp = requests.get(api, params={
            "action": "query", "prop": "extracts", "explaintext": 1,
            "exintro": 0, "titles": page_title, "format": "json",
        }, headers=_WIKI_HEADERS, timeout=timeout)
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages", {}) or {}
    except Exception as e:
        _log.warning("wiki doblaje: fallo al leer '%s': %s", page_title, e)
        return ""
    for page in pages.values():
        text = (page.get("extract") or "").strip()
        if text:
            return text
    return ""


def cutoff_for_series(title: str, seasons: list[int] | tuple[int, ...],
                      timeout: int = 10) -> dict:
    """{temporada: último_doblado} desde la wiki, o {} -- atajo que combina
    search + summary + parse para el primer candidato con corte."""
    from core.eldoblaje import parse_dub_cutoff
    for cand in search_series(title, timeout=timeout):
        summary = get_summary(cand["title"], cand["api"], timeout=timeout)
        if not summary:
            continue
        cutoff = parse_dub_cutoff(summary, seasons)
        if cutoff:
            return cutoff
    return {}
