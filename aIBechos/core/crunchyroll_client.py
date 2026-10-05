"""
Crunchyroll como señal de doblaje al castellano (solo metadatos, sin
cuenta) -- ver https://www.crunchyroll.com/.

Sin API pública: se usa la API interna (beta-api, documentada por la
comunidad y usada por yt-dlp/crunchyroll-rs) como cliente ANÓNIMO, sin
email/password que banear:

1. `GET` de una página de serie (`/series/GY9PJ5KWR/naruto` -- la home
   es a veces una landing de captación sin `apiDomain`) -> del JSON
   embebido se sacan `cxApiParams { apiDomain, anonClientId }` (ver
   _parse_anon_config).
2. `POST {apiDomain}/auth/v1/token` con `grant_type=client_id` y
   `Authorization: Basic base64(anonClientId + ":")` -> `access_token`.
3. `GET {apiDomain}/index/v2` con `Bearer` -> `cms { bucket, policy,
   signature, key_pair_id }` (firma para el CMS).
4. CMS sobre el host que autoriza la propia policy (verificado en vivo:
   `beta-api.crunchyroll.com`, NO el `apiDomain` de la web -- la policy
   solo firma ese host y el otro responde 403): series, temporadas por
   `?series_id=` y episodios por `?season_id=`, con
   `?Policy=&Signature=&Key-Pair-Id=&locale=es-ES`; búsqueda en
   `GET {apiDomain}/content/v1/search?q=...&locale=es-ES` con `Bearer`.

Lo único que se usa de ahí es la señal de AUDIO por episodio
(`audio_locale` / `versions[].audio_locale`): `es-ES` = castellano,
`solo es-419` (u otro español no-ES) = latino (no vale, decisión del
usuario: castellano exigido, igual que core/server_audio.py y
core/streaming_availability.py). Nunca streams ni DRM, nunca descarga
de vídeo -- solo fichas. Nunca lanza: []/{}/None al fallar, quien llama
sigue como si esta consulta no existiera (mismo contrato que
core/eldoblaje.py y core/doblaje_wiki.py).

Solo anime (series + pelis anime): es el 99% del catálogo de
Crunchyroll; para no-anime la búsqueda devuelve vacío y se cachea como
"none" 3 días igual que un corte vacío de eldoblaje.
"""

import base64
import json
import re
import threading
import time
from collections import deque
from urllib.parse import urlsplit

import requests

from core.applog import get_logger

_log = get_logger("aIBechos.crunchyroll", "ai_fallback.log")

# Fuente de `cxApiParams`: una ficha de serie (la home redirige a veces a
# una landing de captación cuyo JSON embebido NO trae `apiDomain` --
# verificado en vivo el 2026-10-05). Naruto como ancla por ser una ficha
# estable del catálogo; la home queda como respaldo.
_CONFIG_URLS = (
    "https://www.crunchyroll.com/series/GY9PJ5KWR/naruto",
    "https://www.crunchyroll.com/",
)
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0 Safari/537.36")

# Del JSON embebido de la página: "anonClientId":"xxx" y
# "apiDomain":"https://...".
_ANON_CLIENT_RE = re.compile(r'"anonClientId"\s*:\s*"([^"]+)"')
_API_DOMAIN_RE = re.compile(r'"apiDomain"\s*:\s*"(https://[^"]+)"')

_TIMEOUT = 15

# Locales que cuentan como castellano de España (verificado en vivo en
# respuestas del CMS: "es-ES"; se aceptan variantes con guion bajo o
# minúsculas por robustez).
_CASTILIAN_LOCALES = {"es-es", "es_es"}
# Español no castellano (latino/neutro/...): confirma que NO hay
# castellano cuando es lo único español presente.
_LATIN_LOCALES = {"es-419", "es_419", "es-mx", "es-ar", "es-cl", "es-co",
                  "es-mx", "es-pe", "es-us"}


