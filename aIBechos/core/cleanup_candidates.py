"""
Lógica pura para decidir qué series/películas son candidatas a liberar
espacio -- combina datos de Jellyfin/Plex (visionado) y FTP (tamaño,
categoría) según los filtros que el usuario tenga activos en la
herramienta "Liberar espacio". Esta es la parte más delicada de toda la
app (el primer paso hacia un borrado real e irreversible): por eso no hay
ningún criterio "por defecto" que actúe solo -- con todos los filtros
vacíos, filter_candidates() devuelve la lista de items TAL CUAL, sin
descartar nada; cada filtro solo entra en juego si el usuario lo activa
explícitamente con un valor concreto.
"""

from dataclasses import dataclass
from typing import Optional
import time

# Valores válidos para CleanupFilters.watched_mode
WATCHED_NEVER = "nunca_vista"
WATCHED_NOT_REWATCHED = "vista_sin_repetir"
WATCHED_LOW_PLAYCOUNT = "pocas_reproducciones"
WATCHED_NO_DATA = "sin_datos_visionado"

_SECONDS_PER_MONTH = 30 * 24 * 3600


@dataclass
class CleanupItem:
    """Un candidato potencial (serie o película) con todo lo necesario
    para aplicarle los filtros -- se construye combinando TMDB (nombre/
    tipo), Jellyfin/Plex (visionado) y FTP (tamaño, ruta, categoría)
    antes de llamar a filter_candidates()."""
    tmdb_id: Optional[int]
    name: str
    media_type: str              # "tv" | "movie"
    ftp_path: str
    category_name: str
    year: str = ""               # año de estreno (Jellyfin ProductionYear / Plex year)
    size_bytes: int = 0
    fully_watched: bool = False
    play_count: int = 0
    last_played_ts: Optional[float] = None   # epoch segundos, None = nunca reproducida
    date_added_ts: Optional[float] = None    # epoch segundos, None = fecha desconocida
    loose_file_paths: Optional[list] = None  # rutas completas si es un grupo de archivos sueltos, no una carpeta (ver group_loose_files_by_name)
    source: str = ""      # "jellyfin" | "plex" | "" si no se encontró en ningún servidor de medios
    server_id: str = ""   # Id de Jellyfin o ratingKey de Plex -- para "abrir en Jellyfin/Plex" (ver gui/app.py::_open_in_media_server)


@dataclass
class CleanupFilters:
    """Filtros activos, todos opcionales y combinables entre sí (Y
    lógico) -- dejar un campo en None/vacío significa "no aplicar ese
    filtro", no "aplicarlo con un valor por defecto"."""
    min_age_months: Optional[int] = None
    watched_mode: Optional[str] = None            # WATCHED_NEVER | WATCHED_NOT_REWATCHED | WATCHED_LOW_PLAYCOUNT | None
    not_rewatched_months: Optional[int] = None    # umbral para WATCHED_NOT_REWATCHED
    max_play_count: Optional[int] = None          # umbral para WATCHED_LOW_PLAYCOUNT
    min_size_gb: Optional[float] = None
    media_types: Optional[set] = None             # {"tv","movie"}, o None = ambos
    category_names: Optional[set] = None          # nombres de categoría FTP, o None = todas
    name_query: Optional[str] = None              # subcadena a buscar en el nombre (case-insensitive)
    only_duplicates: bool = False                 # solo candidatas cuyo tmdb_id aparece en 2+ carpetas/archivos


def _months_ago_ts(months: int, now: Optional[float]) -> float:
    now = now if now is not None else time.time()
    return now - months * _SECONDS_PER_MONTH


