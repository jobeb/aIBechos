"""
TMDB API Client — busca series, películas y anime para obtener metadata correcta.
"""

import re
import threading
import time
from collections import deque
import requests
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w300"


def top_cast_names(credits: dict, limit: int = 6) -> list:
    """Nombres del reparto principal a partir de la respuesta de
    /{tv,movie}/{id}/credits, listos para mostrar: "Actor (Personaje)", o
    solo el actor si TMDB no trae personaje.

    Se ordena por el campo "order" de TMDB (0 = cabeza de cartel) en vez de
    fiarse del orden de llegada: la API lo respeta casi siempre, pero no lo
    garantiza, y con una serie de reparto largo la diferencia es entre
    enseñar a los protagonistas o a tres secundarios cualesquiera. Las
    entradas sin "order" van al final, no a la cabeza (de ahí el infinito).

    Función aparte del cliente (y no un método) para poder probarla sin
    tocar la red -- ver tests/test_top_cast_names.py."""
    cast = (credits or {}).get("cast") or []
    ordered = sorted(
        (c for c in cast if isinstance(c, dict) and (c.get("name") or "").strip()),
        key=lambda c: c.get("order") if isinstance(c.get("order"), int) else float("inf"))
    names = []
    for person in ordered[:max(0, limit)]:
        name = person.get("name", "").strip()
        character = (person.get("character") or "").strip()
        names.append(f"{name} ({character})" if character else name)
    return names


@dataclass
class MediaInfo:
    # Para libros/cómics (media_type="libro") esto aloja el id de Google
    # Books o ComicVine (core/book_client.py, core/comicvine_client.py) --
    # un string, no un int de TMDB. Reutilización deliberada: evita un
    # campo nuevo solo para un tercer origen de datos, y el resto del
    # código que lo toca (comparaciones de igualdad ya filtradas por
    # media_type, serialización a JSON) no le importa el tipo real.
    tmdb_id: int
    media_type: str          # "tv" | "movie" | "libro"
    title: str               # nombre limpio
    original_title: str
    year: str
    poster_url: Optional[str] = None
    # Para episodios de TV
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None
    # Extras
    overview: Optional[str] = None
    genres: list = field(default_factory=list)
    genre_ids: list = field(default_factory=list)   # ids crudos de TMDB, para clasificar por categoría


