from core.custom_links import build_link_url


def test_build_link_url_substitutes_simple_variable():
    url = build_link_url("https://www.themoviedb.org/tv/{tmdb_id}", {"tmdb_id": 1396})
    assert url == "https://www.themoviedb.org/tv/1396"


def test_build_link_url_urlencodes_spaces_and_special_chars():
    url = build_link_url("https://wa.me/?text={nombre_archivo}",
                          {"nombre_archivo": "Breaking Bad 3x07 One Minute"})
    assert url == "https://wa.me/?text=Breaking%20Bad%203x07%20One%20Minute"


def test_build_link_url_missing_variable_becomes_empty_string():
    # Un enlace a nivel de serie (sin episodio concreto) puede tener una
    # plantilla que mencione {episodio} -- no debe fallar, solo dejarlo vacio.
    url = build_link_url("https://ejemplo.com/{serie}/{episodio}", {"serie": "Naruto"})
    assert url == "https://ejemplo.com/Naruto/"


def test_build_link_url_ignores_none_values():
    url = build_link_url("https://ejemplo.com/{titulo}", {"titulo": None})
    assert url == "https://ejemplo.com/"


def test_build_link_url_handles_accented_characters():
    url = build_link_url("https://ejemplo.com/{serie}", {"serie": "(Des)encanto"})
    assert "Des" in url and "encanto" in url


def test_build_link_url_empty_template_returns_empty():
    assert build_link_url("", {"serie": "X"}) == ""


def test_build_link_url_applies_numeric_format_spec():
    # {episodio:02d} debe formatear el numero ANTES de URL-encodearlo, no
    # fallar en silencio como cuando el valor se encodeaba (a str) primero.
    url = build_link_url("https://ejemplo.com/{serie}/{episodio:02d}",
                          {"serie": "Naruto", "episodio": 7})
    assert url == "https://ejemplo.com/Naruto/07"


def test_build_link_url_missing_variable_with_format_spec_becomes_empty():
    url = build_link_url("https://ejemplo.com/{serie}/{episodio:02d}", {"serie": "Naruto"})
    assert url == "https://ejemplo.com/Naruto/"


def test_build_link_url_encodes_literal_spaces_in_template_too():
    # Un espacio que el propio usuario escribe en la plantilla (fuera de
    # cualquier variable, p.ej. entre {serie} y {temporada}) debe salir
    # como %20 igual que los que ya vienen dentro de un valor -- para que
    # la URL final sea válida del todo, no solo la parte de las variables.
    url = build_link_url("https://ejemplo.com/?q={serie} Temporada {temporada}",
                          {"serie": "Naruto", "temporada": 1})
    assert url == "https://ejemplo.com/?q=Naruto%20Temporada%201"
