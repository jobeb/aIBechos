"""
Recomendador de películas y series de la pestaña "Recomendado": cruza las
listas de TMDB (películas: tendencias, populares, próximos estrenos, en
emisión; series: tendencias, populares, en emisión) con lo que de verdad
hay en Plex/Jellyfin, y recomienda lo que NO está en el servidor. Solo
informa -- la descarga se lanza desde la GUI (aMule) cuando el usuario la
pide, igual que Episodios que faltan.
"""


def build_movie_rows(lists_by_name: dict, server_tmdb_ids: set, watch_by_id: dict = None) -> list:
    """Convierte las listas de TMDB en filas de recomendación.

    lists_by_name: {"trending": [resultado_TMDB, ...], "popular": [...],
    "upcoming": [...], "now_playing": [...], "on_the_air": [...]} -- cada
    resultado es el dict crudo de la API, normalizado para que películas y
    series compartan campos: id, media_type, title, release_date,
    poster_path, vote_average, popularity, overview, genre_ids (ver
    TMDBClient._movie_list/_tv_list).
    server_tmdb_ids: set de tmdb_id que YA están en Plex/Jellyfin -- solo
    del MISMO tipo que las filas (la GUI llama una vez con las películas
    del servidor y otra con las series; los IDs de TMDB son espacios
    separados por tipo, una película y una serie pueden compartir número).
    watch_by_id: {tmdb_id: {"flatrate": ["Netflix", ...], "rent": [...],
    "buy": [...], ...}} con la disponibilidad fuera del cine (ver
    TMDBClient.get_movie_watch_providers); las filas sin entrada quedan con
    "watch": {} -- el filtro "Solo disponibles en plataformas" las descarta.
    Solo aplica a películas: las series (media_type "tv") nunca consultan
    watch providers, así que siempre quedan con "watch": {}.

    Devuelve una lista de filas (una por película/serie, sin duplicados):
    {"tmdb_id", "media_type", "title", "year", "release_date", "overview",
    "poster_url", "genre_ids", "vote_average", "popularity", "list",
    "in_server", "watch", "original_language", "origin_country"}.

    "original_language" (código ISO 639-1, p.ej. "ko", "ja", "zh") y
    "origin_country" (lista de códigos ISO 3166-1, p.ej. ["KR"]) vienen
    crudos de TMDB en las listas (las películas traen solo el idioma; las
    series, idioma y país). Alimentan el interruptor "Ocultar asiáticas"
    (ver apply_origin_filter): el usuario no quiere nada de Asia oriental
    ni del sur (China/Japón/Corea/India/Tailandia...), que puebla de sobra
    las listas de tendencias/populares.

    Una misma película (o serie) puede salir en varias listas (p.ej.
    trending y popular) -- se deduplica por (media_type, tmdb_id),
    quedándose con la PRIMERA lista en la que aparezca (el orden de
    lists_by_name manda: lo que el escaneo pase primero gana, típicamente
    trending primero). El media_type entra en la deduplicación porque TMDB
    numera películas y series en espacios separados: aunque el escaneo
    pase cada tipo por separado, una misma llamada podría acabar con ambos
    (los resultados de /trending ya traen media_type propio).
    "list" es la clave interna ("trending"/"popular"/"upcoming"/
    "now_playing"/"on_the_air"); la GUI la traduce a etiqueta con
    MOVIE_LIST_LABELS.
    """
    rows = []
    seen = set()
    for list_name, results in lists_by_name.items():
        for r in results or []:
            tid = r.get("id")
            if not tid:
                continue
            media_type = r.get("media_type") or "movie"
            if (media_type, tid) in seen:
                continue
            seen.add((media_type, tid))
            poster_path = r.get("poster_path")
            rows.append({
                "tmdb_id": tid,
                "media_type": media_type,
                "title": r.get("title", r.get("original_title", "")),
                "year": (r.get("release_date", "") or "")[:4],
                "release_date": r.get("release_date", ""),
                "overview": r.get("overview", ""),
                "poster_url": f"https://image.tmdb.org/t/p/w300{poster_path}" if poster_path else None,
                "genre_ids": list(r.get("genre_ids", []) or []),
                "vote_average": r.get("vote_average", 0),
                "popularity": r.get("popularity", 0),
                "list": list_name,
                "in_server": tid in server_tmdb_ids,
                "watch": dict((watch_by_id or {}).get(tid) or {}),
                "original_language": r.get("original_language", ""),
                "origin_country": list(r.get("origin_country", []) or []),
            })
    return rows


