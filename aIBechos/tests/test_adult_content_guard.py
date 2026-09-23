"""Contenido para adultos fuera de la clasificación automática.

Caso real que lo motivó: "Star Wars Xxx, A Porn Parody 2011 -- (...).mkv" y
"Star Wars Xxx - A Porn Parody - 2011 - Axel Braun - ....avi" acabaron subidos
a /datos2/seriespeques/, la carpeta infantil del servidor.

La cadena que lo produjo:
  1. detect_episode() deja el nombre en "Star Wars" a secas (tira "Xxx",
     "A Porn Parody", el año y el reparto como basura).
  2. series_similarity("Star Wars", "Star Wars Las aventuras de los jóvenes
     Jedi") devolvía EXACTAMENTE 0.90 por ser prefijo.
  3. find_existing_category_folder acepta a partir de >= 0.90, así que
     reutilizó la carpeta de una serie preescolar.
"""

import pytest

from core.adult_content import adult_reason, looks_adult
from core.ftp_categories import find_existing_category_folder
from core.series_match import series_similarity


# ── Detección ────────────────────────────────────────────────────────────

ADULTOS = [
    "Star Wars Xxx, A Porn Parody 2011 -- (Allie Haze, Jennifer White).mkv",
    "Star Wars Xxx - A Porn Parody - 2011 - Axel Braun - Kimberly Kane.avi",
    "Parody Porn Wars 1 (Version Porno Star Wars).LoPaH.mp4",
    "OUT OF SPEC - A STAR WARS PORN ANIMATION PARODY - 3D HENTAI-46001941.flv",
    "Star_Wars_Xxx_A_Porn_Parody_(2012).mp4",
]

# Controles: nombres normales que NO deben marcarse. Varios llevan a propósito
# subcadenas que dispararían una búsqueda ingenua ("sex" dentro de "Essex" y
# "Middlesex", "anal" dentro de "Analyze").
NORMALES = [
    "Star Wars Las aventuras de los jovenes Jedi 1x01.mkv",
    "Spaceballs (1987) - A Star Wars Parody.mkv",
    "Sex Education S01E01.mkv",
    "Analyze That (2002).mkv",
    "Middlesex.epub",
    "The Essex Serpent 1x03.mkv",
    "Rick y Morty 4x05.mkv",
    "Los Simpson 12x07.mkv",
]


@pytest.mark.parametrize("nombre", ADULTOS)
def test_detecta_contenido_adulto(nombre):
    assert looks_adult(nombre), nombre
    assert adult_reason(nombre), "debe explicar qué lo disparó"


@pytest.mark.parametrize("nombre", NORMALES)
def test_no_marca_contenido_normal(nombre):
    assert not looks_adult(nombre), f"falso positivo: {nombre}"


def test_parodia_sin_marcas_porno_no_se_marca():
    """"Parody" a secas es normalísimo (Spaceballs, Scary Movie): solo
    cuenta cuando va acompañado."""
    assert not looks_adult("Scary Movie (2000) - A Horror Parody.mkv")
    assert looks_adult("Scary Movie - A Porn Parody.mkv")


# ── El 0.90 por prefijo ya no basta para elegir carpeta ──────────────────

def test_prefijo_con_texto_libre_ya_no_puntua_090():
    """El corazón del fallo: 'Star Wars' contra la carpeta de la serie
    preescolar. En el modo de elección de carpeta debe quedar muy por
    debajo del 0.90."""
    r = series_similarity("Star Wars", "Star Wars Las aventuras de los jovenes Jedi",
                          strict=True, allow_annotation=True)
    assert r < 0.90


@pytest.mark.parametrize("carpeta", [
    "Star Wars The Clone Wars",
    "Star Wars Rebels",
    "Star Wars Andor",
])
def test_ninguna_carpeta_de_la_franquicia_casa_con_el_titulo_pelado(carpeta):
    assert series_similarity("Star Wars", carpeta, strict=True, allow_annotation=True) < 0.90


@pytest.mark.parametrize("corto,largo", [
    ("Desencanto",      "Desencanto (Disenchantment)"),
    ("Los Simpson",     "Los Simpson (The Simpsons)"),
    ("Juego de Tronos", "Juego de Tronos (Game of Thrones)"),
    ("Expediente X",    "Expediente X [The X-Files]"),
    ("Ranma",           "Ranma (1989)"),
])
def test_la_reutilizacion_legitima_de_carpetas_sigue_funcionando(corto, largo):
    """El patrón "título (título original)" y el año suelto son anotaciones
    del MISMO título, no otro distinto: deben seguir casando."""
    assert series_similarity(corto, largo, strict=True, allow_annotation=True) >= 0.90


def test_el_modo_laxo_no_cambia():
    """La detección de duplicados depende del modo laxo y ahí el patrón
    "nombre corto vs nombre de release largo" SÍ hay que reconocerlo. Un
    falso positivo allí solo evita repetir una subida."""
    assert series_similarity("Pelicula (2024)", "Pelicula.2024.OtraVersion.WEB-DL") >= 0.90
    assert series_similarity("Animal", "Animal Crackers") >= 0.90


# ── Defensa en profundidad en la elección de carpeta ─────────────────────

_CATEGORIAS = [
    {"id": "c1", "name": "SeriesPeques", "genre_ids": [16, 10751],
     "root": "/datos2/seriespeques", "template": "{serie}/"},
    {"id": "c2", "name": "Series", "genre_ids": [],
     "root": "/datos2/series", "template": "{serie}/"},
]

_EN_SERVIDOR = {
    "/datos2/seriespeques": ["Star Wars Las aventuras de los jovenes Jedi"],
    "/datos2/series":       ["Andor", "Desencanto (Disenchantment)"],
}


def _lookup(root):
    return _EN_SERVIDOR.get(root, [])


def test_no_reutiliza_la_carpeta_infantil_para_el_titulo_pelado():
    cat, carpeta = find_existing_category_folder(_CATEGORIAS, "Star Wars", None, _lookup)
    assert cat is None and carpeta is None


def test_contenido_adulto_no_reutiliza_ninguna_carpeta():
    """Aunque el título limpio casara con una carpeta existente, si el
    nombre ORIGINAL delata contenido adulto no se reutiliza nada."""
    cat, carpeta = find_existing_category_folder(
        _CATEGORIAS, "Desencanto", None, _lookup,
        original_name="Desencanto Xxx A Porn Parody.mkv")
    assert cat is None and carpeta is None


def test_sin_marcas_adultas_la_reutilizacion_sigue_igual():
    cat, carpeta = find_existing_category_folder(
        _CATEGORIAS, "Desencanto", None, _lookup,
        original_name="Desencanto 1x03 1080p.mkv")
    assert cat is not None and cat["name"] == "Series"
    assert carpeta == "Desencanto (Disenchantment)"