class TMDBClient:
    # TMDB limita a ~40 peticiones/10s por IP -- este margen (35) es
    # compartido por TODOS los usos de este cliente (búsquedas manuales,
    # subida, y el escaneo de episodios que faltan, que puede encadenar
    # cientos de llamadas seguidas en una biblioteca grande) para no
    # dispararlo nunca desde dentro de la propia app.
    _MAX_REQUESTS_PER_WINDOW = 35
    _WINDOW_SECONDS = 10.0
    _MAX_429_RETRIES = 3

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.params = {"api_key": api_key, "language": "es-ES"}
        self._cache: dict = {}
        self._cache_lock = threading.Lock()
        self._request_times: deque = deque()
        self._rate_lock = threading.Lock()

    def clear_cache(self):
        with self._cache_lock:
            self._cache.clear()

    def set_api_key(self, key: str):
        self.api_key = key
        self.session.params = {"api_key": key, "language": self.session.params.get("language", "es-ES")}
        self.clear_cache()

    def set_language(self, lang: str):
        self.session.params["language"] = lang
        self.clear_cache()

    def _throttle(self):
        """Bloquea lo justo para mantenerse bajo _MAX_REQUESTS_PER_WINDOW
        peticiones cada _WINDOW_SECONDS, compartido entre todos los hilos
        que usen esta misma instancia."""
        while True:
            with self._rate_lock:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] > self._WINDOW_SECONDS:
                    self._request_times.popleft()
                if len(self._request_times) < self._MAX_REQUESTS_PER_WINDOW:
                    self._request_times.append(now)
                    return
                wait = self._WINDOW_SECONDS - (now - self._request_times[0]) + 0.05
            time.sleep(wait)

    def _get(self, endpoint: str, **params) -> dict:
        lang = self.session.params.get("language", "es-ES")
        cache_key = (endpoint, lang, tuple(sorted(params.items())))
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        for attempt in range(self._MAX_429_RETRIES + 1):
            self._throttle()
            try:
                r = self.session.get(f"{TMDB_BASE}{endpoint}", params=params, timeout=10)
                if r.status_code == 429:
                    if attempt == self._MAX_429_RETRIES:
                        raise RuntimeError(
                            "TMDB: límite de peticiones superado, inténtalo de nuevo en unos segundos.")
                    retry_after = float(r.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(retry_after)
                    continue
                r.raise_for_status()
                result = r.json()
                break
            except requests.exceptions.ConnectionError:
                raise ConnectionError("Sin conexión a internet.")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 401:
                    raise ValueError("API Key de TMDB inválida.")
                raise
        with self._cache_lock:
            self._cache[cache_key] = result
        return result

    # detect_episode() devuelve "tv"/"anime"/"movie"; TMDB solo distingue
    # "tv"/"movie" -- el anime es una serie más para su catálogo.
    _TMDB_TYPE = {"tv": "tv", "anime": "tv", "movie": "movie"}

    def search_multi(self, query: str, prefer_type: str = "") -> list:
        """Resultados de /search/multi ordenados por popularidad.

        *prefer_type* (el media_type que dedujo detect_episode del propio
        nombre del archivo) sube al principio los resultados de ESE tipo,
        dejando la popularidad solo para desempatar dentro de cada grupo.
        Sin esto mandaba la popularidad a secas, y un título que existe
        como película famosa Y como serie se resolvía siempre a favor de
        la película: caso real, "Amadeus.1x05.Episodio.5...mkv" acababa
        renombrado como "Amadeus (1984).mkv" (la película de Forman, 8
        Óscar) pese a que el "1x05" del nombre no deja ninguna duda de que
        es un episodio. El tipo detectado ya se conocía, solo que la
        búsqueda lo tiraba a la basura.

        No FILTRA, solo reordena: si no hay ningún resultado del tipo
        preferido (una serie que TMDB solo tiene como película, o una
        detección equivocada), los del otro tipo siguen ahí detrás en vez
        de dejar al usuario sin nada que elegir."""
        data = self._get("/search/multi", query=query)
        results = [r for r in data.get("results", []) if r.get("media_type") in ("tv", "movie")]
        preferred = self._TMDB_TYPE.get(prefer_type, "")
        return sorted(
            results,
            key=lambda r: (bool(preferred) and r.get("media_type") == preferred,
                           r.get("popularity", 0)),
            reverse=True)

    def search_tv(self, query: str) -> list:
        data = self._get("/search/tv", query=query)
        return data.get("results", [])

    def search_movie(self, query: str) -> list:
        data = self._get("/search/movie", query=query)
        return data.get("results", [])

    def get_episode_info(self, tv_id: int, season: int, episode: int) -> dict:
        return self._get(f"/tv/{tv_id}/season/{season}/episode/{episode}")

    def get_watch_providers(self, tv_id: int) -> dict:
        """Dónde se puede ver la serie, por región -- usado por el
        interruptor "Ocultar sin doblaje ES" (ver core/missing_episodes.py)
        para descartar de entrada series que ni siquiera están disponibles
        en España, antes de gastar una llamada por episodio."""
        return self._get(f"/tv/{tv_id}/watch/providers")

    def get_episode_info_es(self, tv_id: int, season: int, episode: int) -> dict:
        """Igual que get_episode_info(), pero siempre en es-ES explícito --
        independiente del idioma configurado globalmente (self.session
        params), porque esta llamada existe justo para comprobar si HAY
        traducción al castellano, no para mostrar la ficha en el idioma que
        el usuario tenga elegido."""
        return self._get(f"/tv/{tv_id}/season/{season}/episode/{episode}", language="es-ES")

    def get_episode_translations(self, tv_id: int, season: int, episode: int) -> dict:
        """Idiomas con traducción REAL del episodio
        (/tv/{id}/season/{s}/episode/{e}/translations) -- usado por el
        interruptor "Ocultar sin doblaje ES" (ver
        core/missing_episodes.py::episode_has_spanish_translation) para
        distinguir "sin traducir al español" de "traducido": el endpoint de
        episodio con language=es-ES devuelve el texto en INGLÉS cuando no
        hay traducción (fallback de TMDB), así que mirar solo si
        overview/name vienen vacíos da doblado-por-defecto a episodios que
        ni siquiera están traducidos (real: El Arca 3x01). Esta lista no
        tiene fallback -- si no hay entrada "es", no hay traducción."""
        return self._get(f"/tv/{tv_id}/season/{season}/episode/{episode}/translations")

    def get_season_episodes(self, tv_id: int, season: int) -> list:
        """Lista de episodios YA EMITIDOS de una temporada (según air_date)
        -- usado por el detector de huecos (core/missing_episodes.py) para
        saber qué episodios deberían existir YA, antes de compararlo con lo
        que hay de verdad en Plex/Jellyfin. Los episodios sin fecha de
        emisión todavía, o con fecha futura, se excluyen -- TMDB a veces ya
        lista episodios de una temporada anunciada/en emisión que aún no
        han salido, y eso no cuenta como "que falta"."""
        data = self._get(f"/tv/{tv_id}/season/{season}")
        today = date.today().isoformat()
        episodes = []
        for e in data.get("episodes", []):
            air_date = e.get("air_date")
            if not air_date or air_date > today:
                continue
            episodes.append({"episode_number": e.get("episode_number"), "name": e.get("name", ""),
                              "air_date": air_date})
        return episodes

    def get_tv_details(self, tv_id: int) -> dict:
        return self._get(f"/tv/{tv_id}")

    def get_movie_details(self, movie_id: int) -> dict:
        return self._get(f"/movie/{movie_id}")

    def get_credits(self, media_type: str, tmdb_id: int) -> dict:
        """Ficha de reparto/equipo de TMDB. media_type acepta lo que devuelve
        detect_episode ("tv"/"anime"/"movie"), no solo lo que entiende TMDB --
        ver _TMDB_TYPE."""
        kind = self._TMDB_TYPE.get(media_type, "movie")
        return self._get(f"/{kind}/{tmdb_id}/credits")

    def get_top_cast(self, media_type: str, tmdb_id: int, limit: int = 6) -> list:
        """Los *limit* intérpretes principales, ya formateados para mostrar
        (ver top_cast_names). Lista vacía si TMDB no tiene reparto."""
        return top_cast_names(self.get_credits(media_type, tmdb_id), limit)

    # Listas de películas que alimentan la pestaña "Películas" (recomendar
    # lo que no está en el servidor, ver gui/app.py::_scan_missing_movies).
    # Cada endpoint devuelve el mismo formato de resultado que /search/movie
    # (id, title, release_date, poster_path, vote_average, popularity,
    # overview, genre_ids) -- solo cambia la fuente; /trending ya añade
    # "media_type": "movie" a cada resultado, los otros tres no, así que se
    # normaliza aquí para que el escaneo no tenga que distinguir.
    def _movie_list(self, endpoint: str, page: int = 1) -> list:
        data = self._get(endpoint, page=page)
        results = []
        for r in data.get("results", []) or []:
            item = dict(r)
            item["media_type"] = "movie"
            results.append(item)
        return results

    def get_trending_movies(self, page: int = 1, window: str = "week") -> list:
        return self._movie_list(f"/trending/movie/{window}", page=page)

    def get_popular_movies(self, page: int = 1) -> list:
        return self._movie_list("/movie/popular", page=page)

    def get_upcoming_movies(self, page: int = 1) -> list:
        return self._movie_list("/movie/upcoming", page=page)

    def get_now_playing_movies(self, page: int = 1) -> list:
        return self._movie_list("/movie/now_playing", page=page)

    # Listas de series que alimentan la pestaña "Recomendado" (ver
    # core/missing_movies.py). Igual que _movie_list pero normalizando los
    # campos de las series (name/original_name/first_air_date en vez de
    # title/original_title/release_date) y con media_type="tv", para que
    # build_movie_rows trate ambos tipos con el mismo código.
    def _tv_list(self, endpoint: str, page: int = 1) -> list:
        data = self._get(endpoint, page=page)
        results = []
        for r in data.get("results", []) or []:
            item = dict(r)
            item["media_type"] = "tv"
            item["title"] = r.get("name", r.get("original_name", ""))
            item["original_title"] = r.get("original_name", "")
            item["release_date"] = r.get("first_air_date", "")
            results.append(item)
        return results

    def get_trending_tv(self, page: int = 1, window: str = "week") -> list:
        return self._tv_list(f"/trending/tv/{window}", page=page)

    def get_popular_tv(self, page: int = 1) -> list:
        return self._tv_list("/tv/popular", page=page)

    def get_on_the_air_tv(self, page: int = 1) -> list:
        return self._tv_list("/tv/on_the_air", page=page)

    def get_movie_watch_providers(self, movie_id: int, region: str = "ES") -> dict:
        """Disponibilidad de una película fuera del cine para la región
        pedida -- devuelve {"flatrate": ["Netflix", ...], "rent": [...],
        "buy": [...], "free": [...], "ads": [...]} con los NOMBRES de los
        proveedores que TMDB registra para esa película en esa región, o {}
        si la película no aparece (solo en cines, o no distribuida allí).

        Alimenta el filtro "Solo disponibles en plataformas" de la pestaña
        Películas: una película con alguna de estas listas no vacía ya se
        puede conseguir (streaming/alquiler/compra/DVD), y la que devuelve
        {} no -- solo está en cines, no se puede bajar todavía.

        El endpoint /movie/{id}/watch/providers responde con un mapa por
        país; nos quedamos con la entrada de *region* (código ISO 3166-1
        alfa-2, p.ej. "ES") y de ella solo los nombres, que es lo único que
        la GUI necesita mostrar."""
        try:
            data = self._get(f"/movie/{movie_id}/watch/providers")
        except Exception:
            return {}
        region_data = (data.get("results") or {}).get(region) or {}
        out = {}
        for key in ("flatrate", "rent", "buy", "free", "ads"):
            names = [p.get("provider_name", "") for p in (region_data.get(key) or [])]
            names = [n for n in names if n]
            if names:
                out[key] = names
        return out

    def get_genres(self, media_type: str) -> list:
        """Lista de géneros TMDB [{"id": int, "name": str}, ...] en el idioma configurado."""
        endpoint = "/genre/tv/list" if media_type == "tv" else "/genre/movie/list"
        return self._get(endpoint).get("genres", [])

    def get_movie_certification(self, movie_id: int, region: str = "ES",
                                fallback_region: str = "US") -> str:
        """Clasificación por edad de una película (PG-13, 12, 18...) para la
        región pedida, o "" si TMDB no la registra.

        Viene de /movie/{id}/release_dates, que agrupa las certificaciones
        por país (ISO 3166-1). Se prefiere *region* (p.ej. "ES"); si no
        declara ninguna certificación se prueba *fallback_region* ("US") --
        las películas españolas suelen tener certificación US aunque la ES
        esté vacía, y un dato aproximado es mejor que "sin dato".

        Devuelve la primera certificación no vacía del primer release con
        alguna; si no hay nada, "". Alimenta la ficha de detalle de la
        pestaña Películas."""
        try:
            data = self._get(f"/movie/{movie_id}/release_dates")
        except Exception:
            return ""
        results = (data.get("results") or []) or []
        for country in (region, fallback_region):
            for entry in results:
                if entry.get("iso_3166_1") != country:
                    continue
                for rd in (entry.get("release_dates") or []):
                    cert = (rd.get("certification") or "").strip()
                    if cert:
                        return cert
        return ""

    def build_media_info(self, result: dict, season=None, episode=None):
        media_type = result.get("media_type", "tv")
        if media_type == "tv":
            title = result.get("name", result.get("original_name", ""))
            year = (result.get("first_air_date", "") or "")[:4]
        else:
            title = result.get("title", result.get("original_title", ""))
            year = (result.get("release_date", "") or "")[:4]

        poster_path = result.get("poster_path")
        poster_url = f"{TMDB_IMAGE}{poster_path}" if poster_path else None

        info = MediaInfo(
            tmdb_id=result.get("id", 0),
            media_type=media_type,
            title=title,
            original_title=result.get("original_name", result.get("original_title", title)),
            year=year,
            poster_url=poster_url,
            season=season,
            episode=episode,
            overview=result.get("overview", ""),
            genres=[],
            genre_ids=result.get("genre_ids", []) or [],
        )

        if media_type == "tv" and season is not None and episode is not None:
            try:
                ep = self.get_episode_info(info.tmdb_id, season, episode)
                info.episode_title = ep.get("name", "")
            except Exception:
                info.episode_title = ""

        return info

    def validate_key(self) -> bool:
        try:
            self._get("/configuration")
            return True
        except Exception:
            return False


# ── Patrones de detección ──────────────────────────────────────────────────────

EPISODE_PATTERNS = [
    # S01E01 -- con un segundo episodio opcional, de tres formas:
    #   1) "E" pegada: S07E21E22
    #   2) temporada+episodio repetida: S07E21-S07E22 / S07E21 S07E22
    #   3) rango compacto SIN indicador propio: S07E21-22 (visto de verdad:
    #      "Stargate Atlantis 1x01-02...", ver el patrón siguiente)
    # Las formas 1 y 2 tienen su propio indicador inequívoco (otra "E", o
    # la temporada repetida) y se aceptan sin más comprobación. La forma 3
    # NO tiene ningún indicador propio -- un número de 2-3 dígitos justo
    # después de un guion sería indistinguible de un año o una resolución
    # (p.ej. "1x05-720p") si se aceptara sin más -- por eso detect_episode
    # solo acepta el último grupo (el de esta forma) cuando el número es
    # CONSECUTIVO al primer episodio (episodio+1): un episodio doble real
    # siempre son dos números seguidos, un año o una resolución nunca lo
    # son "por casualidad" salvo coincidencia extrema. (?!\d) en cada grupo
    # para no comerse solo una parte de un número más largo (p.ej. "108"
    # de "1080p").
    (r"[Ss](\d{1,2})[Ee](\d{1,3})(?:[Ee](\d{1,3})(?!\d)|[\s\-]+[Ss]\d{1,2}[Ee](\d{1,3})(?!\d)|-(\d{1,3})(?!\d))?", "tv"),
    # 1x01 / 1X01 (algunos grupos de release usan la X en mayúscula) -- con
    # un segundo episodio opcional, mismas tres formas y mismo motivo que
    # el patrón de arriba (temporada repetida = de fiar directamente;
    # rango compacto "1x01-02" = solo si es consecutivo, ver detect_episode).
    # (?<!\d) antes del nº de temporada: un "1080x264"/"1080X264" de
    # resolución+códec (caso real: "La Odisea (The Asylum) (2026) Web Dl
    # 1080X264 E-AC3...mkv") NO debe partirse en "80x264" (temporada 80,
    # episodio 264) -- sin el lookbehind el regex encontraba el match en el
    # "80X264" que hay dentro de "1080X264" porque el escaneo empieza en
    # cualquier posición.
    (r"(?<!\d)(\d{1,2})[xX](\d{2,3})(?:[\s\-]+\d{1,2}[xX](\d{2,3})(?!\d)|-(\d{2,3})(?!\d))?", "tv"),
    # Episode.1078 / Episodio 5 / Episode07
    (r"[Ee]pisod[eo]s?[\s.\-]*(\d{1,4})", "anime"),
    # Ep.01 / Ep01
    (r"[Ee][Pp][\s.\-]*(\d{2,4})", "anime"),
    # Cap.01 / Capitulo 01
    (r"[Cc]ap(?:itulo)?[\s.\-]*(\d{1,3})", "anime"),
    # - 01 - / - 01 (al final del nombre, ya sin extensión -- visto de
    # verdad en release groups que nombran "Serie - 320.mp4" sin nada
    # después del número: exigir separador también a la derecha dejaba
    # esos episodios sin temporada/episodio reconocido)
    (r"[\s\-](\d{2,4})(?:[\s\-]|$)", "anime"),
]

# Términos que delatan "a partir de aquí ya no es el título, es basura
# técnica" (resolución, códec, audio, idioma, marcas de doblaje/sub...). Se
# usan de dos formas en _clean_title: para CORTAR el texto en el primer
# punto donde aparezca cualquiera de ellos (ver _find_junk_start) y, por si
# acaso queda alguno antes del corte, también para eliminarlos sueltos.
# Cortar en el primer indicio conocido es mucho más robusto que intentar
# enumerar cada crédito de subida posible ("Kowalski&Xusman", "Para
# Nocturniap2p"...) uno a uno: basta con UN término reconocido para saber
# que todo lo que viene después también es ruido, aunque el resto de la
# cola (nombres de subidor, grupo, etc.) no esté en ninguna lista.
JUNK_MARKERS = [
    r"\b(1080p|720p|480p|2160p|4K|HDR10?\+?|SDR|UHD|HD|SD|HQ|\d{1,2}-?[Bb]it)\b",
    r"\b(BluRay|Blu-Ray|BDRip|BDRemux|BRRemux|WEB-?DL|WEBRip|HDTV|DVDRip|DVDScr|HDRip|BRRip|CAM|TS|AMZN|NF|DSNP)\b",
    r"\b(x264|x265|HEVC|AVC|H\.264|H\.265|AV1)\b",
    r"\b(E?AC3|AAC|DTS|DD5\.1|DD\+|TrueHD|Atmos|FLAC|MP3)\b",
    r"\b(PROPER|REPACK|EXTENDED|UNRATED|THEATRICAL|REMUX)\b",
    # Idioma / doblaje / subtítulos
    r"\b(Castellano|Espa[ñn]ol|Latino|Ingl[eé]s|Franc[eé]s|Alem[aá]n|Italiano|Catal[aá]n|Gallego|Euskera|VOSE|VOSI|VO|Dual|Multi(?:idioma)?|Subtitulad[oa]s?|Subs?)\b",
    r"\b(Forzados?|Completos?|Temporada\s*Completa)\b",
]

JUNK_PATTERNS = JUNK_MARKERS + [
    r"\[(.*?)\]",
    r"\((.*?)\)",
    r"[.\-_]",   # puntos, guiones y guiones bajos → espacios
    r"\b(by|para)\s+\S+\s*$",   # crédito de subida al final ("... by/para nombre_uploader", una palabra)
]


def _learned_junk_pattern() -> list[str]:
    """Términos que la IA identificó como ruido en fallbacks anteriores (ver
    core/ai_title_fallback.py) — una vez aprendidos, se reconocen solos sin
    volver a llamar a la IA. Vacío si nunca se ha usado el fallback."""
    from core.learned_terms import load_learned_terms
    terms = load_learned_terms()
    if not terms:
        return []
    escaped = "|".join(re.escape(t) for t in terms)
    return [rf"\b({escaped})\b"]


def _effective_junk_markers(extra_junk_terms: Optional[list] = None) -> list:
    """JUNK_MARKERS + términos aprendidos de forma persistente + términos
    "candidatos" de esta llamada concreta (usados para probar si un
    resultado de IA ayuda antes de aprenderlo de verdad — ver
    core/ai_title_fallback.py)."""
    patterns = JUNK_MARKERS + _learned_junk_pattern()
    if extra_junk_terms:
        escaped = "|".join(re.escape(t) for t in extra_junk_terms if t and t.strip())
        if escaped:
            patterns = patterns + [rf"\b({escaped})\b"]
    return patterns


def _find_junk_start(text: str, extra_junk_terms: Optional[list] = None) -> int:
    """Posición del primer término de JUNK_MARKERS (+ aprendidos) en text, o
    len(text) si no hay ninguno. Si el término cae dentro de un [..]/(..) sin
    cerrar en ese punto, se usa la posición de apertura del corchete/paréntesis
    en su lugar — si no, el corte parte el grupo por la mitad y deja un "("/"["
    suelto (p.ej. "(Spanish.English.Subs)" con "Subs" reconocido como
    idioma cortaría en mitad de ese paréntesis en vez de antes de él)."""
    bracket_spans = [m.span() for m in re.finditer(r"\[[^\[\]]*\]|\([^()]*\)", text)]
    earliest = len(text)
    for pat in _effective_junk_markers(extra_junk_terms):
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            start = m.start()
            for bstart, bend in bracket_spans:
                if bstart <= start < bend:
                    start = bstart
                    break
            if start < earliest:
                earliest = start
    return earliest


def detect_episode(filename: str, extra_junk_terms: Optional[list] = None,
                    is_book: bool = False, is_comic: bool = False,
                    folder_hint: str = "",
                    series_pattern: Optional[str] = None) -> dict:
    """
    Extrae de un nombre de archivo: season, episode, título limpio, tipo.

    extra_junk_terms: términos "candidatos" adicionales a tratar como ruido
    en esta llamada concreta (sin persistir) — los usa el fallback de IA
    (core/ai_title_fallback.py) para probar si limpian el título antes de
    aprenderlos de verdad vía core/learned_terms.py.

folder_hint: nombre de la carpeta que contiene el archivo -- SOLO se usa
    (is_book/is_comic) cuando el propio nombre de archivo no deja ningún
    título aprovechable, p.ej. escaneos de manga/cómic numerados a secas
    ("01.cbr", "Capitulo 05.cbr") donde el nombre de la serie está puesto en
    la carpeta que los contiene, no en cada archivo. Se limpia con el mismo
    _clean_title que el nombre de archivo (quita créditos de escaneo, año,
    grupo...) y solo se usa como último recurso: si el archivo YA trae un
    título de verdad, ese gana siempre. Ignorado para vídeo (is_book=False)
    -- ahí el propio nombre de archivo casi siempre trae el título.

    series_pattern: Patrón de búsqueda personalizado para esta serie.
    Si se proporciona, se usará para generar una consulta de búsqueda más
    efectiva en TMDB en lugar del título detectado. El formato esperado es
    un patrón tipo "2x01" o "1x01" que se aplicará al título limpio.
    Ejemplo: para "That Time I Got Reincarnated as a Slime", un patrón
    "Slime #Épisode" generaría la búsqueda "Slime 2x01" en TMDB.

is_book: el llamador (gui/app.py, vía core.renamer.is_book_file) ya
    sabe por la extensión que esto es un libro/cómic, no un vídeo -- en
    ese caso se evita POR COMPLETO la cadena EPISODE_PATTERNS (pensada
    para nombres de episodio de TV/anime) y se devuelve media_type="libro"
    directamente. Necesario porque esos patrones son puro texto: un cómic
    como "One Piece - 1078.cbz" haría falso positivo con el patrón
    numérico de anime y se le asignaría un "episodio" que no tiene sentido
    para un libro.

    is_comic: subcaso de is_book -- a diferencia de un ebook de texto, un
    cómic SÍ suele traer un número de emisión fiable en el propio nombre
    de archivo, normalmente con el formato "#NN" (confirmado contra cómics
    reales: "Avatar - The Last Airbender - The Promise (2012) #01.cbr").
    Si NO hay "#" (visto de verdad: escaneos que numeran el número suelto,
    sin ningún símbolo -- "Avatar La Busqueda 01.cbr"), se prueba un
    segundo patrón: un número de 1-4 dígitos, con CERO O MÁS grupos entre
    corchetes/paréntesis opcionales detrás (créditos de escaneo/grupo, año...
    puede haber varios seguidos, p.ej. "World War Hulk 2 [por UltronXII]
    [CRG].cbr" -- dos grupos distintos tras el número) -- mismo criterio que
    ya usa EPISODE_PATTERNS para anime sin indicador propio (última entrada
    de la lista). Sin el "#" como ancla inequívoca, esto puede confundir un
    año suelto de 4 dígitos con el número de emisión (mismo riesgo ya
    aceptado para anime) -- pero sin él, CUALQUIER cómic numerado sin "#" se
    quedaba siempre en el episodio 1 por defecto (ver
    core/renamer.py::build_new_name, "episodio": info.episode or 1), bug
    real visto primero con una trilogía completa subida tres veces como
    "#1", y de nuevo con una colección (World War Hulk) donde CADA número
    de grapa se perdía porque el patrón solo toleraba UN grupo final entre
    corchetes, no varios seguidos. Si se encuentra cualquiera de los dos
    patrones, se extrae como "episode" -- igual que ya se hace con el
    episodio de una serie -- para poder renombrar con ese número real en
    vez de perderlo.

    "extra_episodes": lista de episodios adicionales cuando el archivo
    empaqueta más de uno (p.ej. "Serie 7x21-7x22 Título.avi", un doble
    episodio con su propia numeración repetida, ver EPISODE_PATTERNS) --
    vacía en el caso normal de un episodio por archivo. "season"/"episode"
    NO cambian de significado: siguen siendo el primer/principal episodio
    del archivo, así que el renombrado (que solo usa esos dos) no cambia.
    """
    stem = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename)  # quitar extensión

    if is_book:
        if is_comic:
            m = re.search(r"#\s*(\d{1,4})", stem)
            if not m:
                # "*" (no "?"): puede haber VARIOS grupos entre corchetes
                # seguidos tras el número (créditos de escaneo/grupo, p.ej.
                # "World War Hulk 2 [por UltronXII][CRG].cbr" -- dos grupos
                # distintos, uno con el nombre del escaneador y otro con el
                # grupo). Con "?" (como mucho uno) esto no encontraba nada
                # en ningún archivo con más de un grupo final, y el número
                # de grapa se perdía siempre (caía al 1 por defecto, ver
                # core/renamer.py::build_new_name) aunque hubiera un número
                # de verdad justo antes de esos corchetes. "(?:^|[\s\-_])"
                # (no solo "[\s\-_]"): un escaneo de manga puede venir sin
                # NADA de título propio, solo el número a secas ("01.cbz",
                # con el nombre real de la serie en la carpeta contenedora,
                # ver folder_hint) -- ahí no hay ningún separador antes del
                # número porque es lo único que hay en el nombre.
                m = re.search(r"(?:^|[\s\-_])(\d{1,4})(?:\s*[\[\(][^\]\)]*[\]\)])*\s*$", stem)
            if not m:
                # Último recurso: escaneos de webtoon/manga que ponen la
                # palabra "Capítulo"/"Chapter" delante del número, seguida
                # del TÍTULO PROPIO de ESE capítulo (no de la serie) -- p.ej.
                # "Capítulo 128 — Capítulo final de la segunda temporada.cbz".
                # El patrón de arriba exige que el número esté AL FINAL del
                # nombre -- aquí no lo está (le sigue un guion/raya y más
                # texto), así que no encontraba nada y el episodio se perdía
                # siempre (caía al 1 por defecto, ver core/renamer.py::
                # build_new_name), bug real visto con los capítulos
                # 128/129/193 de una colección de 266 subidos todos como
                # "#01". Se prueba SOLO si el patrón de arriba (número al
                # final) no encontró nada -- si el número SÍ está al final
                # (p.ej. "Capitulo 05.cbr", sin subtítulo detrás), ese
                # patrón ya lo captura bien y no hace falta llegar aquí. Todo
                # lo que va DESPUÉS del número (el subtítulo del capítulo) no
                # es el título de la serie, así que se descarta -- si no
                # queda nada útil ANTES tampoco (lo habitual), folder_hint
                # (ver más abajo) aporta el nombre real de la serie desde la
                # carpeta contenedora.
                m = re.search(r"\b(?:cap[ií]tulos?|chapters?|ep(?:isodios?)?)\.?\s*#?\s*(\d{1,4})(?:\.\d+)?",
                              stem, flags=re.IGNORECASE)
            if m:
                title = _clean_title(stem[:m.start()], extra_junk_terms)
                title = _apply_folder_hint(title, folder_hint, extra_junk_terms)
                return {
                    "title": title,
                    "season": None,
                    "episode": int(m.group(1)),
                    "media_type": "libro",
                    "raw_match": m.group(),
                    "extra_episodes": [],
                }
        title = _clean_title(stem, extra_junk_terms)
        title = _apply_folder_hint(title, folder_hint, extra_junk_terms)
        return {
            "title": title,
            "season": None,
            "episode": None,
            "media_type": "libro",
            "raw_match": None,
            "extra_episodes": [],
        }

    for pattern, media_type in EPISODE_PATTERNS:
        m = re.search(pattern, stem)
        if m:
            groups = m.groups()
            if media_type == "tv":
                season, episode = int(groups[0]), int(groups[1])
                # Grupos 3+ (si el patrón los tiene, ver EPISODE_PATTERNS):
                # variantes alternativas del mismo segundo episodio -- como
                # mucho una participa por match, las demás quedan en None.
                # La ÚLTIMA es siempre la forma de rango compacto sin
                # indicador propio ("1x01-02") -- solo de fiar si el
                # número es consecutivo al primer episodio (ver el
                # comentario largo en EPISODE_PATTERNS); las demás formas
                # (temporada repetida, "E" pegada) tienen su propio
                # indicador inequívoco y se aceptan tal cual.
                extra_groups = groups[2:]
                extra_episodes = []
                for i, g in enumerate(extra_groups):
                    if g is None:
                        continue
                    val = int(g)
                    if i == len(extra_groups) - 1 and val != episode + 1:
                        continue
                    extra_episodes.append(val)
            else:
                season, episode = 1, int(groups[0])
                extra_episodes = []

            title_raw = stem[:m.start()]
            title = _clean_title(title_raw, extra_junk_terms)

            return {
                "title": title,
                "season": season,
                "episode": episode,
                "media_type": media_type,
                "raw_match": m.group(),
                "extra_episodes": extra_episodes,
            }

    # Sin patrón → película
    title = _clean_title(stem, extra_junk_terms)
    # Quitar año del título si aparece al final
    title = re.sub(r"\s+\d{4}\s*$", "", title).strip()
    return {
        "title": title,
        "season": None,
        "episode": None,
        "media_type": "movie",
        "raw_match": None,
        "extra_episodes": [],
    }


