"""
Avisa a Plex y/o Jellyfin para que refresquen su biblioteca justo después
de subir contenido nuevo, en vez de esperar a su próximo escaneo periódico.
Cada uno se activa de forma independiente, solo si el usuario configura
host+credencial en Ajustes -- si no, no se llama a nada.

De momento refresca la biblioteca ENTERA de cada servidor (no solo la
carpeta concreta que se acaba de subir): evita tener que hacer que el
usuario empareje cada categoría FTP con un ID de sección de Plex/biblioteca
de Jellyfin, a costa de ser menos quirúrgico. Tanto Plex como Jellyfin son
incrementales (solo procesan lo que cambió), así que el coste real de un
refresco "de más" es bajo.
"""

import threading
import time
from typing import Optional

import requests
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer

from core.applog import get_logger

_log = get_logger("aIBechos.media_refresh", "media_refresh.log")


class _ItemNotFoundSentinel:
    """Marca que el servidor respondió SIN error pero sin ningún item para
    ese id -- a diferencia de None (fallo de red/timeout/token inválido),
    esto sí confirma que el id ya no existe en el servidor (la serie se
    borró de la biblioteca). Ver get_jellyfin_series_item/
    get_plex_series_item y App._rescan_single_series_worker, que trata
    estos dos casos de forma distinta: un fallo de red deja la fila tal
    cual (podría ser algo temporal), pero un "no encontrado" confirmado
    quita la serie de la lista de huecos, igual que ya hace un reescaneo
    completo (ver _scan_missing_episodes)."""
    def __repr__(self):
        return "ITEM_NOT_FOUND"


ITEM_NOT_FOUND = _ItemNotFoundSentinel()