class CrunchyrollUnavailableError(Exception):
    """5xx / formato inesperado del servidor -- mismo criterio que
    MangaDexUnavailableError/AniListUnavailableError."""
    pass


class CrunchyrollRateLimitError(Exception):
    """429 -- límite de la API interna alcanzado."""
    pass


def _norm_locale(value) -> str:
    """Locale normalizado en minúsculas con guiones ("es-ES" -> "es-es")."""
    if isinstance(value, dict):
        for key in ("cr_locale", "locale", "code", "language", "name"):
            inner = value.get(key)
            if inner:
                return _norm_locale(inner)
        return ""
    return str(value or "").strip().lower().replace("_", "-")


def _parse_anon_config(html: str) -> tuple[str, str]:
    """(api_domain, anon_client_id) desde el HTML de una página de
    Crunchyroll -- ("", "") si falta alguno (la landing de captación,
    p. ej., trae `anonClientId` pero no `apiDomain`)."""
    if not html:
        return "", ""
    m_client = _ANON_CLIENT_RE.search(html)
    m_domain = _API_DOMAIN_RE.search(html)
    return ((m_domain.group(1) if m_domain else ""),
            (m_client.group(1) if m_client else ""))


def _cms_host_from_policy(policy_b64: str, default: str) -> str:
    """Host del CMS autorizado por la propia policy firmada (verificado en
    vivo: `beta-api.crunchyroll.com` aunque el `apiDomain` de la web sea
    otro -- pedirle el CMS al host no autorizado responde 403).

    La policy es JSON en base64 con el alfabeto de CloudFront (`-`/`_`/`~`
    en vez de `+`/`=`/`/`); el `Resource` trae el prefijo autorizado con
    comodines (`.../cms/v?/ES/M2/-/*`), del que solo se usa el host.
    *default* si no se puede decodificar."""
    try:
        padded = (policy_b64 or "").replace("-", "+").replace("_", "=").replace("~", "/")
        padded += "=" * (-len(padded) % 4)
        resource = (json.loads(base64.b64decode(padded).decode())
                    .get("Statement", [{}])[0].get("Resource", ""))
        host = f"{urlsplit(resource).scheme}://{urlsplit(resource).netloc}"
        if host.startswith("https://") and "." in host:
            return host
    except Exception:
        pass
    return default


def _basic_for_client_id(client_id: str) -> str:
    raw = f"{client_id}:".encode("ascii", "ignore")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def has_castilian_audio(locales) -> bool | None:
    """True si hay `es-ES` entre *locales*, False si hay audio pero NADA
    de castellano (p. ej. solo `ja-JP` o solo `es-419`: doblaje
    confirmado ausente), None si no hay información de audio."""
    if not locales:
        return None
    normed = [_norm_locale(loc) for loc in locales]
    normed = [n for n in normed if n]
    if not normed:
        return None
    if any(n in _CASTILIAN_LOCALES for n in normed):
        return True
    return False


def episode_is_castilian(ep: dict) -> bool | None:
    """Verdicto de castellano para un episodio del CMS.

    Mira `versions[].audio_locale` (cada doblaje disponible) y como
    respaldo `audio_locale`/`audioLocale` del propio episodio. `es-ES`
    en cualquiera -> True. Episodio con audio conocido pero sin `es-ES`
    (p. ej. `ja-JP` solo, o `es-419` solo) -> False. Sin ningún dato de
    audio -> None (sin dato, no se supone nada).
    """
    if not isinstance(ep, dict):
        return None
    candidates: list = []
    versions = ep.get("versions")
    if isinstance(versions, list):
        for v in versions:
            if not isinstance(v, dict):
                continue
            loc = v.get("audio_locale", v.get("audioLocale"))
            if loc:
                candidates.append(loc)
    for key in ("audio_locale", "audioLocale"):
        if ep.get(key):
            candidates.append(ep[key])
    audios = ep.get("audios")
    if isinstance(audios, list):
        for a in audios:
            if isinstance(a, dict):
                for key in ("language", "locale", "audio_locale"):
                    if a.get(key):
                        candidates.append(a[key])
    return has_castilian_audio(candidates)