# Palabras sueltas que un nombre de archivo de cómic/manga puede dejar como
# "título" tras quitarle el número de grapa/capítulo -- no identifican la
# serie, así que no sirven para buscar (p.ej. "Capitulo 05.cbr" limpia a
# "Capitulo", "Tomo 3.cbz" a "Tomo"). Ver _is_uninformative_book_title.
_GENERIC_CHAPTER_WORDS = {
    "cap", "capitulo", "capítulo", "chapter", "ch", "tomo", "vol", "volumen",
    "episodio", "parte", "issue", "numero", "número", "n",
}


def _is_uninformative_book_title(title: str) -> bool:
    """True si *title* (ya limpiado) no aporta nada para identificar la
    serie -- vacío, puramente numérico, o una de las palabras sueltas de
    _GENERIC_CHAPTER_WORDS con o sin el propio número de tomo/capítulo
    pegado detrás (p.ej. un ebook de texto "Tomo 3.epub" no pasa por la
    extracción de número de grapa de los cómics -- ese "3" se queda pegado
    al título tal cual, así que hay que reconocer "tomo3" igual que "tomo")."""
    if not title:
        return True
    normalized = re.sub(r"[^\w]", "", title).lower()
    if not normalized:
        return True
    if normalized.isdigit():
        return True
    return re.sub(r"\d+$", "", normalized) in _GENERIC_CHAPTER_WORDS