def parse_media_date(value):
    """Convierte una fecha de Jellyfin (cadena ISO8601, "2024-03-01T00:00:00Z")
    o de Plex (entero unix) a epoch en segundos -- para poder comparar
    ambas fuentes con el mismo criterio en core.cleanup_candidates
    (herramienta "Liberar espacio"). None si no se puede interpretar."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _plex_tmdb_id_from_guids(guids: list) -> Optional[int]:
    """Extrae el tmdb_id de la lista "Guid" que trae Plex en cada item
    (p.ej. [{"id": "tmdb://1396"}, {"id": "imdb://tt0903747"}, ...]) --
    factorizado aquí porque get_plex_series/get_plex_usage_stats/las
    funciones de visionado por usuario lo necesitan por igual."""
    for g in guids or []:
        gid = g.get("id", "")
        if gid.startswith("tmdb://"):
            try:
                return int(gid.split("tmdb://", 1)[1])
            except ValueError:
                return None
    return None


_last_refresh_ts = 0.0
_refresh_lock = threading.Lock()


def refresh_plex(host: str, token: str, timeout: int = 10) -> tuple[bool, str]:
    """Refresca todas las secciones de la biblioteca de Plex.
    host: URL base, p.ej. "http://192.168.1.10:32400"."""
    if not host or not token:
        return False, "Plex no configurado"
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        sections = resp.json().get("MediaContainer", {}).get("Directory", [])
    except Exception as e:
        _log.warning("Plex: fallo al listar secciones: %s", e)
        return False, f"Error consultando secciones de Plex: {e}"

    if not sections:
        _log.warning("Plex: no devolvió ninguna sección de biblioteca")
        return False, "Plex no devolvió ninguna sección de biblioteca"

    ok_count = 0
    for sec in sections:
        sec_id = sec.get("key")
        if sec_id is None:
            continue
        try:
            r = requests.get(
                f"{base}/library/sections/{sec_id}/refresh",
                params={"X-Plex-Token": token},
                timeout=timeout,
            )
            r.raise_for_status()
            ok_count += 1
        except Exception as e:
            _log.warning("Plex: fallo al refrescar sección %s: %s", sec_id, e)

    if ok_count == 0:
        return False, "No se pudo refrescar ninguna sección de Plex"
    _log.info("Plex: %d/%d secciones refrescadas", ok_count, len(sections))
    return True, f"{ok_count}/{len(sections)} secciones refrescadas"


def validate_plex(host: str, token: str, timeout: int = 10) -> bool:
    """Comprueba que host+token de Plex son válidos, sin refrescar nada."""
    if not host or not token:
        return False
    try:
        resp = requests.get(
            f"{host.rstrip('/')}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception as e:
        _log.warning("Plex: fallo al validar credenciales: %s", e)
        return False


def refresh_jellyfin(host: str, api_key: str, timeout: int = 10) -> tuple[bool, str]:
    """Dispara un refresco completo de la biblioteca de Jellyfin.
    host: URL base, p.ej. "http://192.168.1.10:8096"."""
    if not host or not api_key:
        return False, "Jellyfin no configurado"
    base = host.rstrip("/")
    try:
        resp = requests.post(
            f"{base}/Library/Refresh",
            headers={"X-Emby-Token": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        _log.warning("Jellyfin: fallo al refrescar: %s", e)
        return False, f"Error refrescando Jellyfin: {e}"
    _log.info("Jellyfin: refresco de biblioteca disparado")
    return True, "Refresco disparado"


def validate_jellyfin(host: str, api_key: str, timeout: int = 10) -> bool:
    """Comprueba que host+API key de Jellyfin son válidos, sin refrescar nada."""
    if not host or not api_key:
        return False
    try:
        resp = requests.get(
            f"{host.rstrip('/')}/System/Info",
            headers={"X-Emby-Token": api_key},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception as e:
        _log.warning("Jellyfin: fallo al validar credenciales: %s", e)
        return False


def get_jellyfin_series(host: str, api_key: str, timeout: int = 15) -> Optional[list]:
    """Lista todas las series de la biblioteca de Jellyfin. Devuelve
    [{"id": str, "name": str, "tmdb_id": int|None, "folder_name": str|None}, ...]
    o None si falla o no está configurado -- usado por el detector de
    episodios que faltan (core/missing_episodes.py), que solo puede
    comparar con TMDB las series que traen su ID de TMDB emparejado.
    folder_name (el último componente de Path, p.ej. "Accused") es el
    nombre REAL de la carpeta en disco -- puede no parecerse nada al
    nombre mostrado ("Acusado" en Jellyfin, con el nombre en el idioma
    configurado, mientras la carpeta se llama como vino la descarga
    original) -- el detector de coincidencia por nombre difuso (ver
    App._find_category_with_existing_folder) no siempre lo reconoce con
    suficiente confianza, así que tenerlo aparte permite un match exacto
    de carpeta cuando hace falta encontrarla de verdad (p.ej. al borrar)."""
    if not host or not api_key:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/Items",
            headers={"X-Emby-Token": api_key},
            params={"IncludeItemTypes": "Series", "Recursive": "true", "Fields": "ProviderIds,Path"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Jellyfin: fallo al listar series: %s", e)
        return None
    shows = []
    for item in data.get("Items", []) or []:
        tmdb_raw = (item.get("ProviderIds") or {}).get("Tmdb")
        try:
            tmdb_id = int(tmdb_raw) if tmdb_raw else None
        except (TypeError, ValueError):
            tmdb_id = None
        path = item.get("Path") or ""
        folder_name = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else None
        shows.append({"id": item.get("Id"), "name": item.get("Name", ""), "tmdb_id": tmdb_id,
                      "folder_name": folder_name})
    _log.info("Jellyfin: %d serie(s) listadas (%d sin ID de TMDB emparejado)",
              len(shows), sum(1 for s in shows if s["tmdb_id"] is None))
    return shows


def get_jellyfin_series_item(host: str, api_key: str, item_id: str, timeout: int = 15) -> Optional[dict]:
    """Igual que get_jellyfin_series, pero para UN solo item ya conocido
    (por su id) -- usado al reescanear una sola serie (ver
    App._rescan_single_series_worker) para releer su tmdb_id/folder_name
    ACTUALES sin pedir la biblioteca entera. Necesario porque si el
    usuario corrige a mano la identificación de esa serie en Jellyfin
    (cambia a qué ficha de TMDB apunta), la fila de "Episodios que faltan"
    seguía usando el tmdb_id viejo -- guardado en caché de la última vez
    que SÍ se pidió la biblioteca completa -- y "reescanear esta serie"
    comprobaba huecos contra la ficha de TMDB equivocada de todas formas.
    Devuelve {"id", "name", "tmdb_id", "folder_name"}, ITEM_NOT_FOUND si el
    servidor respondió sin error pero ya no existe ese id (se borró la
    serie), o None si falla la petición en sí (red/timeout/token)."""
    if not host or not api_key or not item_id:
        return None
    base = host.rstrip("/")
    try:
        # /Items/{id} (sin usuario) responde 400 en algunos servidores --
        # /Items?Ids= es el mismo endpoint que ya usa get_jellyfin_series,
        # solo que acotado a un id concreto, y sí funciona sin usuario.
        resp = requests.get(
            f"{base}/Items",
            headers={"X-Emby-Token": api_key},
            params={"Ids": item_id, "Fields": "ProviderIds,Path"},
            timeout=timeout,
        )
        if resp.status_code == 404:
            return ITEM_NOT_FOUND
        resp.raise_for_status()
        items = resp.json().get("Items", []) or []
    except Exception as e:
        _log.warning("Jellyfin: fallo al releer la serie %s: %s", item_id, e)
        return None
    if not items:
        return ITEM_NOT_FOUND
    item = items[0]
    tmdb_raw = (item.get("ProviderIds") or {}).get("Tmdb")
    try:
        tmdb_id = int(tmdb_raw) if tmdb_raw else None
    except (TypeError, ValueError):
        tmdb_id = None
    path = item.get("Path") or ""
    folder_name = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else None
    return {"id": item.get("Id"), "name": item.get("Name", ""), "tmdb_id": tmdb_id,
            "folder_name": folder_name}


def find_jellyfin_item_by_tmdb_id(host: str, api_key: str, tmdb_id: int, media_type: str,
                                   timeout: int = 15) -> Optional[dict]:
    """Busca una serie o película en Jellyfin por su tmdb_id -- usado por
    "Abrir en Jellyfin/Plex" en Archivos (gui/app.py::
    _open_entry_in_media_server), donde a diferencia de Episodios que
    faltan/Liberar espacio (que YA traen su server_id de un escaneo
    previo) un archivo recién subido no tiene ningún id de servidor
    guardado todavía. Mismo patrón de listado-completo-y-filtrado-en-
    cliente que get_jellyfin_series (en vez de un parámetro de filtrado
    por proveedor en la API, no confirmado que exista en todas las
    versiones de Jellyfin) -- extendido aquí a también cubrir "Movie", no
    solo "Series". Devuelve {"id": str, "name": str} o None si no está
    configurado, falla la petición, o no se encuentra."""
    if not host or not api_key or not tmdb_id:
        return None
    base = host.rstrip("/")
    item_type = "Series" if media_type == "tv" else "Movie"
    try:
        resp = requests.get(
            f"{base}/Items",
            headers={"X-Emby-Token": api_key},
            params={"IncludeItemTypes": item_type, "Recursive": "true", "Fields": "ProviderIds"},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("Items", []) or []
    except Exception as e:
        _log.warning("Jellyfin: fallo al buscar por tmdb_id %s: %s", tmdb_id, e)
        return None
    for item in items:
        tmdb_raw = (item.get("ProviderIds") or {}).get("Tmdb")
        try:
            if tmdb_raw and int(tmdb_raw) == tmdb_id:
                return {"id": item.get("Id"), "name": item.get("Name", "")}
        except (TypeError, ValueError):
            continue
    return None


def get_jellyfin_episodes(host: str, api_key: str, series_id: str, timeout: int = 15) -> Optional[set]:
    """{(temporada, episodio), ...} presentes de verdad en Jellyfin para esa
    serie, o None si falla."""
    if not host or not api_key or not series_id:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/Shows/{series_id}/Episodes",
            headers={"X-Emby-Token": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Jellyfin: fallo al listar episodios de %s: %s", series_id, e)
        return None
    present = set()
    for ep in data.get("Items", []) or []:
        season, episode = ep.get("ParentIndexNumber"), ep.get("IndexNumber")
        if season is not None and episode is not None:
            present.add((season, episode))
    _log.info("Jellyfin: serie %s -> %d episodio(s) indexados (%d de %d items con "
              "temporada/episodio reconocidos)",
              series_id, len(present), len(present), len(data.get("Items", []) or []))
    return present


def get_plex_series(host: str, token: str, timeout: int = 15) -> Optional[list]:
    """Lista todas las series de todas las secciones de tipo "show" de Plex.
    Devuelve [{"rating_key": str, "name": str, "tmdb_id": int|None}, ...] o
    None si falla o no está configurado."""
    if not host or not token:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        sections = resp.json().get("MediaContainer", {}).get("Directory", []) or []
    except Exception as e:
        _log.warning("Plex: fallo al listar secciones: %s", e)
        return None

    shows = []
    for sec in sections:
        if sec.get("type") != "show":
            continue
        sec_id = sec.get("key")
        try:
            resp = requests.get(
                f"{base}/library/sections/{sec_id}/all",
                params={"X-Plex-Token": token, "type": "2", "includeGuids": "1"},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            items = resp.json().get("MediaContainer", {}).get("Metadata", []) or []
        except Exception as e:
            _log.warning("Plex: fallo al listar series de la sección %s: %s", sec_id, e)
            continue
        for item in items:
            tmdb_id = _plex_tmdb_id_from_guids(item.get("Guid", []))
            shows.append({"rating_key": item.get("ratingKey"), "name": item.get("title", ""),
                          "tmdb_id": tmdb_id})
    return shows


def get_plex_series_item(host: str, token: str, rating_key: str, timeout: int = 15) -> Optional[dict]:
    """Igual que get_jellyfin_series_item pero para Plex -- ver ese
    docstring para el motivo (releer el tmdb_id ACTUAL de una sola serie
    tras una re-identificación manual, sin pedir toda la biblioteca).
    Devuelve {"rating_key", "name", "tmdb_id"}, ITEM_NOT_FOUND si el
    servidor respondió sin error pero ya no existe ese id (se borró la
    serie), o None si falla la petición en sí (red/timeout/token)."""
    if not host or not token or not rating_key:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/library/metadata/{rating_key}",
            params={"X-Plex-Token": token, "includeGuids": "1"},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        # A diferencia de /Items de Jellyfin (una búsqueda, que responde
        # 200 con lista vacía), esto es una petición directa por id -- un
        # ratingKey borrado de verdad da 404, no una lista vacía.
        if resp.status_code == 404:
            return ITEM_NOT_FOUND
        resp.raise_for_status()
        items = resp.json().get("MediaContainer", {}).get("Metadata", []) or []
    except Exception as e:
        _log.warning("Plex: fallo al releer la serie %s: %s", rating_key, e)
        return None
    if not items:
        return ITEM_NOT_FOUND
    item = items[0]
    tmdb_id = _plex_tmdb_id_from_guids(item.get("Guid", []))
    return {"rating_key": item.get("ratingKey"), "name": item.get("title", ""), "tmdb_id": tmdb_id}


def find_plex_item_by_tmdb_id(host: str, token: str, tmdb_id: int, media_type: str,
                               timeout: int = 15) -> Optional[dict]:
    """Equivalente a find_jellyfin_item_by_tmdb_id pero para Plex -- ver
    ese docstring para el motivo. Recorre las secciones de la biblioteca
    del tipo que corresponda ("show" para tv, "movie" para películas,
    "type": "2"/"1" respectivamente en la búsqueda de esa sección, mismo
    valor que ya usa get_plex_series para "show") y filtra por tmdb_id en
    el propio cliente. Devuelve {"rating_key": str, "name": str} o None si
    no está configurado, falla la petición, o no se encuentra."""
    if not host or not token or not tmdb_id:
        return None
    base = host.rstrip("/")
    sec_type = "show" if media_type == "tv" else "movie"
    plex_type = "2" if media_type == "tv" else "1"
    try:
        resp = requests.get(
            f"{base}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        sections = resp.json().get("MediaContainer", {}).get("Directory", []) or []
    except Exception as e:
        _log.warning("Plex: fallo al listar secciones: %s", e)
        return None
    for sec in sections:
        if sec.get("type") != sec_type:
            continue
        sec_id = sec.get("key")
        try:
            resp = requests.get(
                f"{base}/library/sections/{sec_id}/all",
                params={"X-Plex-Token": token, "type": plex_type, "includeGuids": "1"},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            items = resp.json().get("MediaContainer", {}).get("Metadata", []) or []
        except Exception as e:
            _log.warning("Plex: fallo al buscar por tmdb_id en la sección %s: %s", sec_id, e)
            continue
        for item in items:
            if _plex_tmdb_id_from_guids(item.get("Guid", [])) == tmdb_id:
                return {"rating_key": item.get("ratingKey"), "name": item.get("title", "")}
    return None


def get_plex_episodes(host: str, token: str, rating_key: str, timeout: int = 15) -> Optional[set]:
    """{(temporada, episodio), ...} presentes de verdad en Plex para esa
    serie, o None si falla."""
    if not host or not token or not rating_key:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/library/metadata/{rating_key}/allLeaves",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("MediaContainer", {}).get("Metadata", []) or []
    except Exception as e:
        _log.warning("Plex: fallo al listar episodios de %s: %s", rating_key, e)
        return None
    present = set()
    for ep in items:
        season, episode = ep.get("parentIndex"), ep.get("index")
        if season is not None and episode is not None:
            present.add((season, episode))
    return present


def get_plex_machine_identifier(host: str, token: str, timeout: int = 10) -> Optional[str]:
    """Identificador único de ESTE servidor Plex -- a diferencia de Jellyfin,
    un enlace a la app web de Plex (app.plex.tv) no basta con el ID del
    ítem: la URL también codifica a qué servidor pertenece, porque la app
    web es la misma para cualquier servidor Plex del mundo. Se usa para
    construir el enlace "abrir en Plex" del logo de origen en Episodios que
    faltan (ver gui/app.py::_open_source_server_link)."""
    if not host or not token:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("MediaContainer", {}).get("machineIdentifier") or None
    except Exception as e:
        _log.warning("Plex: fallo al obtener el identificador del servidor: %s", e)
        return None


def get_jellyfin_usage_stats(host: str, api_key: str, timeout: int = 120,
                             username: str = "") -> Optional[dict]:
    """Datos de uso real (vista/no vista, veces reproducida, fecha del
    último visionado, fecha de añadido) por serie y película en Jellyfin
    -- para la herramienta de liberar espacio (core/cleanup_candidates.py)
    y para la puntuación de tendencia (core/trending.py), que necesitan
    saber qué contenido lleva mucho sin tocarse / cuánto se ha visto.

    UserData es por usuario, no global -- en un servidor familiar/
    compartido (varios usuarios), lo que importa para decidir qué borrar
    es si ALGUIEN de la casa lo ha visto, no una sola persona en
    concreto. Por eso, si *username* se deja vacío (lo normal), se
    consulta a TODOS los usuarios del servidor EN PARALELO y se combina:
    vista si cualquiera de ellos la vio por completo, reproducciones
    sumadas entre todos, fecha del último visionado la más reciente de
    cualquiera. Si *username* sí se indica, se restringe a ese usuario en
    concreto (case-insensitive) -- por si alguna vez interesa ver solo lo
    que ha visto una persona en particular.

    Devuelve {jellyfin_id: {"name", "tmdb_id", "media_type", "year",
    "fully_watched", "play_count", "last_played", "date_added",
    "size_bytes"}, ...} o None si falla o no está configurado. size_bytes
    y date_added no dependen del usuario (son propiedades del archivo),
    así que se piden una sola vez, no una vez por usuario -- solo el
    visionado en sí necesita consultarse por usuario.

    timeout=120 (antes 20s): en un servidor con biblioteca grande y
    muchos usuarios, la consulta recursiva de TODOS los episodios (sin
    filtrar por serie) puede tardar bastante más de 20s por sí sola --
    medido de verdad contra un servidor real con ~1400 series/películas y
    ~14000 episodios: 45s la consulta principal, 23s la de episodios, EN
    UN SOLO HILO SIN CONTENCIÓN (esta función lanza hasta 8 en paralelo,
    uno por usuario, así que la contención real puede ser peor). Con 20s,
    estas consultas fallaban con "Read timed out" para TODOS los
    usuarios, esta función devolvía None, y toda la puntuación de
    tendencia se quedaba vacía como si no hubiera datos de visionado en
    absoluto -- aunque sí los hubiera y en abundancia (bug real: una
    serie marcada como vista no aparecía con ninguna tendencia)."""
    if not host or not api_key:
        return None
    base = host.rstrip("/")
    try:
        users_resp = requests.get(f"{base}/Users", headers={"X-Emby-Token": api_key}, timeout=timeout)
        users_resp.raise_for_status()
        users = users_resp.json() or []
    except Exception as e:
        _log.warning("Jellyfin: fallo al listar usuarios: %s", e)
        return None
    if not users:
        _log.warning("Jellyfin: no hay ningún usuario para consultar datos de visionado")
        return None

    if username:
        match = next((u for u in users if (u.get("Name") or "").lower() == username.lower()), None)
        if match:
            users_to_query = [match]
        else:
            _log.warning("Jellyfin: usuario '%s' no encontrado (%d usuario(s) disponibles) -- "
                         "usando el primero en su lugar", username, len(users))
            users_to_query = users[:1]
    else:
        users_to_query = users

    # Metadata que NO depende del usuario (tamaño de película, fecha de
    # añadido, ProviderIds) -- se pide UNA sola vez, con el primer
    # usuario disponible, ya que estos campos son los mismos lo consulte
    # quien lo consulte.
    admin_user_id = users[0].get("Id")
    try:
        resp = requests.get(
            f"{base}/Users/{admin_user_id}/Items",
            headers={"X-Emby-Token": api_key},
            params={"IncludeItemTypes": "Series,Movie", "Recursive": "true",
                   "Fields": "DateCreated,ProviderIds,MediaSources"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Jellyfin: fallo al consultar datos de uso: %s", e)
        return None

    stats = {}
    series_ids = []
    for item in data.get("Items", []) or []:
        tmdb_raw = (item.get("ProviderIds") or {}).get("Tmdb")
        try:
            tmdb_id = int(tmdb_raw) if tmdb_raw else None
        except (TypeError, ValueError):
            tmdb_id = None
        media_type = "tv" if item.get("Type") == "Series" else "movie"
        if media_type == "movie":
            size_bytes = sum((ms.get("Size") or 0) for ms in item.get("MediaSources", []) or [])
        else:
            size_bytes = 0   # una serie no trae MediaSources propio -- se suma abajo por episodio
            series_ids.append(item.get("Id"))
        stats[item.get("Id")] = {
            "name": item.get("Name", ""),
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "year": str(item.get("ProductionYear") or ""),
            "fully_watched": False,
            "play_count": 0,
            "last_played": None,
            "date_added": item.get("DateCreated"),
            "size_bytes": size_bytes,
        }

    if series_ids:
        # Una sola llamada para TODOS los episodios de TODAS las series
        # (Recursive=true), sumando por SeriesId -- evita una consulta
        # aparte por cada serie, que con una biblioteca grande sería lento.
        # Tampoco depende del usuario (MediaSources es del archivo).
        try:
            ep_resp = requests.get(
                f"{base}/Users/{admin_user_id}/Items",
                headers={"X-Emby-Token": api_key},
                params={"IncludeItemTypes": "Episode", "Recursive": "true", "Fields": "MediaSources"},
                timeout=timeout,
            )
            ep_resp.raise_for_status()
            ep_data = ep_resp.json()
            sizes_by_series = {}
            for ep in ep_data.get("Items", []) or []:
                sid = ep.get("SeriesId")
                if not sid:
                    continue
                ep_size = sum((ms.get("Size") or 0) for ms in ep.get("MediaSources", []) or [])
                sizes_by_series[sid] = sizes_by_series.get(sid, 0) + ep_size
            for sid, total in sizes_by_series.items():
                if sid in stats:
                    stats[sid]["size_bytes"] = total
        except Exception as e:
            _log.warning("Jellyfin: fallo al calcular tamaño de episodios: %s", e)

    # Visionado: SÍ depende del usuario. Se consulta a cada usuario en
    # paralelo (no uno a uno en serie, que con muchos usuarios sería
    # lentísimo) y se combina el resultado -- vista si CUALQUIERA la vio,
    # reproducciones sumadas, último visionado el más reciente de todos.
    def _fetch_user_watch_data(user):
        uid = user.get("Id")
        items, episodes = [], []
        try:
            r = requests.get(
                f"{base}/Users/{uid}/Items",
                headers={"X-Emby-Token": api_key},
                params={"IncludeItemTypes": "Series,Movie", "Recursive": "true", "Fields": "UserData"},
                timeout=timeout,
            )
            r.raise_for_status()
            items = r.json().get("Items", []) or []
        except Exception as e:
            _log.warning("Jellyfin: fallo al consultar visionado del usuario '%s': %s",
                         user.get("Name"), e)
        try:
            # PlayCount/LastPlayedDate del UserData de la SERIE (a
            # diferencia de UnplayedItemCount, que sí es fiable, ver abajo)
            # no siempre vienen poblados por Jellyfin -- visto de verdad
            # con Arcadia: el panel de administración de Jellyfin sí
            # mostraba el visionado real de un usuario, pero ese campo del
            # API a nivel de serie venía a 0/vacío, dejando la puntuación
            # de tendencia en 0 aunque sí se hubiera visto. Se agregan en
            # su lugar desde el UserData de cada EPISODIO (mismo endpoint
            # que ya usa get_jellyfin_episode_watched, confirmado fiable
            # ahí), igual que size_bytes ya se agrega desde episodios más
            # arriba en esta misma función.
            er = requests.get(
                f"{base}/Users/{uid}/Items",
                headers={"X-Emby-Token": api_key},
                params={"IncludeItemTypes": "Episode", "Recursive": "true", "Fields": "UserData"},
                timeout=timeout,
            )
            er.raise_for_status()
            episodes = er.json().get("Items", []) or []
        except Exception as e:
            _log.warning("Jellyfin: fallo al consultar visionado de episodios del usuario '%s': %s",
                         user.get("Name"), e)
        return items, episodes

    def _merge_watch_field(entry, ud):
        entry["play_count"] += ud.get("PlayCount", 0) or 0
        last_played = ud.get("LastPlayedDate")
        # Comparación de cadenas ISO8601 ("2024-01-01T..." <
        # "2024-06-01T...") da el orden cronológico correcto sin
        # tener que convertir a datetime.
        if last_played and (entry["last_played"] is None or last_played > entry["last_played"]):
            entry["last_played"] = last_played

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, len(users_to_query)) or 1) as pool:
        for items, episodes in pool.map(_fetch_user_watch_data, users_to_query):
            for item in items:
                entry = stats.get(item.get("Id"))
                if entry is None:
                    continue
                ud = item.get("UserData", {}) or {}
                if entry["media_type"] == "movie":
                    # Una película sí es un solo archivo -- Played/
                    # PlayCount/LastPlayedDate en su propio UserData son
                    # fiables directamente, sin agregar nada.
                    watched = bool(ud.get("Played"))
                    entry["fully_watched"] = entry["fully_watched"] or watched
                    _merge_watch_field(entry, ud)
                else:
                    # Una serie se considera vista por completo si no le
                    # queda ningún episodio sin ver (UnplayedItemCount
                    # llega a 0) -- este campo SÍ es fiable a nivel de
                    # serie. PlayCount/LastPlayedDate, no -- se agregan
                    # abajo desde los episodios en su lugar.
                    watched = ud.get("UnplayedItemCount", 1) == 0
                    entry["fully_watched"] = entry["fully_watched"] or watched
            for ep in episodes:
                entry = stats.get(ep.get("SeriesId"))
                if entry is None:
                    continue
                _merge_watch_field(entry, ep.get("UserData", {}) or {})

    _log.info("Jellyfin: %d elemento(s) con datos de uso obtenidos (%d usuario(s) consultado(s))",
              len(stats), len(users_to_query))
    return stats


def get_plex_usage_stats(host: str, token: str, timeout: int = 120) -> Optional[dict]:
    """Equivalente a get_jellyfin_usage_stats() pero para Plex -- datos de
    uso real (vista/no vista, veces reproducida, fecha del último
    visionado, fecha de añadido) por serie y película, para la
    herramienta de liberar espacio. Devuelve {rating_key: {"name",
    "tmdb_id", "media_type", "year", "fully_watched", "play_count",
    "last_played", "date_added", "size_bytes"}, ...} o None si falla o no
    está configurado. timeout=120 (antes 20s) por el mismo motivo medido en
    get_jellyfin_usage_stats -- una biblioteca grande puede tardar más de
    20s en responder a un listado recursivo. Las películas traen su
    tamaño directamente en el mismo listado; para series hace falta una
    llamada extra por sección (no
    por serie) pidiendo los episodios, ya que Plex no agrega el tamaño
    total a nivel de serie -- sigue siendo mucho más rápido que calcularlo
    recorriendo el FTP carpeta por carpeta."""
    if not host or not token:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        sections = resp.json().get("MediaContainer", {}).get("Directory", []) or []
    except Exception as e:
        _log.warning("Plex: fallo al listar secciones: %s", e)
        return None

    stats = {}
    for sec in sections:
        sec_type = sec.get("type")
        if sec_type not in ("show", "movie"):
            continue
        sec_id = sec.get("key")
        plex_type = "2" if sec_type == "show" else "1"
        try:
            resp = requests.get(
                f"{base}/library/sections/{sec_id}/all",
                params={"X-Plex-Token": token, "type": plex_type, "includeGuids": "1"},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            items = resp.json().get("MediaContainer", {}).get("Metadata", []) or []
        except Exception as e:
            _log.warning("Plex: fallo al consultar datos de uso de la sección %s: %s", sec_id, e)
            continue
        for item in items:
            tmdb_id = _plex_tmdb_id_from_guids(item.get("Guid", []))
            media_type = "tv" if sec_type == "show" else "movie"
            if media_type == "movie":
                fully_watched = (item.get("viewCount") or 0) > 0
                size_bytes = sum((part.get("size") or 0)
                                 for media in item.get("Media", []) or []
                                 for part in media.get("Part", []) or [])
            else:
                # leafCount = episodios totales, viewedLeafCount = vistos
                fully_watched = item.get("viewedLeafCount", 0) == item.get("leafCount", -1)
                size_bytes = 0   # se suma abajo, con los episodios de la sección
            rating_key = item.get("ratingKey")
            stats[rating_key] = {
                "name": item.get("title", ""),
                "tmdb_id": tmdb_id,
                "media_type": media_type,
                "year": str(item.get("year") or ""),
                "fully_watched": fully_watched,
                "play_count": item.get("viewCount", 0) or 0,
                "last_played": item.get("lastViewedAt"),
                "date_added": item.get("addedAt"),
                "size_bytes": size_bytes,
            }

        if sec_type == "show":
            # Un solo listado recursivo de TODOS los episodios de la
            # sección (type=4), sumando por serie (grandparentRatingKey)
            # -- evita una llamada aparte por cada serie.
            try:
                ep_resp = requests.get(
                    f"{base}/library/sections/{sec_id}/all",
                    params={"X-Plex-Token": token, "type": "4"},
                    headers={"Accept": "application/json"},
                    timeout=timeout,
                )
                ep_resp.raise_for_status()
                episodes = ep_resp.json().get("MediaContainer", {}).get("Metadata", []) or []
                sizes_by_show = {}
                for ep in episodes:
                    show_rk = ep.get("grandparentRatingKey")
                    if not show_rk:
                        continue
                    ep_size = sum((part.get("size") or 0)
                                 for media in ep.get("Media", []) or []
                                 for part in media.get("Part", []) or [])
                    sizes_by_show[show_rk] = sizes_by_show.get(show_rk, 0) + ep_size
                for rk, total in sizes_by_show.items():
                    if rk in stats:
                        stats[rk]["size_bytes"] = total
            except Exception as e:
                _log.warning("Plex: fallo al calcular tamaño de episodios de la sección %s: %s", sec_id, e)

    _log.info("Plex: %d elemento(s) con datos de uso obtenidos", len(stats))
    return stats


# ── Sincronizar visionado por usuario entre Plex y Jellyfin ─────────────────
#
# A diferencia de get_plex_usage_stats/get_jellyfin_usage_stats (que
# AGREGAN el visionado de todos los usuarios en un único resultado, para
# "liberar espacio", donde solo importa si ALGUIEN vio algo), aquí hace
# falta el estado POR USUARIO por separado, para poder sincronizarlo entre
# las dos plataformas persona a persona.
#
# El lado de Plex usa la librería plexapi en vez de requests a mano (a
# diferencia del resto de este archivo) -- el propio token de servidor NO
# basta para listar usuarios ni conseguir un token por usuario (eso vive
# en la API de cuenta de plex.tv, no en el servidor local), y reimplementar
# a mano el cambio de usuario es la parte más delicada de esta función.
# Confirmado en vivo (ver plan): 27/27 usuarios reales sin bloqueo de PIN,
# y tanto la lectura como la escritura (markWatched/markUnwatched)
# funcionan de forma fiable a través del token por usuario.

def get_plex_home_users(host: str, owner_token: str, timeout: int = 15) -> Optional[list]:
    """[{"id": str, "title": str}, ...] -- todos los usuarios (Home/
    gestionados o compartidos) con acceso al servidor Plex del propietario
    de *owner_token*, MÁS el propio propietario (con id="owner", una
    cadena que nunca choca con un id numérico real de Plex) -- caso real:
    el administrador de la cuenta también ve contenido con su propio
    usuario y quiere sincronizar SU visionado, no solo el de las cuentas
    gestionadas/compartidas. MyPlexAccount.users() nunca lo incluye por
    sí solo (es "quien pregunta", no alguien "compartido consigo mismo"),
    así que se añade aquí a mano -- ver get_plex_user_token, que sabe
    resolver id="owner" sin necesitar buscarlo en esa lista (no
    aparecería). Se muestra con la parte del email antes de la @, no con
    account.username/title -- probado en vivo: username da el nombre de
    marca del servidor Plex ("CineBechos"), no el nombre de la persona --
    el email es lo que de verdad identifica a quién pertenece la cuenta,
    igual que ya se usa para las demás integraciones de esta app. None si
    falla o no está configurado."""
    if not host or not owner_token:
        return None
    try:
        account = MyPlexAccount(token=owner_token)
        users = account.users()
    except Exception as e:
        _log.warning("Plex: fallo al listar usuarios de la cuenta: %s", e)
        return None
    email_local_part = (account.email or "").split("@", 1)[0]
    owner_name = email_local_part or account.title or account.username or "Propietario"
    result = [{"id": "owner", "title": owner_name}]
    result += [{"id": str(u.id), "title": u.title} for u in users]
    return result


def get_plex_user_token(host: str, owner_token: str, plex_user_id: str,
                        timeout: int = 15) -> Optional[str]:
    """Token de acceso al servidor Plex de *host*, pero autenticado COMO
    el usuario *plex_user_id* (no el propietario) -- necesario para leer/
    escribir el visionado de ese usuario en concreto. Nunca se cachea por
    el llamador (ver el porqué en el plan: vida corta, más seguro
    re-derivarlo cada vez que guardarlo). None si el usuario no existe o
    la llamada falla (p.ej. una cuenta restringida con PIN -- no
    confirmado como bloqueante en la práctica, pero se trata igual como
    "no disponible" si ocurre).

    plex_user_id="owner" (ver get_plex_home_users) es un caso especial:
    el propio owner_token YA ES su token, no hace falta derivar nada -- y
    no se podría, porque el propietario nunca aparece en
    account.users()."""
    if not host or not owner_token or not plex_user_id:
        return None
    if plex_user_id == "owner":
        return owner_token
    try:
        account = MyPlexAccount(token=owner_token)
        plex = PlexServer(host, owner_token)
        user = next((u for u in account.users() if str(u.id) == str(plex_user_id)), None)
        if user is None:
            _log.warning("Plex: usuario id=%s no encontrado", plex_user_id)
            return None
        return user.get_token(plex.machineIdentifier)
    except Exception as e:
        _log.warning("Plex: fallo al obtener token del usuario id=%s: %s", plex_user_id, e)
        return None


def get_jellyfin_users(host: str, api_key: str, timeout: int = 10) -> Optional[list]:
    """[{"id": str, "name": str}, ...] -- todos los usuarios de Jellyfin.
    Mismo GET /Users que ya hace get_jellyfin_usage_stats internamente,
    factorizado aquí como función propia para no duplicarlo."""
    if not host or not api_key:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(f"{base}/Users", headers={"X-Emby-Token": api_key}, timeout=timeout)
        resp.raise_for_status()
        users = resp.json() or []
    except Exception as e:
        _log.warning("Jellyfin: fallo al listar usuarios: %s", e)
        return None
    return [{"id": u.get("Id"), "name": u.get("Name", "")} for u in users]


def get_plex_episode_watched(host: str, user_token: str, rating_key: str,
                             timeout: int = 15) -> Optional[dict]:
    """{(temporada, episodio): {"watched": bool, "ref": ratingKey_episodio}, ...}
    para la serie *rating_key*, para el usuario dueño de *user_token* -- a
    diferencia de get_plex_episodes() (que solo confirma que el episodio
    existe), aquí importa si ESE usuario en concreto lo ha visto, y se
    guarda el ratingKey de CADA episodio (no el de la serie) para poder
    escribir en él después."""
    if not host or not user_token or not rating_key:
        return None
    try:
        plex = PlexServer(host, user_token)
        show = plex.fetchItem(int(rating_key))
        result = {}
        for ep in show.episodes():
            if ep.parentIndex is not None and ep.index is not None:
                result[(ep.parentIndex, ep.index)] = {
                    "watched": bool(ep.isWatched), "ref": str(ep.ratingKey)}
        return result
    except Exception as e:
        _log.warning("Plex: fallo al leer visionado de episodios de %s: %s", rating_key, e)
        return None


def get_jellyfin_episode_watched(host: str, api_key: str, user_id: str, series_id: str,
                                 timeout: int = 15) -> Optional[dict]:
    """{(temporada, episodio): {"watched": bool, "ref": itemId_episodio}, ...}
    para la serie *series_id*, para el usuario *user_id* -- consulta
    SIEMPRE a través de /Users/{user_id}/Items (no del endpoint genérico
    sin usuario, que puede omitir UserData -- ver issue
    jellyfin/jellyfin#11408)."""
    if not host or not api_key or not user_id or not series_id:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/Users/{user_id}/Items",
            headers={"X-Emby-Token": api_key},
            params={"ParentId": series_id, "IncludeItemTypes": "Episode",
                   "Fields": "UserData", "Recursive": "true"},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("Items", []) or []
    except Exception as e:
        _log.warning("Jellyfin: fallo al leer visionado de episodios de %s (usuario %s): %s",
                     series_id, user_id, e)
        return None
    result = {}
    for ep in items:
        season, episode = ep.get("ParentIndexNumber"), ep.get("IndexNumber")
        if season is not None and episode is not None:
            result[(season, episode)] = {
                "watched": bool((ep.get("UserData") or {}).get("Played")), "ref": ep.get("Id")}
    return result


def get_plex_movie_watched(host: str, user_token: str, timeout: int = 20) -> Optional[dict]:
    """{tmdb_id: {"watched": bool, "ref": ratingKey, "name": título}, ...}
    para todas las películas del servidor, para el usuario dueño de
    *user_token*."""
    if not host or not user_token:
        return None
    try:
        plex = PlexServer(host, user_token)
        result = {}
        for sec in plex.library.sections():
            if sec.type != "movie":
                continue
            for movie in sec.all():
                tmdb_id = _plex_tmdb_id_from_guids([{"id": g.id} for g in getattr(movie, "guids", [])])
                if tmdb_id is not None:
                    result[tmdb_id] = {"watched": bool(movie.isWatched), "ref": str(movie.ratingKey),
                                       "name": movie.title}
        return result
    except Exception as e:
        _log.warning("Plex: fallo al leer visionado de películas: %s", e)
        return None


def get_jellyfin_movie_watched(host: str, api_key: str, user_id: str,
                               timeout: int = 20) -> Optional[dict]:
    """{tmdb_id: {"watched": bool, "ref": itemId, "name": título}, ...}
    para todas las películas de Jellyfin, para el usuario *user_id*."""
    if not host or not api_key or not user_id:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/Users/{user_id}/Items",
            headers={"X-Emby-Token": api_key},
            params={"IncludeItemTypes": "Movie", "Recursive": "true",
                   "Fields": "UserData,ProviderIds"},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("Items", []) or []
    except Exception as e:
        _log.warning("Jellyfin: fallo al leer visionado de películas (usuario %s): %s", user_id, e)
        return None
    result = {}
    for item in items:
        tmdb_raw = (item.get("ProviderIds") or {}).get("Tmdb")
        try:
            tmdb_id = int(tmdb_raw) if tmdb_raw else None
        except (TypeError, ValueError):
            tmdb_id = None
        if tmdb_id is not None:
            result[tmdb_id] = {"watched": bool((item.get("UserData") or {}).get("Played")),
                               "ref": item.get("Id"), "name": item.get("Name", "")}
    return result


def mark_plex_watched(host: str, user_token: str, rating_key: str, timeout: int = 10) -> bool:
    """Marca como vista (para el usuario dueño de *user_token*) la
    película/episodio *rating_key*. Sin equivalente "desmarcar" -- la
    sincronización nunca necesita quitar el visto, ver el plan."""
    if not host or not user_token or not rating_key:
        return False
    try:
        plex = PlexServer(host, user_token)
        plex.fetchItem(int(rating_key)).markWatched()
        return True
    except Exception as e:
        _log.warning("Plex: fallo al marcar como vista %s: %s", rating_key, e)
        return False


def mark_jellyfin_watched(host: str, api_key: str, user_id: str, item_id: str,
                          timeout: int = 10) -> bool:
    """Marca como vista (para *user_id*) la película/episodio *item_id*."""
    if not host or not api_key or not user_id or not item_id:
        return False
    base = host.rstrip("/")
    try:
        resp = requests.post(f"{base}/Users/{user_id}/PlayedItems/{item_id}",
                             headers={"X-Emby-Token": api_key}, timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as e:
        _log.warning("Jellyfin: fallo al marcar como vista %s (usuario %s): %s", item_id, user_id, e)
        return False


def verify_jellyfin_password(host: str, username: str, password: str, timeout: int = 10) -> bool:
    """True si *password* es de verdad la contraseña de *username* en
    Jellyfin -- POST /Users/AuthenticateByName. Usado al emparejar
    usuarios en "Sincronizar visionado" (ver core/watch_sync.py): Plex no
    tiene equivalente para usuarios gestionados de Plex Home (no tienen
    contraseña propia, confirmado contra la documentación oficial de
    Plex), así que esto solo puede confirmar la mitad Jellyfin del
    emparejamiento -- no sustituye la confirmación humana de que ambas
    cuentas son la misma persona."""
    if not host or not username or not password:
        return False
    base = host.rstrip("/")
    try:
        resp = requests.post(
            f"{base}/Users/AuthenticateByName",
            headers={"Authorization": 'MediaBrowser Client="aIBechos", Device="aIBechos", '
                                       'DeviceId="aibechos-watch-sync", Version="1.0.0"'},
            json={"Username": username, "Pw": password},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception as e:
        _log.warning("Jellyfin: fallo al verificar contraseña de '%s': %s", username, e)
        return False


def get_jellyfin_storage_folders(host: str, api_key: str, timeout: int = 10) -> Optional[list]:
    """Devuelve la lista de carpetas de biblioteca que reporta Jellyfin
    (GET /System/Info/Storage, disponible desde Jellyfin 10.11), cada una
    con al menos "Path" y "FreeSpace" (bytes) -- o None si falla, no está
    configurado, o el servidor es demasiado antiguo para tener este
    endpoint (se degrada sin romper nada, igual que get_free_space() de
    FTPClient con un servidor que no soporta ningún comando de espacio)."""
    if not host or not api_key:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/System/Info/Storage",
            headers={"X-Emby-Token": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Jellyfin: fallo al consultar almacenamiento: %s", e)
        return None
    folders = []
    for lib in data.get("Libraries", []) or []:
        folders.extend(lib.get("Folders", []) or [])
    return folders


_storage_cache = {"ts": 0.0, "folders": None}
_STORAGE_CACHE_TTL = 30   # segundos -- evita consultar Jellyfin en cada archivo de un lote grande


def get_jellyfin_free_space_for_root(category_root: str, host: str, api_key: str) -> Optional[int]:
    """Espacio libre (bytes) de la carpeta de Jellyfin que corresponde a
    *category_root* (la raíz de una categoría FTP, p.ej. "/datos2/series/"),
    emparejando por los últimos segmentos de ruta -- ver
    core/jellyfin_storage_match.py. None si Jellyfin no está configurado,
    el endpoint no existe en esa versión, o no hay ninguna carpeta
    coincidente."""
    now = time.monotonic()
    if _storage_cache["folders"] is None or now - _storage_cache["ts"] > _STORAGE_CACHE_TTL:
        _storage_cache["folders"] = get_jellyfin_storage_folders(host, api_key)
        _storage_cache["ts"] = now
    folders = _storage_cache["folders"]
    if not folders:
        return None
    from core.jellyfin_storage_match import match_root_to_folder
    match = match_root_to_folder(category_root, folders)
    return match.get("FreeSpace") if match else None


def _reset_storage_cache_for_tests() -> None:
    global _storage_cache
    _storage_cache = {"ts": 0.0, "folders": None}


def refresh_configured_servers(config, force: bool = False) -> None:
    """Refresca Plex y/o Jellyfin según lo que haya activado el usuario en
    *config* (objeto con .get(key, default), p.ej. Config o el dict-like
    que usa AutoWatcher). No lanza -- cualquier fallo queda solo registrado
    en el log, nunca interrumpe el flujo de subida que lo llama."""
    if config.get("plex_enabled"):
        ok, msg = refresh_plex(config.get("plex_host", ""), config.get("plex_token", ""))
        if not ok:
            _log.warning("Refresco de Plex no completado: %s", msg)
    if config.get("jellyfin_enabled"):
        ok, msg = refresh_jellyfin(config.get("jellyfin_host", ""), config.get("jellyfin_api_key", ""))
        if not ok:
            _log.warning("Refresco de Jellyfin no completado: %s", msg)


def trigger_refresh(config, min_interval_seconds: int = 60) -> None:
    """Punto de entrada pensado para llamar tras cada subida (manual o
    automática): con antirrebote -- si ya se refrescó hace menos de
    min_interval_seconds, no hace nada, para no machacar Plex/Jellyfin con
    un aviso por cada archivo de un lote grande -- y siempre en un hilo
    aparte, así el llamador nunca espera a la respuesta de red."""
    global _last_refresh_ts
    with _refresh_lock:
        now = time.monotonic()
        if now - _last_refresh_ts < min_interval_seconds:
            return
        _last_refresh_ts = now
    threading.Thread(target=refresh_configured_servers, args=(config,), daemon=True).start()


def _reset_debounce_for_tests() -> None:
    global _last_refresh_ts
    _last_refresh_ts = 0.0


def get_jellyfin_movies(host: str, api_key: str, timeout: int = 15) -> Optional[list]:
    """Lista todas las películas de la biblioteca de Jellyfin (mismo patrón
    que get_jellyfin_series pero con IncludeItemTypes=Movie). Devuelve
    [{"id": str, "name": str, "tmdb_id": int|None, "folder_name": str|None},
    ...] o None si falla o no está configurado -- usado por la pestaña
    "Películas" para no recomendar lo que ya está en el servidor."""
    if not host or not api_key:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/Items",
            headers={"X-Emby-Token": api_key},
            params={"IncludeItemTypes": "Movie", "Recursive": "true",
                    "Fields": "ProviderIds,Path"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Jellyfin: fallo al listar películas: %s", e)
        return None
    movies = []
    for item in data.get("Items", []) or []:
        tmdb_raw = (item.get("ProviderIds") or {}).get("Tmdb")
        try:
            tmdb_id = int(tmdb_raw) if tmdb_raw else None
        except (TypeError, ValueError):
            tmdb_id = None
        path = item.get("Path") or ""
        folder_name = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else None
        movies.append({"id": item.get("Id"), "name": item.get("Name", ""), "tmdb_id": tmdb_id,
                       "folder_name": folder_name})
    _log.info("Jellyfin: %d película(s) listadas (%d sin ID de TMDB emparejado)",
              len(movies), sum(1 for m in movies if m["tmdb_id"] is None))
    return movies


def get_plex_movies(host: str, token: str, timeout: int = 15) -> Optional[list]:
    """Lista todas las películas de todas las secciones de tipo "movie" de
    Plex (mismo patrón que get_plex_series pero con sec.type == "movie" y
    type=1). Devuelve [{"rating_key": str, "name": str, "tmdb_id": int|None},
    ...] o None si falla o no está configurado."""
    if not host or not token:
        return None
    base = host.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        sections = resp.json().get("MediaContainer", {}).get("Directory", []) or []
    except Exception as e:
        _log.warning("Plex: fallo al listar secciones: %s", e)
        return None

    movies = []
    for sec in sections:
        if sec.get("type") != "movie":
            continue
        sec_id = sec.get("key")
        try:
            resp = requests.get(
                f"{base}/library/sections/{sec_id}/all",
                params={"X-Plex-Token": token, "type": "1", "includeGuids": "1"},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            items = resp.json().get("MediaContainer", {}).get("Metadata", []) or []
        except Exception as e:
            _log.warning("Plex: fallo al listar películas de la sección %s: %s", sec_id, e)
            continue
        for item in items:
            tmdb_id = _plex_tmdb_id_from_guids(item.get("Guid", []))
            movies.append({"rating_key": item.get("ratingKey"), "name": item.get("title", ""),
                           "tmdb_id": tmdb_id})
    return movies