# Etiquetas legibles de cada lista de TMDB, para la columna "Lista" de la
# tabla y para el detalle -- traducidas, porque es texto de UI. "on_the_air"
# es la lista de series "En emisión" (las películas usan "now_playing" para
# el mismo concepto).
MOVIE_LIST_LABELS = {
    "trending": "Tendencias",
    "popular": "Populares",
    "upcoming": "Próximos estrenos",
    "now_playing": "En emisión",
    "on_the_air": "En emisión",
}


def apply_in_server_filter(rows: list, hide_in_server: bool) -> list:
    """Recorta *rows* a solo las que NO están en el servidor si
    hide_in_server es True; si es False devuelve todas (para ver el
    catálogo entero de las listas, marcando con "in_server" las que ya se
    tienen) -- mismo patrón que "Ocultar completas" en Episodios."""
    if not hide_in_server:
        return rows
    return [r for r in rows if not r.get("in_server")]


def watch_available(r: dict) -> bool:
    """True si la película de la fila *r* ya se puede conseguir fuera del
    cine (streaming/alquiler/compra/DVD) según sus watch providers de TMDB
    -- cualquier categoría (flatrate/rent/buy/free/ads) con al menos un
    proveedor cuenta. False si el mapa está vacío (solo en cines) o no se
    consultó todavía (sin datos). Es la base del interruptor "Solo
    disponibles en plataformas"."""
    return bool(r.get("watch"))


def apply_watch_availability_filter(rows: list, only_available: bool) -> list:
    """Recorta *rows* a solo las películas que ya se pueden conseguir
    fuera del cine si only_available es True; si es False devuelve todas
    (para ver también las que solo están en cines, marcadas como "Solo en
    cines"). Sin datos de providers (watch vacío) la fila se considera no
    disponible, igual que watch_available."""
    if not only_available:
        return rows
    return [r for r in rows if watch_available(r)]


def format_watch_display(r: dict, max_names: int = 3) -> str:
    """Texto corto de la columna "Disponible en" de una fila de Películas:
    los nombres de los proveedores de streaming (flatrate) primero, y si no
    hay ninguno pero sí alquiler/compra, "Alquiler/compra"; "Solo en cines"
    cuando no hay nada. Los nombres de más se resumen con "y N más" -- la
    columna es estrecha y TMDB devuelve muchos proveedores por película."""
    watch = r.get("watch") or {}
    flatrate = watch.get("flatrate") or []
    if flatrate:
        shown = flatrate[:max_names]
        extra = len(flatrate) - len(shown)
        text = ", ".join(shown)
        return f"{text} y {extra} más" if extra > 0 else text
    if any((watch.get(k) or []) for k in ("rent", "buy", "free", "ads")):
        return "Alquiler/compra"
    return "Solo en cines"


def filter_by_text(rows: list, query: str) -> list:
    """Filtra *rows* por coincidencia de texto en el título (y su año),
    sin distinguir mayúsculas. Query vacío devuelve todo."""
    q = (query or "").strip().lower()
    if not q:
        return rows
    return [r for r in rows
            if q in r.get("title", "").lower()
            or q in str(r.get("year", ""))]


# "Anime" es un alias de usuario de "Animación" (el género TMDB que agrupa
# tanto anime japonés como animación occidental) -- TMDB no distingue anime
# como género propio, así que el filtro por "Anime" selecciona las filas cuyo
# género es "Animación". Sin entrada (o "Todos") el filtro no hace nada.
GENRE_ANIME_ALIASES = ("animación", "animation")


def row_matches_genre(r: dict, genre: str) -> bool:
    """True si la fila *r* pertenece al género TMDB *genre* (nombre en el
    idioma configurado). La fila debe traer "genres" (lista de nombres,
    como build_movie_rows/la caché la dejan). "Anime" es un alias de
    "Animación" (ver GENRE_ANIME_ALIASES). Cualquier valor vacío o no
    reconocido devuelve True (sin filtrar)."""
    g = (genre or "").strip().lower()
    if not g or g in ("todos", "todo", "all", "todos los géneros"):
        return True
    row_genres = {x.lower() for x in (r.get("genres") or [])}
    if not row_genres:
        # Sin géneros conocidos no puede pertenecer a un género CONCRETO:
        # un filtro por género (p.ej. "Terror") no debe mostrar obras sin
        # género (p.ej. "Happu Ki Ultan Paltan", sin genre_ids en TMDB)
        # mezcladas con las de verdad del género. Solo "Todos" las muestra.
        return False
    if g in row_genres:
        return True
    if g == "anime" and any(a in row_genres for a in GENRE_ANIME_ALIASES):
        return True
    return False