def _apply_folder_hint(title: str, folder_hint: str, extra_junk_terms: Optional[list] = None) -> str:
    """Sustituye *title* por *folder_hint* (limpiado igual que un nombre de
    archivo) cuando el propio archivo no dejó ningún título aprovechable --
    ver detect_episode(folder_hint=...). Si *title* ya es útil, o no hay
    folder_hint, o el propio folder_hint limpia a nada, se deja *title* tal
    cual."""
    if not folder_hint or not _is_uninformative_book_title(title):
        return title
    folder_title = _clean_title(folder_hint, extra_junk_terms)
    return folder_title if folder_title else title


def _clean_title(text: str, extra_junk_terms: Optional[list] = None) -> str:
    """Elimina basura técnica y formatea el título."""
    # Cortar en el primer término técnico reconocido: todo lo que venga
    # después (idiomas, créditos de subida, nombres de grupo/tracker...) se
    # descarta de una vez, lo esté o no en JUNK_PATTERNS explícitamente.
    text = text[:_find_junk_start(text, extra_junk_terms)]
    # JUNK_MARKERS ya está cubierto (+ aprendidos/candidatos) por el corte de
    # arriba; aquí solo hace falta el resto de JUNK_PATTERNS (corchetes,
    # paréntesis, separadores, crédito final) para limpiar lo que quedó
    # dentro del propio título.
    removal_patterns = _effective_junk_markers(extra_junk_terms) + JUNK_PATTERNS[len(JUNK_MARKERS):]
    for pat in removal_patterns:
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.title() if text else text