def matches_filters(item: CleanupItem, filters: CleanupFilters, now: Optional[float] = None,
                    duplicate_tmdb_ids: Optional[set] = None) -> bool:
    """True si *item* cumple TODOS los filtros activos en *filters*
    (los que estén en None se ignoran). None = filtro inactivo (no
    restringe nada); un conjunto VACÍO (pero no None) significa "no hay
    ningún tipo/categoría marcado" y debe excluir todo, no comportarse
    como si el filtro no existiera -- de ahí "is not None" en vez de
    comprobar solo que el conjunto sea "truthy". *duplicate_tmdb_ids* lo
    calcula filter_candidates() a partir de la lista completa cuando
    only_duplicates está activo -- son claves (tmdb_id, media_type), no
    solo tmdb_id (ver el porqué en find_duplicate_tmdb_ids)."""
    if filters.media_types is not None and item.media_type not in filters.media_types:
        return False
    if filters.category_names is not None and item.category_name not in filters.category_names:
        return False
    if filters.name_query and filters.name_query.lower() not in item.name.lower():
        return False
    if filters.only_duplicates:
        key = (item.tmdb_id, item.media_type)
        if item.tmdb_id is None or not duplicate_tmdb_ids or key not in duplicate_tmdb_ids:
            return False

    if filters.min_age_months is not None:
        if item.date_added_ts is None:
            return False   # sin fecha de añadido no se puede afirmar que sea "antigua"
        if item.date_added_ts > _months_ago_ts(filters.min_age_months, now):
            return False

    if filters.min_size_gb is not None:
        if item.size_bytes < filters.min_size_gb * (1024 ** 3):
            return False

    if filters.watched_mode == WATCHED_NEVER:
        if item.play_count > 0 or item.fully_watched:
            return False
    elif filters.watched_mode == WATCHED_NOT_REWATCHED:
        if not item.fully_watched:
            return False
        if item.last_played_ts is None:
            return False   # vista pero sin fecha de ultimo visionado -- no se puede afirmar "sin repetir"
        months = filters.not_rewatched_months if filters.not_rewatched_months is not None else 0
        if item.last_played_ts > _months_ago_ts(months, now):
            return False
    elif filters.watched_mode == WATCHED_LOW_PLAYCOUNT:
        max_pc = filters.max_play_count if filters.max_play_count is not None else 0
        if item.play_count > max_pc:
            return False
    elif filters.watched_mode == WATCHED_NO_DATA:
        if item.tmdb_id is not None:
            return False   # sí se emparejó con Jellyfin/Plex -- no es "sin datos"

    return True


def find_duplicate_tmdb_ids(items: list) -> set:
    """Conjunto de claves (tmdb_id, media_type) que aparecen en 2 o más
    candidatas -- señal de que el mismo contenido existe repetido en el
    servidor (p.ej. la misma película en dos categorías por error, o a
    la vez como carpeta propia y como archivo suelto). Deliberadamente NO
    se compara por nombre de forma difusa aquí -- solo por tmdb_id, que
    es una coincidencia fiable; un parecido de nombres podría dar falsos
    positivos en una herramienta donde un falso positivo puede llevar a
    borrar algo que no tocaba.

    La clave incluye media_type porque TMDB numera series y películas en
    espacios INDEPENDIENTES -- una serie y una película pueden compartir
    el mismo número de tmdb_id por pura casualidad sin ser el mismo
    contenido ni parecerse en nada (visto de verdad: una serie y una
    película con tmdb_id numéricamente igual, tratadas como "duplicadas"
    cuando no tenían ninguna relación)."""
    from collections import Counter
    counts = Counter((it.tmdb_id, it.media_type) for it in items if it.tmdb_id is not None)
    return {key for key, n in counts.items() if n >= 2}


def filter_candidates(items: list, filters: CleanupFilters, now: Optional[float] = None) -> list:
    """Aplica matches_filters a toda la lista de items, devolviendo solo
    los que cumplen TODOS los filtros activos."""
    duplicate_ids = find_duplicate_tmdb_ids(items) if filters.only_duplicates else None
    return [it for it in items if matches_filters(it, filters, now, duplicate_ids)]