def cutoff_from_episodes(verdicts: dict) -> dict:
    """{temporada: último_episodio_doblado} a partir de veredictos
    {(temporada, episodio): True|False|None} -- misma regla que
    core/server_audio.cutoff_from_audio: solo tramos iniciales TODO True
    (un hueco o un unknown intermedio invalida el resto)."""
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


def _parse_search_items(data: dict) -> tuple[list, list]:
    """(series, películas) de `content/v1/search` -- cada una el dict tal
    cual de la API (con `id`/`title`)."""
    series, movies = [], []
    if not isinstance(data, dict):
        return series, movies
    for block in data.get("items") or []:
        if not isinstance(block, dict) or not block.get("total"):
            continue
        kind = block.get("type")
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            if kind == "series":
                series.append(item)
            elif kind == "movie_listing":
                movies.append(item)
    return series, movies


def _series_title(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("title", "name"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    meta = item.get("series_metadata")
    if isinstance(meta, dict):
        return _series_title(meta)
    return ""


def _series_locales(item: dict) -> list:
    """Locales de audio declarados a nivel de serie (si los trae)."""
    if not isinstance(item, dict):
        return []
    for key in ("audio_locales", "audioLocales"):
        val = item.get(key)
        if isinstance(val, list):
            return val
    meta = item.get("series_metadata")
    if isinstance(meta, dict):
        return _series_locales(meta)
    return []


def _ep_numbers(ep: dict) -> tuple[int | None, int | None]:
    """(temporada, episodio) de un episodio del CMS -- (None, None) si no
    se pueden determinar. El CMS no siempre trae `season_number`: se
    aceptan `season_number`/`seasonNumber` y `episode`/`sequence_number`/
    `episode_number`."""
    if not isinstance(ep, dict):
        return None, None
    season = ep.get("season_number", ep.get("seasonNumber"))
    num = ep.get("episode", ep.get("sequence_number",
                                  ep.get("episode_number",
                                         ep.get("sequenceNumber"))))
    try:
        s = int(season) if season is not None else None
        e = int(str(num).split(".")[0]) if num is not None else None
    except (TypeError, ValueError):
        return None, None
    return s, e


class CrunchyrollClient:
    """Cliente anónimo de solo-metadatos para señal de doblaje."""

    _MAX_REQUESTS_PER_WINDOW = 20
    _WINDOW_SECONDS = 60.0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _BROWSER_UA})
        self._request_times: deque = deque()
        self._rate_lock = threading.Lock()
        self._api_domain = ""
        self._anon_client_id = ""
        self._token = ""
        self._token_exp = 0.0
        self._cms: dict = {}
        self._auth_lock = threading.Lock()

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
            raise CrunchyrollRateLimitError(
                "Límite de peticiones de Crunchyroll alcanzado -- espera un minuto e inténtalo de nuevo.")
        if r.status_code >= 500:
            raise CrunchyrollUnavailableError(
                "El servicio de Crunchyroll no está disponible ahora mismo "
                "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
        return r

    def _post(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        try:
            r = self.session.post(url, timeout=_TIMEOUT, **kwargs)
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Sin conexión a internet.")
        if r.status_code == 429:
            raise CrunchyrollRateLimitError(
                "Límite de peticiones de Crunchyroll alcanzado -- espera un minuto e inténtalo de nuevo.")
        if r.status_code >= 500:
            raise CrunchyrollUnavailableError(
                "El servicio de Crunchyroll no está disponible ahora mismo "
                "(error del propio servidor) -- inténtalo de nuevo en unos minutos.")
        return r

    def _ensure_auth(self):
        """Token anónimo + firma del CMS cacheados -- relanza como
        CrunchyrollUnavailableError si la web/API cambian de formato."""
        with self._auth_lock:
            now = time.time()
            if self._token and now < self._token_exp and self._cms:
                return
            try:
                api_domain, client_id = "", ""
                for url in _CONFIG_URLS:
                    page = self._get(url)
                    page.raise_for_status()
                    api_domain, client_id = _parse_anon_config(page.text)
                    if api_domain and client_id:
                        break
                if not api_domain or not client_id:
                    raise CrunchyrollUnavailableError(
                        "Crunchyroll cambió el formato de su web -- no se pudo obtener acceso anónimo.")
                self._api_domain = api_domain
                self._anon_client_id = client_id
                token_resp = self._post(
                    f"{api_domain}/auth/v1/token",
                    headers={"Authorization": _basic_for_client_id(client_id),
                             "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                    data="grant_type=client_id&scope=offline_access")
                token_resp.raise_for_status()
                token_data = token_resp.json()
                token = token_data.get("access_token", "")
                if not token:
                    raise CrunchyrollUnavailableError(
                        "Crunchyroll no devolvió token anónimo.")
                self._token = token
                self._token_exp = now + int(token_data.get("expires_in", 240) or 240) - 30
                policy_resp = self._get(
                    f"{api_domain}/index/v2",
                    headers={"Authorization": f"{token_data.get('token_type', 'Bearer')} {token}"})
                policy_resp.raise_for_status()
                policy = policy_resp.json()
                cms = policy.get("cms_beta") or policy.get("cms") or {}
                if not isinstance(cms, dict) or not cms.get("bucket"):
                    raise CrunchyrollUnavailableError(
                        "Crunchyroll no devolvió firma del CMS.")
                cms["cms_host"] = _cms_host_from_policy(
                    cms.get("policy", ""), api_domain)
                self._cms = cms
            except (CrunchyrollUnavailableError, CrunchyrollRateLimitError, ConnectionError):
                raise
            except Exception as e:
                raise CrunchyrollUnavailableError(f"Crunchyroll: fallo de acceso anónimo: {e}")

    def _cms_params(self, locale: str = "es-ES") -> dict:
        cms = self._cms or {}
        return {"Policy": cms.get("policy", ""),
                "Signature": cms.get("signature", ""),
                "Key-Pair-Id": cms.get("key_pair_id", ""),
                "locale": locale}

    def _cms_base(self) -> str:
        cms = self._cms or {}
        host = cms.get("cms_host") or self._api_domain
        return f"{host}/cms/v2{cms.get('bucket', '')}"

    def _cms_collection(self, url: str, locale: str) -> list:
        """Items de una colección del CMS (temporadas/episodios) -- [] si la
        forma de la respuesta no es la esperada (nunca lanza aquí; quien
        llama decide)."""
        r = self._get(url, params=self._cms_params(locale))
        r.raise_for_status()
        data = r.json()
        items = (data.get("items") if isinstance(data, dict) else None) or []
        return [i for i in items if isinstance(i, dict)]

    def search(self, query: str, locale: str = "es-ES") -> tuple[list, list]:
        """(series, películas) para *query* -- dicts tal cual de la API."""
        if not (query or "").strip():
            return [], []
        self._ensure_auth()
        try:
            r = self._get(
                f"{self._api_domain}/content/v1/search",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"q": query.strip(), "n": 10, "type": "", "locale": locale})
            r.raise_for_status()
            return _parse_search_items(r.json())
        except (CrunchyrollRateLimitError, CrunchyrollUnavailableError, ConnectionError):
            raise
        except Exception as e:
            raise CrunchyrollUnavailableError(f"Crunchyroll: fallo al buscar '{query}': {e}")

    def get_series(self, series_id: str, locale: str = "es-ES") -> dict:
        self._ensure_auth()
        r = self._get(f"{self._cms_base()}/series/{series_id}",
                      params=self._cms_params(locale))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    def get_seasons(self, series_id: str, locale: str = "es-ES") -> list:
        """Temporadas de la serie -- la API es HATEOAS: el enlace exacto
        viene en `__links__["series/seasons"]` de la ficha; `?series_id=`
        como forma directa (verificada en vivo), la ruta antigua
        `/series/{id}/seasons` (404 hoy) como último recurso."""
        self._ensure_auth()
        try:
            detail = self.get_series(series_id, locale=locale)
            href = ((detail.get("__links__") or {}).get("series/seasons") or {}).get("href")
            if href:
                url = href if href.startswith("http") else \
                    f"{(self._cms or {}).get('cms_host') or self._api_domain}{href}"
                return self._cms_collection(url, locale)
        except (CrunchyrollRateLimitError, ConnectionError):
            raise
        except Exception:
            pass
        try:
            return self._cms_collection(
                f"{self._cms_base()}/seasons?series_id={series_id}", locale)
        except (CrunchyrollRateLimitError, ConnectionError):
            raise
        except Exception:
            pass
        return self._cms_collection(
            f"{self._cms_base()}/series/{series_id}/seasons", locale)

    def get_episodes(self, season_id: str, locale: str = "es-ES") -> list:
        """Episodios de la temporada -- `?season_id=` (verificado en vivo),
        la ruta antigua `/seasons/{id}/episodes` como respaldo."""
        self._ensure_auth()
        try:
            return self._cms_collection(
                f"{self._cms_base()}/episodes?season_id={season_id}", locale)
        except (CrunchyrollRateLimitError, ConnectionError):
            raise
        except Exception:
            pass
        return self._cms_collection(
            f"{self._cms_base()}/seasons/{season_id}/episodes", locale)

    def cutoff_for_series(self, query: str, seasons: list | tuple,
                          locale: str = "es-ES") -> dict:
        """{temporada: último_episodio_doblado al castellano} para la
        primera serie que encaje con *query* -- {} si no hay serie, si no
        trae audio castellano, o si falla cualquier cosa. Solo emite
        veredicto para temporadas de *seasons* (las que faltan de esa
        serie, igual que eldoblaje.parse_dub_cutoff). Nunca lanza."""
        scope = set()
        for s in seasons or ():
            try:
                scope.add(int(s))
            except (TypeError, ValueError):
                continue
        if not (query or "").strip() or not scope:
            return {}
        try:
            series, movies = self.search(query, locale=locale)
        except Exception as e:
            _log.warning("crunchyroll: fallo al buscar '%s': %s", query, e)
            return {}
        target = (series or movies or [None])[0]
        if not target or not target.get("id"):
            return {}
        series_id = target["id"]
        try:
            cms_seasons = self.get_seasons(series_id, locale=locale)
        except Exception as e:
            _log.warning("crunchyroll: fallo al pedir temporadas de '%s': %s", query, e)
            return {}
        verdicts: dict[tuple[int, int], bool | None] = {}
        for idx, season in enumerate(cms_seasons, start=1):
            season_id = (season or {}).get("id")
            if not season_id:
                continue
            try:
                episodes = self.get_episodes(season_id, locale=locale)
            except Exception as e:
                _log.warning("crunchyroll: fallo al pedir episodios de %s: %s", season_id, e)
                continue
            for pos, ep in enumerate(episodes, start=1):
                s_num, e_num = _ep_numbers(ep if isinstance(ep, dict) else {})
                # El CMS a veces no numera la temporada: posición en el
                # listado de la serie como respaldo (igual que
                # streaming_availability.cutoff_from_show).
                if s_num is None:
                    s_num = idx
                if e_num is None:
                    e_num = pos
                if s_num not in scope:
                    continue
                verdicts[(s_num, e_num)] = episode_is_castilian(ep)
        cutoff = cutoff_from_episodes(verdicts)
        result = {s: c for s, c in cutoff.items() if s in scope}
        if result:
            _log.info("crunchyroll: corte de doblaje es-ES para '%s': %s",
                      query, result)
        return result
