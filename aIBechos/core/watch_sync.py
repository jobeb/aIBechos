"""
Decide qué hay que sincronizar entre Plex y Jellyfin para un usuario
emparejado, dado el estado de visionado ya leído de ambas plataformas.
Sin imports de red ni GUI -- puro y testable, igual que
core/cleanup_candidates.py y core/season_grouping.py. La lectura/escritura
real vive en core/media_server_refresh.py; la confirmación humana antes de
escribir vive en gui/app.py.

Regla de sincronización, decidida explícitamente por el usuario: gana
"visto" -- si CUALQUIERA de las dos plataformas tiene un ítem marcado como
visto, la otra también se marca. Nunca se desmarca nada como no visto (por
eso SyncAction no tiene ningún concepto de "unmark" -- ni la propia forma
de los datos lo permite). Mismo criterio que ya usa
core.cleanup_candidates.merge_usage_entries() para las estadísticas
agregadas de "liberar espacio".
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class UserMapping:
    """Un usuario de Plex emparejado a mano con su equivalente en
    Jellyfin -- confirmado una vez por la persona que ejecuta la
    sincronización, nunca adivinado por nombre parecido."""
    plex_user_id: str
    plex_user_name: str
    jellyfin_user_id: str
    jellyfin_user_name: str


@dataclass
class WatchedItem:
    """Una película o episodio, con su estado de visionado en cada
    plataforma, para UN usuario ya emparejado. tmdb_id identifica la
    película o la serie a la que pertenece el episodio; season/episode
    solo tienen sentido si media_type == "episode". plex_user_id/
    jellyfin_user_id identifican A QUIÉN pertenece este ítem -- necesario
    para saber, al aplicar un SyncAction, con el token/usuario de quién
    escribir (un mismo tmdb_id puede aparecer una vez por cada usuario
    emparejado, cada uno con su propio estado)."""
    media_type: str          # "movie" | "episode"
    tmdb_id: Optional[int]
    name: str
    season: Optional[int] = None
    episode: Optional[int] = None
    plex_watched: bool = False
    jellyfin_watched: bool = False
    plex_ref: Optional[str] = None       # ratingKey, para poder escribir después
    jellyfin_ref: Optional[str] = None   # itemId, para poder escribir después
    plex_user_id: Optional[str] = None
    jellyfin_user_id: Optional[str] = None


@dataclass
class SyncAction:
    """Una escritura pendiente: marcar *item* como visto en *target*.
    Nunca representa un "desmarcar" -- eso no existe en este diseño."""
    target: str    # "plex" | "jellyfin"
    item: WatchedItem


def diff_watched_items(items: list) -> list:
    """Aplica la regla "gana visto" a una lista de WatchedItem (de UN
    usuario, o de varios ya mezclados -- da igual, cada item se evalúa
    por separado) y devuelve la lista de SyncAction pendientes.

    - Visto en Plex pero no en Jellyfin -> SyncAction("jellyfin", item)
    - Visto en Jellyfin pero no en Plex -> SyncAction("plex", item)
    - Visto en ambas o en ninguna -> sin acción
    - Sin tmdb_id (no se pudo emparejar el título entre plataformas) ->
      se excluye por completo, nunca se adivina la coincidencia (mismo
      principio que core.cleanup_candidates.find_duplicate_tmdb_ids)
    - Sin *_ref en la plataforma DESTINO -> se excluye tambien -- visto de
      verdad: dos bibliotecas con estructura desincronizada (un episodio
      subido a un lado y no al otro, una serie repartida entre categorías
      distintas...) hacía que se generara una acción para marcar como
      vista una película/episodio que sencillamente no existe ahí, sin
      nada a lo que escribir -- salían como "fallidos" en el resumen sin
      ser un fallo real de la API, solo ruido. No hay nada que sincronizar
      donde el contenido no existe en ambos lados."""
    actions = []
    for item in items:
        if item.tmdb_id is None:
            continue
        if item.plex_watched and not item.jellyfin_watched:
            if item.jellyfin_ref is not None:
                actions.append(SyncAction(target="jellyfin", item=item))
        elif item.jellyfin_watched and not item.plex_watched:
            if item.plex_ref is not None:
                actions.append(SyncAction(target="plex", item=item))
    return actions


def summarize_actions(actions: list) -> dict:
    """{"plex": {"movies": n, "episodes": n}, "jellyfin": {"movies": n,
    "episodes": n}} -- los números que muestra la vista previa antes de
    escribir nada."""
    summary = {
        "plex": {"movies": 0, "episodes": 0},
        "jellyfin": {"movies": 0, "episodes": 0},
    }
    for action in actions:
        key = "movies" if action.item.media_type == "movie" else "episodes"
        summary[action.target][key] += 1
    return summary
