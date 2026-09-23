from core.amule_search import (
    filename_stem_to_search_terms, build_amule_query, sanitize_search_query,
)


def test_doctor_who_sin_puntos():
    # Caso real: "Doctor.Who.1108..." rompía el parser de aMule
    # ("syntax error / Undefined search expression").
    q = filename_stem_to_search_terms(
        "Doctor.Who.1108.EI.imperio.de.la.muerte.720p.mkv")
    assert q == "Doctor Who 1108 EI imperio de la muerte 720p"
    assert "." not in q and "_" not in q


def test_guiones_y_guion_bajo_y_sin_extension():
    assert filename_stem_to_search_terms("Serie_2x05-WEB-DL_castellano.mkv") == \
        "Serie 2x05 WEB DL castellano"
    assert filename_stem_to_search_terms("Serie 2x05") == "Serie 2x05"
    assert filename_stem_to_search_terms("") == ""


def test_dobles_espacios_colapsados():
    assert filename_stem_to_search_terms("A..B__C.mkv") == "A B C"


def test_parentesis_corchetes_y_comillas_fuera():
    # Un paréntesis sin cerrar o una comilla suelta rompen el parser
    # booleano de aMule; como keywords no aportan nada.
    q = filename_stem_to_search_terms("Serie 1x08 (SUB ESP [cat].mkv")
    assert q == "Serie 1x08 SUB ESP cat"
    assert filename_stem_to_search_terms("L'avventura.1960.720p.mkv") == "L avventura 1960 720p"


def test_sanitize_query_conserva_contenido_sin_brackets():
    assert sanitize_search_query("Doctor Who (2005) 1x08") == "Doctor Who 2005 1x08"
    assert sanitize_search_query("Doctor Who 1x08") == "Doctor Who 1x08"
    assert sanitize_search_query("") == ""


def test_build_query_respeta_template():
    templates = {"Doctor Who": "DW {temporada}x{episodio:02d}"}
    assert build_amule_query("Doctor Who", 11, 8, templates=templates) == "DW 11x08"


def test_build_query_defecto_serie():
    assert build_amule_query("Doctor Who", 11, 8) == "Doctor Who 11x08"
