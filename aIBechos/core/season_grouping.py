"""
Clasifica los archivos de una serie (sueltos en la raíz, o ya repartidos en
subcarpetas "Temporada NN") según en qué temporada debería vivir cada uno --
para detectar y corregir series con estructura mezclada en el servidor
(algunos episodios sueltos, otros ya agrupados por temporada). Sin imports
de FTP ni GUI: puro y testable, pensado para un script de mantenimiento
aparte (ver contexto en el historial de la sesión), no una función de la
aplicación.

Toda serie termina agrupada por temporada, SIN excepción: una serie de una
sola temporada -- o anime con numeración absoluta, que
core.api_client.detect_episode() siempre resuelve como season=1 -- termina
igualmente con una única carpeta "Temporada 01/", nunca archivos sueltos en
la raíz.
"""

import re
from dataclasses import dataclass, field

from core.api_client import detect_episode
from core.cleanup_candidates import file_base_name
from core.renamer import is_video_file


def season_folder_name(season: int) -> str:
    return f"Temporada {season:02d}"


# ── Segundo intento, más permisivo que detect_episode() ─────────────────────
#
# detect_episode() exige espacio/guion a ambos lados de un número aislado
# (ver EPISODE_PATTERNS en core/api_client.py) porque esa función también
# decide si un archivo es una PELÍCULA en TODA la app (búsqueda, subida,
# renombrado) -- ahí, aceptar punto/guion_bajo como separador o un número
# pegado al principio/final del nombre confundiría películas tituladas solo
# con un número o con año sin separar ("1917.mkv", "Movie.2008.mkv") con
# episodios de anime.
#
# Aquí, en cambio, ya sabemos que el archivo vive dentro de una carpeta que
# el propio escaneo identificó como serie de TV en el FTP -- esa ambigüedad
# con películas no aplica, así que un segundo intento más permisivo es
# seguro (y el informe del Paso A se revisa a mano de todas formas antes de
# mover nada). Solo hace falta saber SI hay un número de episodio (siempre
# season=1, numeración absoluta -- igual que el resto de patrones "anime"
# de detect_episode()), nunca cuál es exactamente.
_BRACKET_SPAN_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)")
_FALLBACK_NUMBER_RE = re.compile(r"(?:^|[\s\-._])(\d{1,4})(?:[\s\-._]|$)")


def _mask_bracketed(text: str) -> str:
    """Sustituye el contenido de [...]/(...) por espacios (misma longitud)
    para no confundir un año o tag de release group entre paréntesis/
    corchetes con el número de episodio -- p.ej. si el único número del
    nombre estuviera SOLO dentro de un tag entre corchetes (un release
    group con dígitos) y no hubiera ningún episodio real en el resto del
    nombre."""
    return _BRACKET_SPAN_RE.sub(lambda m: " " * len(m.group(0)), text)


def _fallback_isolated_number_found(filename: str) -> bool:
    """True si, tras enmascarar corchetes/paréntesis, hay algún número de
    1 a 4 dígitos delimitado por espacio/guion/punto/guion_bajo (o
    principio/fin de nombre) -- casos reales vistos que detect_episode()
    no reconoce: "1.mp4" (nombre = solo el número), "001 Sailor Moon -
    ....mp4" (número al principio), "Shin.Chan.372.-Historia.mkv" (punto
    como separador), "Naruto_006_Historia.mp4" (guion bajo)."""
    stem = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename)
    return _FALLBACK_NUMBER_RE.search(_mask_bracketed(stem)) is not None


def _normalize_folder(folder: str) -> str:
    return folder.strip().rstrip("/").lower()


@dataclass
class FileMove:
    relative_folder: str            # ubicación actual ("" = suelto en la raíz de la serie)
    filename: str
    target_relative_folder: str     # siempre "Temporada NN" (nunca "")
    season: int


@dataclass
class SeriesSeasonPlan:
    moves: list = field(default_factory=list)              # FileMove -- ubicación actual != destino
    already_correct: list = field(default_factory=list)    # FileMove -- ya está donde debe
    unclassified: list = field(default_factory=list)       # [(relative_folder, filename), ...] -- nunca se tocan


def classify_series_files(files: list) -> SeriesSeasonPlan:
    """files: [(relative_folder, filename), ...] de TODOS los archivos de
    UNA serie -- relative_folder="" para los sueltos en la raíz, o el
    nombre de la subcarpeta actual ("Temporada 3", "Temporada03"... tal
    cual esté en el servidor) si ya estaban agrupados.

    Agrupa por nombre base (vídeo + compañeros -- pósters/nfo/etc., ver
    core.cleanup_candidates.file_base_name) y aplica detect_episode()
    SOLO al archivo de vídeo de cada grupo; los compañeros heredan la
    temporada de su vídeo, nunca se clasifican por su cuenta.

    Un archivo sin vídeo emparejado (compañero huérfano) o cuyo vídeo no
    tiene ningún patrón de episodio reconocible (season=None, p.ej. una
    película metida por error en la carpeta de la serie) va a
    "unclassified" -- nunca se mueve ni se le asigna una temporada al
    azar."""
    groups: dict = {}   # nombre base -> {"video": (relative_folder, filename) | None, "companions": [...]}
    for relative_folder, filename in files:
        base = file_base_name(filename)
        g = groups.setdefault(base, {"video": None, "companions": []})
        if is_video_file(filename):
            g["video"] = (relative_folder, filename)
        else:
            g["companions"].append((relative_folder, filename))

    plan = SeriesSeasonPlan()
    for g in groups.values():
        video = g["video"]
        if video is None:
            plan.unclassified.extend(g["companions"])
            continue

        rel_folder, filename = video
        season = detect_episode(filename).get("season")
        if season is None and _fallback_isolated_number_found(filename):
            season = 1
        if season is None:
            plan.unclassified.append((rel_folder, filename))
            plan.unclassified.extend(g["companions"])
            continue

        target = season_folder_name(season)
        for rel, fname in [(rel_folder, filename)] + g["companions"]:
            move = FileMove(relative_folder=rel, filename=fname,
                            target_relative_folder=target, season=season)
            if _normalize_folder(rel) == _normalize_folder(target):
                plan.already_correct.append(move)
            else:
                plan.moves.append(move)

    return plan
