"""Filas de Recomendado estilo web (cards + líneas por categoría).

Espejo de ROWS en solicitudes-web/app.js: mismas categorías, mismos
endpoints/parámetros de TMDB y misma regla de relleno (si la primera página
se queda corta con los filtros, se piden más páginas hasta el tope, máximo
5). Puro (sin Qt): la pestaña Qt (gui_qt/movies/) lo consume y pinta cada
fila con el delegate de cards.

Cada fila: {"id", "title", "path", "params", "numbered", "no_avail",
"hide_if_unavail"} con el mismo significado que en la web:
- numbered: Top 10 (puesto 1..10 entre lo mostrado).
- no_avail: no se le aplica el filtro de disponibilidad (Próximamente).
- hide_if_unavail: con "Disponibles en plataformas" la fila entera
  desaparece (nada de lo próximo está en plataformas todavía).
"""

from datetime import date

from core.api_client import TMDB_IMAGE

# Series: fuera talk shows, noticias y realities en todo (web: TV_CLEAN).
TV_CLEAN = {"without_genres": "10767,10763,10764"}

PLATFORMS = [
    ("Netflix", "8"), ("Prime Video", "119"), ("Disney+", "337"),
    ("Max", "1899"), ("Movistar+", "2241|149"), ("Apple TV", "350"),
]

POP_DESC = "popularity.desc"
VOTE_DESC = "vote_average.desc"


def _platform_rows(kind: str) -> list:
    rows = []
    for name, ids in PLATFORMS:
        params = {"watch_region": "ES", "with_watch_providers": ids, "sort_by": POP_DESC}
        if kind == "tv":
            params.update(TV_CLEAN)
        rows.append({"id": f"plat-{name}", "title": f"Popular en {name}",
                     "path": f"/discover/{kind}", "params": params,
                     "numbered": False, "no_avail": False, "hide_if_unavail": False})
    return rows


def _genre_row(kind: str, rid: str, title: str, genres: str, extra: dict | None = None) -> dict:
    params = {"with_genres": genres, "sort_by": POP_DESC, "vote_count.gte": 100}
    if kind == "tv":
        params.update(TV_CLEAN)
    params.update(extra or {})
    return {"id": rid, "title": title, "path": f"/discover/{kind}", "params": params,
            "numbered": False, "no_avail": False, "hide_if_unavail": False}


def rows_for(kind: str) -> list:
    """Definiciones de fila para kind ("movie"/"tv"), con el año actual
    resuelto (los Top 10 son del año en curso y del anterior)."""
    year = date.today().year
    if kind == "movie":
        rows = [
            {"id": "trend", "title": "En tendencia esta semana", "path": "/trending/movie/week",
             "params": {}, "numbered": False, "no_avail": False, "hide_if_unavail": False},
            {"id": "top", "title": f"Top 10 películas de {year}", "path": "/discover/movie",
             "params": {"primary_release_year": year, "sort_by": POP_DESC, "vote_count.gte": 50},
             "numbered": True, "no_avail": False, "hide_if_unavail": False},
            {"id": "top-prev", "title": f"Top 10 películas de {year - 1}", "path": "/discover/movie",
             "params": {"primary_release_year": year - 1, "sort_by": POP_DESC, "vote_count.gte": 100},
             "numbered": True, "no_avail": False, "hide_if_unavail": False},
            {"id": "cines", "title": "En cines", "path": "/movie/now_playing",
             "params": {"region": "ES"}, "numbered": False, "no_avail": False, "hide_if_unavail": False},
            {"id": "pronto", "title": "Próximamente", "path": "/movie/upcoming",
             "params": {"region": "ES"}, "numbered": False, "no_avail": True, "hide_if_unavail": True},
            {"id": "best", "title": "Mejor valoradas de siempre", "path": "/movie/top_rated",
             "params": {}, "numbered": False, "no_avail": False, "hide_if_unavail": False},
            *_platform_rows("movie"),
            _genre_row("movie", "anim", "Mejores de animación", "16",
                       {"sort_by": VOTE_DESC, "vote_count.gte": 1000}),
            _genre_row("movie", "anime", "Anime", "16", {"with_original_language": "ja"}),
            _genre_row("movie", "comedia", "Comedia", "35"),
            _genre_row("movie", "terror", "Terror", "27"),
            _genre_row("movie", "scifi", "Ciencia ficción", "878"),
            _genre_row("movie", "accion", "Acción", "28"),
            _genre_row("movie", "docu", "Documentales", "99"),
        ]
    else:
        tv_top = {"sort_by": POP_DESC}
        tv_top.update(TV_CLEAN)
        rows = [
            {"id": "trend", "title": "En tendencia esta semana", "path": "/trending/tv/week",
             "params": {}, "numbered": False, "no_avail": False, "hide_if_unavail": False},
            {"id": "top", "title": f"Top 10 series de {year}", "path": "/discover/tv",
             "params": {**tv_top, "first_air_date_year": year, "vote_count.gte": 20},
             "numbered": True, "no_avail": False, "hide_if_unavail": False},
            {"id": "top-prev", "title": f"Top 10 series de {year - 1}", "path": "/discover/tv",
             "params": {**tv_top, "first_air_date_year": year - 1, "vote_count.gte": 50},
             "numbered": True, "no_avail": False, "hide_if_unavail": False},
            {"id": "emision", "title": "En emisión", "path": "/tv/on_the_air",
             "params": {}, "numbered": False, "no_avail": False, "hide_if_unavail": False},
            {"id": "best", "title": "Mejor valoradas de siempre", "path": "/tv/top_rated",
             "params": {}, "numbered": False, "no_avail": False, "hide_if_unavail": False},
            *_platform_rows("tv"),
            _genre_row("tv", "anim", "Animación", "16"),
            _genre_row("tv", "anime", "Anime", "16", {"with_original_language": "ja"}),
            _genre_row("tv", "comedia", "Comedia", "35"),
            _genre_row("tv", "misterio", "Misterio", "9648"),
            _genre_row("tv", "scifi", "Ciencia ficción y fantasía", "10765"),
            _genre_row("tv", "accion", "Acción y aventura", "10759"),
            _genre_row("tv", "docu", "Documentales", "99"),
            {"id": "mini", "title": "Miniseries", "path": "/discover/tv",
             "params": {**tv_top, "with_type": 2, "vote_count.gte": 100},
             "numbered": False, "no_avail": False, "hide_if_unavail": False},
        ]
    return rows