def apply_genre_filter(rows: list, genre: str) -> list:
    """Recorta *rows* a las de un género TMDB (ver row_matches_genre). Valor
    vacío/"Todos" devuelve todo; filas sin géneros conocidos se ocultan
    cuando hay un género concreto elegido (no pertenecen a ninguno)."""
    if not (genre or "").strip() or (genre or "").strip().lower() in ("todos", "todo", "all"):
        return rows
    return [r for r in rows if row_matches_genre(r, genre)]


# Idiomas de origen (original_language, ISO 639-1) y países (origin_country,
# ISO 3166-1) de Asia ORIENTAL y del SUR que el usuario NO quiere ver en
# "Recomendado" (interruptor "Ocultar asiáticas"): China/Japón/Corea (todo
# el cine y drama asiático que llena las listas de tendencias/populares),
# India/Bollywood, Tailandia, Taiwán, Hong Kong, Indonesia, Filipinas,
# Vietnam... El origen se deduce del idioma original (lo más fiable para
# películas, que TMDB no lista por país en las listas de tendencias) y del
# país de origen (solo las series lo traen). Un código vacío/desconocido
# no excluye nada: el filtro solo descarta lo que se sabe asiático.
_ASIAN_LANGS = {
    # China y Taiwán (zh y sus variantes regionales que TMDB lista aparte)
    "zh", "yue", "wuu", "nan", "hak",
    # Japón y Corea
    "ja", "ko",
    # India y sur de Asia (Bollywood y cine regional)
    "hi", "ta", "te", "ml", "kn", "bn", "pa", "ur", "gu", "mr", "or", "as",
    "si", "ne", "sd", "dv",
    # Sudeste asiático
    "th", "vi", "id", "ms", "tl", "fil", "km", "my", "lo",
}
_ASIAN_COUNTRIES = {
    # Asia oriental
    "CN", "HK", "MO", "TW", "JP", "KR", "KP", "MN",
    # Sur de Asia
    "IN", "PK", "BD", "LK", "NP", "BT", "MV",
    # Sudeste asiático
    "TH", "VN", "ID", "MY", "PH", "SG", "MM", "KH", "LA", "BN", "TL",
}


def is_asian_origin(r: dict) -> bool:
    """True si la fila *r* es de Asia oriental o del sur, según su
    original_language (ISO 639-1) u origin_country (ISO 3166-1, solo las
    series lo traen). Ver _ASIAN_LANGS/_ASIAN_COUNTRIES."""
    lang = (r.get("original_language") or "").strip().lower()
    if lang in _ASIAN_LANGS:
        return True
    countries = r.get("origin_country") or []
    if any(c in _ASIAN_COUNTRIES for c in countries):
        return True
    return False


def apply_origin_filter(rows: list, hide_asian: bool) -> list:
    """Recorta *rows* a las que NO son de Asia oriental ni del sur si
    hide_asian es True (interruptor "Ocultar asiáticas"); si es False
    devuelve todas. Las obras sin dato de origen nunca se descartan."""
    if not hide_asian:
        return rows
    return [r for r in rows if not is_asian_origin(r)]


def sort_movie_rows(rows: list, key: str, asc: bool) -> list:
    """Ordena *rows* para la tabla de Películas.

    key: "title" (alfabético), "year" (año), "popularity", "vote_average",
    "list" (por etiqueta de lista). Cualquier otra clave cae al título.
    asc: True ascendente, False descendente. Orden estable (las filas con
    el mismo valor mantienen su orden original).
    """
    def _sort_key(r):
        if key == "popularity":
            return (r.get("popularity") or 0)
        if key == "vote_average":
            return (r.get("vote_average") or 0)
        if key == "year":
            return (r.get("year") or "")
        if key == "list":
            return MOVIE_LIST_LABELS.get(r.get("list"), "")
        return (r.get("title") or "").lower()
    return sorted(rows, key=_sort_key, reverse=not asc)


def _reset_cache_for_tests() -> None:
    pass