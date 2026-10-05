"""
RTVE Play como señal de doblaje al castellano (solo metadatos, sin
cuenta) -- ver https://www.rtve.es/play/.

API JSON pública (documentada por la comunidad y usada por yt-dlp,
streamlink y el addon Kodi de RTVE -- ver
https://ulisesgascon.github.io/RTVE-API/), sin token ni login:

- Catálogo: `GET https://secure-api.rtve.es/api/programas.json`
  (`page.items[]` con `id`, `name`, `uri`, `htmlUrl`, `language`,
  `numSeasons`, `seasons[{orden, numEpisodes}]`). Sin búsqueda en
  servidor (`?q=`/`?search=`/`?startWithLetter=` se ignoran --
  verificado en vivo el 2026-10-05), así que la búsqueda es en dos
  tiempos (ver search_program): slug directo + índice local.
- Episodios completos: `GET .../programas/{id}/videos.json` con
  `type=39816` (filtra avances/fragmentos -- sin él vienen miles de
  clips con `episode=0`), `page.items[]` con `episode`,
  `temporadaOrden`, `language`, `languageOriginal`, `type`,
  `longTitle`.
- Ficha por vídeo: `GET https://www.rtve.es/api/videos/{id}.json`
  (mismos campos de idioma + `qualities[].language`).

Semántica de la señal (decisión del usuario: basta audio castellano
disponible aunque exista VO): vídeo con `language == "es"` (o
`qualities[].language` con `"es"`) -> True; programa existente pero
episodio con idioma conocido no-español -> False; sin programa o sin
episodio -> None (sin dato, no se supone nada). El corte
{temporada: último_doblado} solo cuenta tramos iniciales todo-True
(igual que core/server_audio.cutoff_from_audio). Nunca lanza:
[]/{}/None al fallar, quien llama sigue como si esta consulta no
existiera (mismo contrato que core/eldoblaje.py y
core/crunchyroll_client.py). Solo metadatos, nunca streams.
"""

import difflib
import json
import re
import threading
import time
import unicodedata
from collections import deque

import requests

from core.applog import get_logger

_log = get_logger("aIBechos.rtve", "ai_fallback.log")

_API = "https://secure-api.rtve.es/api"
_WEB = "https://www.rtve.es"
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0 Safari/537.36")

# type=39816 == "Completo" (episodio/película entera). Sin este filtro el
# listado de vídeos de un programa mezcla avances, fragmentos y promos
# (episode=0) con los capítulos de verdad -- verificado en vivo con
# Cuéntame (2766 items sin filtro, 418 con filtro).
_FULL_VIDEO_TYPE = 39816
_PAGE_SIZE = 60

# Del HTML de la ficha de programa: identificador numérico del programa.
_PROGRAM_ID_RE = re.compile(
    r'<meta name="DC.identifier" content="(\d+)"')
_OG_TITLE_RE = re.compile(
    r'<meta property="og:title" content="([^"]+)"')

# Similitud mínima (difflib, 0-1) entre el título buscado y el de la
# ficha para aceptar un slug -- por debajo se rechaza (un slug puede
# colisionar con otro programa, p. ej. radio vs TV).
_MIN_NAME_SIMILARITY = 0.7

# El índice local de programas se refresca como mucho cada 7 días (el
# catálogo cambia poco; 5 peticiones de ~5MB por refresco no se hacen en
# cada chequeo).
INDEX_MAX_AGE_DAYS = 7.0

_TIMEOUT = 20


class RTVEUnavailableError(Exception):
    """5xx / formato inesperado del servidor -- mismo criterio que
    CrunchyrollUnavailableError/MangaDexUnavailableError."""
    pass


class RTVERateLimitError(Exception):
    """429 -- límite de la API de RTVE alcanzado."""
    pass