def row_limit(rowdef: dict) -> int:
    """Ítems por fila: 10 en los Top numerados, 20 en el resto (web)."""
    return 10 if rowdef.get("numbered") else 20


def build_params(rowdef: dict, kind: str, genre_ids=(), year_min: int | None = None) -> dict:
    """Parámetros efectivos de una fila: los suyos más el género y la
    ventana de años del usuario, EMPUJADOS a la consulta cuando el
    endpoint es /discover (TMDB filtra en servidor con with_genres en AND
    y fecha.gte). Sin esto, con género=Comedia la fila de Misterio traía
    20 títulos para quedarse con 1 tras filtrar en cliente; ahora TMDB
    devuelve directamente comedias de misterio y la fila se llena. En
    /trending, /top_rated y demás no hay esos parámetros: se devuelve lo
    de la fila tal cual y filtra el prefilter en cliente."""
    params = dict(rowdef.get("params") or {})
    if not rowdef.get("path", "").startswith("/discover/"):
        return params
    genre_ids = sorted({int(g) for g in (genre_ids or []) if str(g).isdigit()})
    if genre_ids:
        have = [g.strip() for g in str(params.get("with_genres", "")).split(",") if g.strip()]
        params["with_genres"] = ",".join(sorted(set(have) | {str(g) for g in genre_ids}))
    if year_min is not None and "primary_release_year" not in params \
            and "first_air_date_year" not in params:
        key = "primary_release_date.gte" if kind == "movie" else "first_air_date.gte"
        params[key] = f"{year_min}-01-01"
    return params


def normalize_item(r: dict, kind: str) -> dict:
    """Ítem TMDB crudo -> dict de card (mismos campos que pinta la web:
    título, año, nota, póster). Conserva genre_ids/original_language/
    origin_country para los filtros de la app (género, ocultar asiáticas)."""
    title = (r.get("title") or r.get("name") or "").strip()
    if kind == "tv":
        title = (r.get("title") or r.get("name") or r.get("original_name") or "").strip()
    release = r.get("release_date") or r.get("first_air_date") or ""
    poster = r.get("poster_path") or ""
    try:
        vote = float(r.get("vote_average") or 0)
    except (TypeError, ValueError):
        vote = 0.0
    try:
        tmdb_id = int(r.get("id"))
    except (TypeError, ValueError):
        tmdb_id = 0
    return {"tmdb_id": tmdb_id, "media_type": kind, "title": title,
            "year": (release or "")[:4], "vote": vote,
            "poster_url": f"{TMDB_IMAGE}{poster}" if poster else None,
            "overview": r.get("overview") or "",
            "genre_ids": list(r.get("genre_ids") or []),
            "original_language": r.get("original_language") or "",
            "origin_country": list(r.get("origin_country") or [])}


