"""
Audio real del servidor (Plex/Jellyfin) como señal de doblaje al
castellano -- ver core/media_server_refresh.py.

A diferencia del chequeo TMDB (texto traducido, no audio) o de
eldoblaje.com (texto libre de la comunidad), esto mira las PISTAS DE
AUDIO de los archivos que ya están indexados en el servidor: es la
única señal de doblaje 100% real y gratis. Límite honesto: solo calibra
episodios PRESENTES, nunca los que faltan (para esos mandan
eldoblaje/wiki/IA).

Castellano de España exigido (decisión del usuario): una pista marcada
explícitamente como latina NO cuenta como doblaje. Sin marca regional
("spa"/"Español" a secas) se acepta -- la mayoría de servidores en
España etiquetan así el castellano; lo explícitamente latino se
rechaza. Sin información de audio -> None (sin dato, no se supone).
"""

# Marcas que confirman castellano de España en DisplayTitle/título.
_CASTILIAN_MARKERS = (
    "españa", "espana", "castellano", "castilian", "castillian",
    "es-es", "es_es",
)

# Marcas que confirman español LATINO (no vale como doblaje).
_LATIN_MARKERS = (
    "latino", "latinoamérica", "latinoamerica", "méxico", "mexico",
    "argentina", "colombia", "chile", "es-419", "es-mx", "es-ar",
    "es-cl", "es-co", "neutral",
)

# Códigos de idioma que son español en cualquier variante.
_SPANISH_CODES = {
    "spa", "es", "esp", "spanish", "español", "espanol", "castellano",
}


def _track_text(track: dict) -> str:
    """Todo el texto identificativo de una pista, en minúsculas."""
    parts = [
        track.get("Language"), track.get("language"),
        track.get("DisplayTitle"), track.get("displayTitle"),
        track.get("display_title"),
        track.get("title"), track.get("Title"),
        track.get("Name"), track.get("name"),
        track.get("languageTag"), track.get("language_tag"),
        track.get("LanguageTag"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _track_lang_code(track: dict) -> str:
    """Código de idioma normalizado de la pista (o "")."""
    for key in ("Language", "language", "languageTag", "language_tag",
                "LanguageTag", "iso_639_1", "IsoCode"):
        val = track.get(key)
        if val:
            code = str(val).strip().lower().replace("_", "-")
            # "spa", "es", "es-ES"... se comparan tal cual abajo.
            return code
    return ""


def _is_audio_track(track: dict) -> bool:
    """True si el dict describe una pista de AUDIO (Jellyfin usa
    Type="Audio", Plex streamType=2). Sin marcador conocido se acepta
    (algunas respuestas no lo traen) para no perder la señal."""
    t = track.get("Type", track.get("type"))
    if t is not None and str(t).lower() not in ("audio", "2"):
        return False
    st = track.get("streamType", track.get("stream_type"))
    if st is not None and str(st) != "2":
        return False
    return True


def classify_audio_tracks(tracks: list) -> bool | None:
    """True si hay pista de castellano, False si hay audio pero NADA de
    castellano (p. ej. solo latino/inglés: doblaje confirmado ausente),
    None si no hay información de audio aprovechable.

    Precedencia por pista: marca castellana explícita > marca latina
    explícita > español genérico (se acepta, ver docstring del módulo).
    """
    if not tracks:
        return None
    saw_audio = False
    saw_generic_spanish = False
    for track in tracks or ():
        if not isinstance(track, dict) or not _is_audio_track(track):
            continue
        saw_audio = True
        text = _track_text(track)
        code = _track_lang_code(track)
        if any(m in text for m in _CASTILIAN_MARKERS) or code in ("es-es",):
            return True
        if any(m in text for m in _LATIN_MARKERS) or code in (
                "es-419", "es-mx", "es-ar", "es-cl", "es-co"):
            continue  # latina: no suma, pero tampoco decide aún
        base = code.split("-")[0] if code else ""
        if base in _SPANISH_CODES or "español" in text or "espanol" in text:
            saw_generic_spanish = True
    if saw_generic_spanish:
        return True
    if saw_audio:
        # Hay pistas de audio y ninguna es española: sin doblaje.
        # (Si alguna era latina, cayó aquí igualmente: latino != castellano.)
        return False
    return None


def episode_key(season: int, episode: int) -> str:
    """Clave "SxEE" compatible con spanish_dub_cache.json."""
    return f"{season}x{episode:02d}"


def audio_map_to_episodes(audio_map: dict) -> dict:
    """{(temporada, episodio): True|False|None} -> {"SxEE": bool}, sin los
    None (sin dato: no se escribe nada, ante la duda no se dice nada)."""
    result = {}
    for (season, ep), verdict in (audio_map or {}).items():
        if verdict is None:
            continue
        try:
            result[episode_key(int(season), int(ep))] = bool(verdict)
        except (TypeError, ValueError):
            continue
    return result


def cutoff_from_audio(audio_map: dict) -> dict:
    """{temporada: último_episodio_doblado} a partir de veredictos por
    episodio del servidor -- formato que entiende
    filter_missing_by_dub_cutoff. Solo temporadas donde el tramo inicial
    1..N es TODO True (sin huecos ni unknowns): el corte afirma "hasta N
    hay doblaje", así que un hueco intermedio invalida el resto (no se
    puede afirmar que E05 esté doblado si E04 es desconocido)."""
    by_season: dict[int, dict[int, bool | None]] = {}
    for (season, ep), verdict in (audio_map or {}).items():
        try:
            by_season.setdefault(int(season), {})[int(ep)] = verdict
        except (TypeError, ValueError):
            continue
    result = {}
    for season, eps in by_season.items():
        cut = 0
        for ep in sorted(eps):
            if eps[ep] is True and ep == cut + 1:
                cut = ep
            else:
                break
        if cut > 0:
            result[season] = cut
    return result


def remap_audio_keys(audio_map: dict, season_counts: dict) -> dict:
    """Reasigna claves {(s, e)} de numeración absoluta a numeración por
    temporada con la misma regla acumulada que
    missing_episodes.remap_absolute_episodes (los servidores a veces
    indexan anime largo de corrido). Claves fuera de rango se descartan,
    igual que allí."""
    cumulative = []
    total = 0
    for season in sorted(season_counts):
        try:
            count = int(season_counts[season])
        except (TypeError, ValueError):
            continue
        cumulative.append((total, total + count, season))
        total += count
    remapped = {}
    for (season, ep), verdict in (audio_map or {}).items():
        try:
            absolute_ep = int(ep)
        except (TypeError, ValueError):
            continue
        for start, end, real_season in cumulative:
            if start < absolute_ep <= end:
                remapped[(real_season, absolute_ep - start)] = verdict
                break
    return remapped
