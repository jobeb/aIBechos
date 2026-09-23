"""
Categorías de destino FTP: agrupan una ruta raíz del servidor bajo un nombre
libre elegido por el usuario, con una regla de clasificación automática
basada en los géneros que devuelve TMDB. La primera categoría cuyos géneros
coincidan con el título a subir gana; una categoría sin géneros actúa como
comodín (catch-all).
"""

import uuid
from typing import Callable, Optional

from core.adult_content import looks_adult
from core.ftp_client import _ftp_safe
from core.series_match import best_match, best_match_with_year


def new_category_id() -> str:
    return f"cat_{uuid.uuid4().hex[:8]}"


def choose_category(genre_ids, categories: list) -> Optional[dict]:
    """Primera categoría de *categories* (en orden de lista) cuyo genre_ids
    intersecta con *genre_ids*. Si ninguna específica coincide, devuelve la
    primera categoría comodín (genre_ids vacío). None si no hay ninguna aplicable."""
    genre_set = set(genre_ids or [])
    wildcard = None
    for cat in categories:
        cat_genres = cat.get("genre_ids") or []
        if not cat_genres:
            if wildcard is None:
                wildcard = cat
            continue
        if genre_set & set(cat_genres):
            return cat
    return wildcard


def find_existing_category_folder(categories: list, desired_title: str,
                                    known_folder_name: Optional[str],
                                    dir_lookup: Callable[[str], Optional[list]],
                                    known_year: Optional[str] = None,
                                    original_name: Optional[str] = None) -> tuple:
    """Busca en *categories* (ya filtradas por tipo de media) si ya existe
    una carpeta con nombre igual o prácticamente idéntico a *desired_title*
    -- para que la organización real del servidor prevalezca sobre la
    clasificación automática por género cuando no coinciden (series
    movidas a mano de categoría, p.ej. por tener contenido para adultos y
    ya no encajar en una categoría infantil aunque el género -- Animación,
    normalmente -- siga siendo el mismo). Usado tanto por la subida manual
    (gui/app.py) como por AutoWatcher (core/auto_watcher.py); ambos deciden
    ellos mismos CÓMO listar/cachear cada raíz (gui/app.py evita bloquear
    la interfaz, AutoWatcher siempre lista de verdad) a través de
    *dir_lookup*.

    dir_lookup(root) -> lista de nombres de carpeta ya existentes en esa
    raíz, o None si no se puede/quiere comprobar esa categoría ahora mismo
    (p.ej. "use_cache_only" en gui/app.py y esa raíz aún no está en caché)
    -- en ese caso la categoría se salta, no cuenta como "no hay carpeta".

    Solo hace caso a coincidencias de alta confianza: el folder_name ya
    conocido de antemano (p.ej. de un servidor de medios -- el nombre
    mostrado puede estar traducido y no parecerse nada al de la carpeta
    real, así que si YA se sabe cuál es el real no hace falta que se
    parezca a nada), nombre exacto tras sanear, o ratio >= 0.90 de
    series_similarity (el que da cuando un nombre está literalmente
    contenido en el otro, o solo difiere en mayúsculas/capitalización --
    no es un parecido vago). Devuelve (categoría, nombre_de_carpeta_
    existente), o (None, None) si no hay ninguna coincidencia de esa
    confianza en ninguna categoría.

    known_year (opcional, p.ej. first_air_date de TMDB) es el año de
    estreno real de *desired_title* cuando el propio texto no lo trae
    (típico de un título tal cual lo da un servidor de medios) -- caso
    real: "Ranma ½" (sin año) frente a dos carpetas reales "Ranma (1989)"
    y "Ranma (2024)" (remake con el mismo nombre base), donde el ratio
    normal se queda justo por debajo de 0.90 al no encontrar ninguna con
    exact/0.90 de parecido directo. Se usa solo como último recurso, tras
    fallar las comprobaciones de arriba (ver best_match_with_year)."""
    # Defensa en profundidad. El filtro principal está en AutoWatcher, que ni
    # llega hasta aquí con contenido adulto, pero esta función también la usa
    # la subida manual. Reutilizar una carpeta YA EXISTENTE es justo el paso
    # que metió porno en la carpeta infantil, así que si el nombre original
    # tiene marcas de adulto no se reutiliza ninguna: que se cree su propia
    # carpeta donde le toque, en vez de colarse en la de otra cosa.
    if original_name and looks_adult(original_name):
        return None, None

    sanitized_desired = _ftp_safe(desired_title)
    for cat in categories:
        root = cat.get("root", "")
        if not root:
            continue
        existing = dir_lookup(root)
        if existing is None:
            continue
        if known_folder_name:
            existing_lower = {e.lower(): e for e in existing}
            real = existing_lower.get(known_folder_name.lower())
            if real:
                return cat, real
        if sanitized_desired in existing:
            return cat, sanitized_desired
        # strict + allow_annotation: aquí se decide EN QUÉ CARPETA REAL cae
        # el archivo, así que ser prefijo de una carpeta existente no basta.
        # "Star Wars" (lo que queda de "Star Wars Xxx, A Porn Parody 2011"
        # tras limpiar el nombre) puntuaba 0.90 contra "Star Wars Las
        # aventuras de los jóvenes Jedi" y acababa en la categoría infantil.
        # Caso real: porno subido a /datos2/seriespeques/. Con este modo ese
        # par baja a 0.35, y la reutilización legítima sigue funcionando
        # ("Desencanto (Disenchantment)", "Ranma (1989)").
        candidate, ratio = best_match(desired_title, existing, min_ratio=0.55,
                                       strict=True, allow_annotation=True)
        if ratio >= 0.90:
            return cat, candidate
        if known_year:
            candidate, ratio = best_match_with_year(desired_title, existing, known_year)
            if candidate:
                return cat, candidate
    return None, None


def split_template(template: str):
    """Separa una plantilla de ruta completa en (root, plantilla_relativa),
    cortando en '{serie}'. Sin '{serie}', toda la cadena es la raíz."""
    template = template or ""
    idx = template.find("{serie}")
    if idx == -1:
        root = template.rstrip("/")
        if root and not root.startswith("/"):
            root = "/" + root
        return root, ""
    root = template[:idx].rstrip("/")
    if root and not root.startswith("/"):
        root = "/" + root
    relative = template[idx:]
    return root, relative


def build_wildcard_category(name: str, old_template: str) -> dict:
    """Construye una categoría comodín (sin filtro de género) a partir de una
    plantilla de ruta antigua — usada solo por la migración desde
    ftp_path_template/ftp_movie_path_template."""
    root, relative = split_template(old_template)
    return {
        "id": new_category_id(),
        "name": name,
        "genre_ids": [],
        "root": root,
        "template": relative or "{serie}/",
    }