def merge_usage_entries(a: dict, b: dict) -> dict:
    """Combina los datos de uso de Jellyfin y Plex para el MISMO título --
    ninguna de las dos fuentes ve lo que pasa en la otra, así que si el
    usuario ve principalmente por una (o Jellyfin apunta al usuario
    equivocado en un servidor con varios, ver jellyfin_username) y apenas
    usa la otra, quedarse solo con la primera fuente procesada dejaba
    casi todo como "nunca visto" aunque sí se hubiera visto por la otra
    vía. "Vista" si CUALQUIERA de las dos lo dice (no ambas), reproduc-
    ciones sumadas, fecha del último visionado la más reciente entre las
    dos, fecha de añadido la más antigua (primera vez que cualquiera de
    los dos la vio en su biblioteca). source/server_id (para "abrir en
    Jellyfin/Plex", ver gui/app.py::_open_in_media_server) se quedan con
    los de *a* si los tiene -- el llamador procesa Jellyfin antes que Plex,
    así que por construcción esto respeta la preferencia "Jellyfin primero"
    ya establecida en el resto de la app."""
    from core.media_server_refresh import parse_media_date
    last_played = max(
        (t for t in (parse_media_date(a.get("last_played")), parse_media_date(b.get("last_played")))
         if t is not None), default=None)
    date_added = min(
        (t for t in (parse_media_date(a.get("date_added")), parse_media_date(b.get("date_added")))
         if t is not None), default=None)
    return {
        "name": a.get("name") or b.get("name"),
        "tmdb_id": a.get("tmdb_id") or b.get("tmdb_id"),
        "media_type": a.get("media_type") or b.get("media_type"),
        "year": a.get("year") or b.get("year") or "",
        "fully_watched": bool(a.get("fully_watched")) or bool(b.get("fully_watched")),
        "play_count": (a.get("play_count") or 0) + (b.get("play_count") or 0),
        "last_played": last_played,
        "date_added": date_added,
        "size_bytes": a.get("size_bytes") or b.get("size_bytes") or 0,
        "source": a.get("source") or b.get("source") or "",
        "server_id": a.get("server_id") or b.get("server_id") or "",
    }


# Sufijos de archivos "compañeros" de un vídeo (carátulas, arte de Kodi/
# Jellyfin) que hay que agrupar junto al vídeo, no tratar como entradas
# sueltas propias -- vistos de verdad en un servidor real con películas
# guardadas como archivos sueltos en la raíz de la categoría (sin carpeta
# propia por película, a diferencia de las series).
_COMPANION_SUFFIXES = ("-poster", "-backdrop", "-landscape", "-logo", "-banner", "-thumb",
                      "-fanart", "-clearart", "-clearlogo", "-discart")


def _file_base_name(filename: str) -> str:
    """Nombre "base" de un archivo, quitando extensión y, si la tiene,
    un sufijo de archivo compañero conocido -- así "Peli (2020).mkv",
    "Peli (2020)-poster.jpg" y "Peli (2020).nfo" agrupan todos bajo
    "Peli (2020)"."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    lower = stem.lower()
    for suffix in _COMPANION_SUFFIXES:
        if lower.endswith(suffix):
            return stem[:-len(suffix)]
    return stem


# Alias público -- core/season_grouping.py reutiliza este agrupado de
# archivos compañeros (pósters/nfo/etc.) para que un episodio y sus
# archivos asociados siempre se muevan juntos a la misma carpeta de
# temporada, sin duplicar la lista de sufijos aquí y allá.
file_base_name = _file_base_name


def group_loose_files_by_name(files: list) -> dict:
    """Agrupa archivos sueltos (vídeo + póster/backdrop/logo/.../nfo) que
    comparten el mismo nombre base -- para categorías donde cada
    serie/película es un conjunto de archivos directamente en la raíz,
    no una carpeta propia. *files* es [(nombre, tamaño), ...]. Solo se
    devuelven grupos con AL MENOS un archivo de vídeo: una imagen o .nfo
    huérfano (sin su vídeo) no es un candidato borrable por sí solo, y
    tratarlo como uno arriesgaría explicaciones erróneas de qué se libera.
    Devuelve {nombre_base: {"size_bytes": int, "file_names": [...]}, ...}."""
    from core.renamer import is_video_file
    groups: dict = {}
    for name, size in files:
        base = _file_base_name(name)
        g = groups.setdefault(base, {"size_bytes": 0, "file_names": [], "has_video": False})
        g["size_bytes"] += size
        g["file_names"].append(name)
        if is_video_file(name):
            g["has_video"] = True
    return {base: {"size_bytes": g["size_bytes"], "file_names": g["file_names"]}
            for base, g in groups.items() if g["has_video"]}