def _slugify(title: str) -> str:
    """Título a slug estilo RTVE ("Cuéntame cómo pasó" ->
    "cuentame-como-paso"): minúsculas, sin tildes, no-alfanumérico a
    guiones. Los slugs de play/videos/ suelen seguir esta forma."""
    normalized = unicodedata.normalize("NFKD", title or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _parse_program_page(html: str) -> tuple[str, str]:
    """(program_id, og_title) desde el HTML de una ficha de programa --
    ("", "") si no es una ficha válida."""
    if not html:
        return "", ""
    m_id = _PROGRAM_ID_RE.search(html)
    m_title = _OG_TITLE_RE.search(html)
    return ((m_id.group(1) if m_id else ""),
            (m_title.group(1).strip() if m_title else ""))


def _match_ratio(q: str, c: str) -> float:
    """Similitud 0-1 entre títulos ya slugificados (espacios en vez de
    guiones): 1.0 por contención (con longitud mínima -- sin esto un
    programa de una letra como "I+" encaja en cualquier título que la
    contenga, falso positivo visto en vivo con "breaking bad"), si no
    difflib."""
    if not q or not c:
        return 0.0
    if len(q) >= 3 and len(c) >= 4 and (q == c or q in c or c in q):
        return 1.0
    return difflib.SequenceMatcher(None, q, c).ratio()


def _names_match(query: str, candidate: str,
                 threshold: float = _MIN_NAME_SIMILARITY) -> bool:
    """True si *candidate* (título de ficha o de índice) es el mismo
    programa que *query* -- comparación insensible a tildes/mayúsculas
    con margen para coletillas ("Cuéntame cómo pasó - Programa...")."""
    q = _slugify(query).replace("-", " ").strip()
    c = _slugify(candidate).replace("-", " ").strip()
    return _match_ratio(q, c) >= threshold


def episode_is_castilian(video: dict) -> bool | None:
    """Verdicto de castellano para un vídeo de RTVE.

    `language == "es"` (o `"es"` en `qualities[].language`) -> True
    (basta castellano disponible aunque exista VO -- decisión del
    usuario, igual que `es-ES` junto a otros audios en Crunchyroll).
    Vídeo con idioma conocido pero sin español (p. ej. `"ca"` solo) ->
    False. Sin ningún dato de idioma -> None (sin dato).
    """
    if not isinstance(video, dict):
        return None
    candidates = []
    lang = video.get("language")
    if lang:
        candidates.append(lang)
    for q in video.get("qualities") or []:
        if isinstance(q, dict) and q.get("language"):
            candidates.append(q["language"])
    normed = [str(c).strip().lower().replace("_", "-") for c in candidates]
    normed = [n for n in normed if n]
    if not normed:
        return None
    if any(n == "es" or n.startswith("es-") for n in normed):
        return True
    return False


def cutoff_from_episodes(verdicts: dict) -> dict:
    """{temporada: último_episodio_doblado} a partir de veredictos
    {(temporada, episodio): True|False|None} -- misma regla que
    core/server_audio.cutoff_from_audio y
    core/crunchyroll_client.cutoff_from_episodes: solo tramos iniciales
    TODO True (un hueco o un unknown intermedio invalida el resto)."""
    by_season: dict[int, dict[int, bool | None]] = {}
    for (season, ep), verdict in (verdicts or {}).items():
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


def _ep_numbers(video: dict, default_season: int | None = None) -> tuple[int | None, int | None]:
    """(temporada, episodio) de un vídeo -- (None, None) si no se pueden
    determinar (`temporadaOrden`/`episode`; los clips traen 0 o nada y se
    descartan siempre).

    *default_season* cubre programas de temporada única donde RTVE no
    rellena `temporadaOrden` (visto en vivo: "Dragon Ball DAIMA" trae
    episodios 1-8 en castellano sin temporada) -- con episodio >= 1 se
    atribuyen a esa temporada. En programas multi-temporada no se aplica
    (quien llama solo lo pasa si la ficha trae <= 1 temporada): atribuir
    a ciegas a la T1 podría extender el corte con episodios de otra
    temporada."""
    if not isinstance(video, dict):
        return None, None
    try:
        season = video.get("temporadaOrden")
        ep = video.get("episode")
        s = int(season) if season is not None else None
        e = int(ep) if ep is not None else None
    except (TypeError, ValueError):
        return None, None
    if not e:
        return None, None
    if not s:
        if default_season:
            return int(default_season), e
        return None, None
    return s, e


class RTVEClient:
    """Cliente de solo-metadatos para señal de doblaje en RTVE Play."""

    _MAX_REQUESTS_PER_WINDOW = 20
    _WINDOW_SECONDS = 60.0

    def __init__(self, index_path=None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _BROWSER_UA})
        self._request_times: deque = deque()
        self._rate_lock = threading.Lock()
        self._index_path = index_path
        self._index: list | None = None

    def _throttle(self):
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

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        try:
            r = self.session.get(url, timeout=_TIMEOUT, **kwargs)
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Sin conexión a internet.")
        if r.status_code == 429:
            raise RTVERateLimitError(
                "Límite de peticiones de RTVE alcanzado -- espera un minuto e inténtalo de nuevo.")
        if r.status_code >= 500:
            raise RTVEUnavailableError(
                "El servicio de RTVE no está disponible ahora mismo "
                "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
        return r

    def _get_json(self, url: str, **kwargs) -> dict:
        """GET + JSON como dict ({} si la forma no es la esperada) --
        404/HTTPError se propagan para que quien llama distinga "no
        existe" de "fallo de red" (ver search_program)."""
        r = self._get(url, **kwargs)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    # ---- búsqueda de programa ----

    def program_by_slug(self, query: str) -> dict | None:
        """Ficha del programa cuyo slug coincide con *query* (`play/videos/
        {slug}/` + `DC.identifier`), verificada por similitud de título
        -- None si no existe o el título no encaja. 1-2 peticiones."""
        slug = _slugify(query)
        if not slug:
            return None
        try:
            r = self._get(f"{_WEB}/play/videos/{slug}/")
            if r.status_code == 404:
                return None
            r.raise_for_status()
        except (RTVERateLimitError, RTVEUnavailableError, ConnectionError):
            raise
        except Exception:
            return None
        program_id, page_title = _parse_program_page(r.text)
        if not program_id or not _names_match(query, page_title or ""):
            return None
        try:
            data = self._get_json(f"{_API}/programas/{program_id}.json")
        except Exception:
            return None
        items = (data.get("page") or {}).get("items") or []
        record = items[0] if items else None
        if not isinstance(record, dict):
            return None
        if not _names_match(query, str(record.get("name") or "")):
            return None
        return record

    def _index_file(self):
        if self._index_path is not None:
            from pathlib import Path
            return Path(self._index_path)
        from core.appdirs import app_data_dir
        return app_data_dir() / "rtve_programs_index.json"

    def _load_index(self) -> list:
        """[{id, name}] del índice local -- [] si no existe o está roto."""
        if self._index is not None:
            return self._index
        try:
            with open(self._index_file(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        items = (data if isinstance(data, dict) else {}).get("items") or []
        self._index = [i for i in items
                       if isinstance(i, dict) and i.get("id")]
        return self._index

    def _index_is_fresh(self) -> bool:
        try:
            with open(self._index_file(), encoding="utf-8") as f:
                data = json.load(f)
            checked_at = (data if isinstance(data, dict) else {}).get("checked_at")
        except (OSError, ValueError):
            return False
        if checked_at is None:
            return False
        try:
            return (time.time() - float(checked_at)) / 86400 <= INDEX_MAX_AGE_DAYS
        except (TypeError, ValueError):
            return False

    def refresh_index(self) -> list:
        """Reconstruye el índice local con el dump completo (`size=1000`,
        ~5 peticiones) y lo guarda en disco -- [{id, name}]. Lanza en
        caso de fallo (quien llama decide si reintentar o seguir sin
        índice)."""
        items: list = []
        page = 1
        while True:
            data = self._get_json(f"{_API}/programas.json",
                                  params={"size": 1000, "page": page})
            page_info = data.get("page") or {}
            batch = page_info.get("items") or []
            for it in batch:
                if isinstance(it, dict) and it.get("id") and it.get("name"):
                    items.append({"id": str(it["id"]),
                                  "name": str(it["name"])})
            total_pages = page_info.get("totalPages") or 1
            if page >= total_pages or not batch:
                break
            page += 1
        payload = {"checked_at": time.time(), "items": items}
        path = self._index_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        self._index = items
        _log.info("rtve: índice de programas refrescado (%d entradas)",
                  len(items))
        return items

    def program_by_index(self, query: str) -> dict | None:
        """Mejor candidato del índice local (difflib), verificado contra
        la ficha -- None si no hay nada que encaje. Refresca el índice
        si caducó (7 días); si el refresco falla se sigue con el índice
        viejo que haya."""
        if not (query or "").strip():
            return None
        if not self._index_is_fresh():
            try:
                self.refresh_index()
            except Exception as e:
                _log.warning("rtve: no se pudo refrescar el índice: %s", e)
        index = self._load_index()
        if not index:
            return None
        q = _slugify(query).replace("-", " ").strip()
        best = None
        best_ratio = 0.0
        for it in index:
            name = str(it.get("name") or "")
            c = _slugify(name).replace("-", " ").strip()
            ratio = _match_ratio(q, c)
            if ratio > best_ratio:
                best_ratio, best = ratio, it
        if best is None or best_ratio < _MIN_NAME_SIMILARITY:
            return None
        try:
            data = self._get_json(f"{_API}/programas/{best['id']}.json")
        except Exception:
            return None
        items = (data.get("page") or {}).get("items") or []
        record = items[0] if items else None
        if not isinstance(record, dict):
            return None
        if not _names_match(query, str(record.get("name") or "")):
            return None
        return record

    def search_program(self, query: str) -> dict | None:
        """Ficha del programa de RTVE Play para *query* -- slug directo
        primero (barato), índice local como respaldo. None si no hay
        programa o falla algo (nunca lanza)."""
        if not (query or "").strip():
            return None
        for finder in (self.program_by_slug, self.program_by_index):
            try:
                record = finder(query)
            except (RTVERateLimitError, ConnectionError) as e:
                _log.warning("rtve: fallo al buscar '%s': %s", query, e)
                return None
            except RTVEUnavailableError as e:
                _log.warning("rtve: fallo al buscar '%s': %s", query, e)
                return None
            except Exception as e:
                _log.warning("rtve: fallo al buscar '%s': %s", query, e)
                return None
            if record:
                return record
        return None

    # ---- episodios y corte ----

    def get_full_videos(self, program_id: str | int) -> list:
        """Vídeos completos del programa (`type=39816`, paginado) --
        dicts tal cual de la API. Lanza en caso de fallo."""
        videos: list = []
        page = 1
        while True:
            data = self._get_json(
                f"{_API}/programas/{program_id}/videos.json",
                params={"type": _FULL_VIDEO_TYPE, "size": _PAGE_SIZE,
                        "page": page})
            page_info = data.get("page") or {}
            batch = page_info.get("items") or []
            videos.extend(i for i in batch if isinstance(i, dict))
            total_pages = page_info.get("totalPages") or 1
            if page >= total_pages or not batch:
                break
            page += 1
        return videos

    def program_seasons(self, record: dict) -> dict:
        """{orden_temporada: nº_episodios} desde `seasons[]` de la ficha
        del programa -- {} si no lo trae."""
        sizes = {}
        seasons = (record or {}).get("seasons") or []
        for s in seasons:
            if not isinstance(s, dict):
                continue
            try:
                orden = int(s.get("orden"))
                count = int(s.get("numEpisodes", 0) or 0)
            except (TypeError, ValueError):
                continue
            if orden > 0:
                sizes[orden] = count
        return sizes

    def cutoff_for_series(self, query: str, seasons: list | tuple) -> dict:
        """{temporada: último_episodio_doblado al castellano} para el
        programa de RTVE Play que encaje con *query* -- {} si no hay
        programa, si no trae castellano, o si falla cualquier cosa. Solo
        emite veredicto para temporadas de *seasons* (las que faltan de
        esa serie, igual que eldoblaje.parse_dub_cutoff). Nunca lanza."""
        scope = set()
        for s in seasons or ():
            try:
                scope.add(int(s))
            except (TypeError, ValueError):
                continue
        if not (query or "").strip() or not scope:
            return {}
        try:
            record = self.search_program(query)
            if not record:
                return {}
            # Programas de temporada única (o sin seasons en ficha, como
            # Clan): los vídeos sin temporada se atribuyen a la T1.
            default_season = 1 if len(self.program_seasons(record)) <= 1 else None
            verdicts: dict[tuple[int, int], bool | None] = {}
            for video in self.get_full_videos(record.get("id")):
                s_num, e_num = _ep_numbers(video, default_season)
                if s_num is None or s_num not in scope:
                    continue
                verdicts[(s_num, e_num)] = episode_is_castilian(video)
        except Exception as e:
            _log.warning("rtve: fallo al calcular corte de '%s': %s",
                         query, e)
            return {}
        result = {s: c for s, c in cutoff_from_episodes(verdicts).items()
                  if s in scope}
        if result:
            _log.info("rtve: corte de doblaje es para '%s': %s",
                      query, result)
        return result
