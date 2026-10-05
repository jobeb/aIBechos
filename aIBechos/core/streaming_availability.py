"""
Streaming Availability API (Movie of the Night) como señal de doblaje al
castellano -- ver https://docs.movieofthenight.com/.

`GET /v4/shows/{tmdbId}?country=es&series_granularity=episode` trae la
serie entera con `streamingOptions.es[].audios[]` POR EPISODIO, cada audio
con idioma ISO 639-2 + región (`spa` + `ESP` = castellano, `spa` + `419`
= latino). Es señal de audio real por plataforma en España, no texto
traducido.

Reglas (decisión del usuario: castellano exigido):
- `spa` + región `ESP` (o sin región) en ALGUNA plataforma ES -> doblado.
- Solo `spa` + `419` (u otros audios) -> NO castellano (solo latino).
- Episodio sin opciones ES o sin audios -> sin dato (None): la ausencia
  de oferta no afirma nada (real: Simpson S3E1 vacío en Disney+ ES).

Los objetos season/episode NO traen números (verificado en vivo) -- se
mapean por posición (seasons[i] = temporada i+1, episodes[j] = episodio
j+1) y se validan contra los tamaños de TMDB: si alguna temporada no
cuadra en nº de episodios, no se emite veredicto para ella.

1 llamada = 1 serie completa (plan gratis: 1000 req/mes). Nunca lanza:
{} / None al fallar.
"""

import requests

from core.applog import get_logger

_log = get_logger("aIBechos.streaming_avail", "ai_fallback.log")

_BASE = "https://api.movieofthenight.com/v4"
_COUNTRY = "es"

# Regiones que cuentan como castellano de España (ISO 3166-1 alpha-3 o
# UN M49 según la doc; ESP verificado en vivo). Vacío = se acepta (la
# mayoría de ofertas ES etiquetan así el castellano).
_CASTILIAN_REGIONS = {"ESP", "ES", "724", ""}
# Regiones explícitamente latinas (419 verificado en vivo; resto códigos
# UN M49 de América Latina/Caribe).
_LATIN_REGIONS = {"419", "013", "029", "005"}

_TIMEOUT = 30


def _headers(api_key: str) -> dict:
    return {"X-API-Key": api_key or ""}


def get_show(api_key: str, tmdb_id: int, timeout: int = _TIMEOUT,
             granularity: str = "episode") -> dict | None:
    """Show completo con granularidad por episodio (o "show" para solo
    metadatos, útil para validar la key gastando menos), o None si falla.
    El id es el mismo de TMDB con prefijo ("tv/236994", "movie/155")."""
    if not api_key or not tmdb_id:
        return None
    try:
        resp = requests.get(
            f"{_BASE}/shows/tv/{tmdb_id}",
            headers=_headers(api_key),
            params={"country": _COUNTRY, "series_granularity": granularity,
                    "output_language": "en"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        _log.warning("streaming-availability: fallo al pedir tv/%s: %s", tmdb_id, e)
        return None


def _episode_castilian(streaming_options: dict) -> bool | None:
    """True si ALGUNA opción ES trae audio spa+ESP (o spa sin región),
    False si hay opciones ES con audio pero ninguna castellana (p. ej.
    solo latino/japonés), None si no hay opciones ES o no traen audios."""
    if not isinstance(streaming_options, dict):
        return None
    options = streaming_options.get("es") or streaming_options.get("ES") or []
    if not options:
        return None
    saw_audio = False
    for opt in options:
        for audio in (opt or {}).get("audios", []) or []:
            if not isinstance(audio, dict):
                continue
            lang = str(audio.get("language") or "").lower()
            if lang not in ("spa", "es", "spanish", "español", "espanol"):
                if lang:
                    saw_audio = True
                continue
            saw_audio = True
            region = str(audio.get("region") or "").upper()
            if region in _LATIN_REGIONS:
                continue
            if region in _CASTILIAN_REGIONS:
                return True
    if saw_audio:
        return False
    return None


def cutoff_from_show(show: dict, season_sizes: dict | None = None) -> dict:
    """{temporada: último_episodio_doblado} desde el show con granularidad
    por episodio. Mapeo posicional validado contra *season_sizes*
    ({temporada: nº_episodios} de TMDB): la temporada que no cuadre se
    omite entera. Solo tramos iniciales TODO True (igual que
    server_audio.cutoff_from_audio: un hueco intermedio invalida el
    resto). Sin season_sizes no se puede validar el mapeo y se devuelve
    {} (ante la duda no se dice nada)."""
    if not show or not season_sizes:
        return {}
    seasons = show.get("seasons") or []
    result = {}
    for idx, season in enumerate(seasons, start=1):
        episodes = (season or {}).get("episodes") or []
        try:
            expected = int(season_sizes.get(idx, season_sizes.get(str(idx), 0)) or 0)
        except (TypeError, ValueError):
            continue
        if expected <= 0 or len(episodes) != expected:
            continue
        cut = 0
        for j, ep in enumerate(episodes, start=1):
            verdict = _episode_castilian((ep or {}).get("streamingOptions") or {})
            if verdict is True and j == cut + 1:
                cut = j
            else:
                break
        if cut > 0:
            result[idx] = cut
    return result


def cutoff_for_series(api_key: str, tmdb_id: int, season_sizes: dict,
                      timeout: int = _TIMEOUT) -> dict:
    """Atajo: show + corte, o {} -- lo que consume el worker de doblaje."""
    show = get_show(api_key, tmdb_id, timeout=timeout)
    if not show:
        return {}
    return cutoff_from_show(show, season_sizes)