def fetch_row(client, kind: str, rowdef: dict, limit: int | None = None,
              max_pages: int = 5, prefilter=None, params: dict | None = None) -> list:
    """Ítems normalizados de una fila, rellenando con más páginas hasta el
    tope (máximo *max_pages*, sin repetir). *prefilter* (opcional) recorta
    cada página ANTES de acumular; *params* sustituye a los de la fila
    (ver build_params: género/años empujados a la consulta). En serie
    para no castigar el límite de TMDB (el _get del cliente cachea)."""
    want = limit if limit is not None else row_limit(rowdef)
    query = params if params is not None else (rowdef.get("params") or {})
    items, seen, pages, page = [], set(), 1, 1
    while len(items) < want and page <= min(pages, max_pages):
        fetched, pages = client.list_endpoint(rowdef["path"], kind, page=page, params=query)
        batch = [normalize_item(r, kind) for r in fetched
                 if r.get("id") is not None and (r.get("media_type") or kind, r.get("id")) not in seen]
        if prefilter is not None:
            batch = prefilter(batch)
        for it in batch:
            seen.add((it.get("media_type"), it.get("tmdb_id")))
            items.append(it)
        page += 1
    items = items[:want]
    if rowdef.get("numbered"):
        for i, it in enumerate(items):
            it["rank"] = i + 1
    return items


def apply_filters(items: list, owned_ids: set | None = None,
                  hide_owned: bool = False, hide_asian: bool = False,
                  genre_ids: set | None = None, year_min: int | None = None,
                  text: str = "") -> list:
    """Filtros de la app sobre los ítems de una fila (los dos primeros son
    los interruptores de la web: ocultar lo que ya está; el resto, los de
    la app: asiáticas, género, años y texto del buscador)."""
    from core.missing_movies import is_asian_origin
    owned_ids = owned_ids or set()
    genre_ids = genre_ids or set()
    needle = (text or "").strip().lower()
    out = []
    for it in items:
        if hide_owned and (it.get("media_type"), it.get("tmdb_id")) in owned_ids:
            continue
        if hide_asian and is_asian_origin({"original_language": it.get("original_language"),
                                           "origin_country": it.get("origin_country")}):
            continue
        if genre_ids and not (set(it.get("genre_ids") or []) & genre_ids):
            continue
        if year_min is not None:
            try:
                y = int((it.get("year") or "")[:4])
            except (TypeError, ValueError):
                y = 0
            if y and y < year_min:
                continue
        if needle and needle not in (it.get("title") or "").lower():
            continue
        out.append(it)
    return out


def _es_available(results, region: str) -> bool | None:
    """¿Hay alguna opción (plataforma/alquiler/compra/gratis/anuncios) en
    *region* dentro de un mapa crudo por país? True/False, o None sin dato
    (respuesta sin esa región: no bloquear por un fallo, sin dato se deja,
    como la web)."""
    if not isinstance(results, dict) or region not in results:
        return None
    entry = results[region] or {}
    if not isinstance(entry, dict):
        return None
    return bool(entry.get("flatrate") or entry.get("rent") or entry.get("buy")
                or entry.get("free") or entry.get("ads"))


def tv_available(providers: dict, region: str = "ES") -> bool | None:
    """¿La serie está en alguna plataforma de *region*? True/False, o None
    sin dato. *providers* es la respuesta cruda de /tv/{id}/watch/providers."""
    try:
        results = (providers or {}).get("results")
    except AttributeError:
        return None
    return _es_available(results, region)


def movie_providers_available(results: dict | None, region: str = "ES") -> bool | None:
    """Igual para películas, con el mapa crudo de
    TMDBClient.movie_watch_providers_raw (lanza en error de red: el que
    llama convierte la excepción en None)."""
    if results is None:
        return None
    return _es_available(results, region)


def filter_available(client, kind: str, items: list) -> list:
    """Quita de *items* lo que no está en plataformas ES (solo con el
    interruptor "Disponibles en plataformas" activo). Sin dato se deja
    (fail-open, como la web); error de red por ítem también se deja. Las
    series usan su endpoint propio (mejor que la tabla anterior, que las
    ocultaba todas al no traer dato de providers)."""
    kept = []
    for it in items:
        try:
            if kind == "movie":
                avail = movie_providers_available(client.movie_watch_providers_raw(it["tmdb_id"]))
            else:
                avail = tv_available(client.get_watch_providers(it["tmdb_id"]))
        except Exception:
            avail = None
        if avail is False:
            continue
        kept.append(it)
    return kept


def movie_available(watch: dict) -> bool | None:
    """Igual para películas, con el dict ya resumido de
    TMDBClient.get_movie_watch_providers. {} = solo en cines (False);
    None = sin dato (se deja)."""
    if watch is None:
        return None
    return bool(watch.get("flatrate") or watch.get("rent") or watch.get("buy")
                or watch.get("free") or watch.get("ads"))
