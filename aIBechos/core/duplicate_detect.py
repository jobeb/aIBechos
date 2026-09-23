"""
Detección de contenido duplicado ya subido al servidor, antes de procesar
un archivo -- distinto de la comprobación de "archivo ya existe" (que solo
mira el nombre remoto exacto): aquí se detecta el mismo episodio/película
aunque haya llegado con un nombre de archivo distinto (otra fuente, otra
calidad, otro grupo de subida...).
"""

import re
from typing import Optional

from core.api_client import detect_episode
from core.applog import get_logger
from core.renamer import is_video_file
from core.series_match import series_similarity

_MOVIE_DUPLICATE_MIN_RATIO = 0.75
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_log = get_logger("aIBechos.duplicate_detect", "app.log")


def find_duplicate(existing_filenames: list, media_info, current_filename: str) -> Optional[str]:
    """Busca en *existing_filenames* (lo que ya hay en la carpeta remota de
    destino) algo que represente el mismo contenido que *media_info*, sin
    contar el propio *current_filename* que se está a punto de subir.
    Devuelve el nombre del archivo duplicado encontrado, o None."""
    others = [f for f in existing_filenames if f != current_filename]
    if not others:
        return None

    if media_info.media_type == "tv":
        target_season  = media_info.season
        target_episode = media_info.episode
        if target_season is None or target_episode is None:
            return None
        for name in others:
            if not is_video_file(name):
                continue
            det = detect_episode(name)
            if det.get("season") == target_season and det.get("episode") == target_episode:
                _log.info("Duplicado TV: %s T%sE%s coincide con existente '%s'",
                          media_info.title, target_season, target_episode, name)
                return name
        return None

    # Película/anime sin episodio: antes se asumía que CUALQUIER otro vídeo
    # de la misma carpeta era el mismo contenido (pensado para carpetas "una
    # por película") -- pero eso da falsos positivos en carpetas
    # compartidas por varios títulos (colecciones numeradas, por ejemplo).
    # Ahora se compara el título adivinado del archivo existente contra el
    # de destino, igual que se hace para reutilizar carpetas de serie.
    target_title = media_info.title or ""
    target_year = str(media_info.year or "")
    for name in others:
        if not is_video_file(name):
            continue
        existing_title = detect_episode(name).get("title", "")

        # Desempate por año ANTES de mirar el título: una película nueva que
        # comparte el nombre base con otra más antigua NO es el mismo
        # contenido ("Toy Story 5" (2026) vs "Toy Story" (1995), "Minions
        # and Monsters" (2026) vs "Los Minions" (2015)). detect_episode le
        # QUITA el año al título (core/api_client.py::detect_episode), así
        # que series_similarity jamás lo ve y el impulso de prefijo da por
        # duplicadas películas distintas con el mismo nombre base -- bug
        # real visto en el modo automático: "Omitido: Ya existe otro
        # archivo para este mismo contenido: Toy Story (1995).avi" al subir
        # Toy Story 5. Aquí se mira el año en el nombre de archivo CRUDO y,
        # si el archivo existente trae un año y no coincide con el real de
        # la película (media_info.year, de TMDB), NO es duplicado.
        # (No se registra el descarte: esto se ejecuta contra CADA archivo de
        # la carpeta remota, y en /datos2/peliculas eso eran ~300 líneas de
        # log por cada película subida -- ahogaba el propio log. Lo que
        # interesa es el duplicado que SÍ se encuentra, que sí se registra
        # unas líneas más abajo.)
        existing_year = _year_in_name(name)
        if existing_year and target_year and existing_year != target_year:
            continue

        ratio = series_similarity(target_title, existing_title)
        if ratio >= _MOVIE_DUPLICATE_MIN_RATIO:
            _log.info("Duplicado película: '%s' (destino) vs '%s' -> '%s' (existente), ratio=%.2f",
                      target_title, existing_title, name, ratio)
            return name
    return None


def _year_in_name(name: str) -> str:
    """Primer año (19xx/20xx) que aparece en un nombre de archivo crudo --
    para desempatar por año en la detección de duplicados de películas (ver
    find_duplicate): detect_episode limpia el año del título y
    series_similarity no puede usarlo, así que aquí se mira el nombre tal
    cual viene de la carpeta remota. Devuelve "" si no hay ninguno (un
    archivo sin año no permite descartar por este criterio)."""
    m = _YEAR_RE.search(name)
    return m.group() if m else ""
