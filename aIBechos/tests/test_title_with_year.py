"""Liberar espacio enseñaba "Los Vengadores (2012) (2012)": el nombre del
servidor ya trae el año y se le volvía a añadir."""
from core.fmt import title_with_year


def test_año_ya_en_el_nombre_sale_una_vez():
    assert title_with_year("Los Vengadores (2012)", "2012") == ("Los Vengadores (2012)", None)
    assert title_with_year("La singular vida de Ibelin 2024", 2024) == ("La singular vida de Ibelin (2024)", None)
    assert title_with_year("Sherlock Holmes 2 - Juego de Sombras -2011", "2011") == \
        ("Sherlock Holmes 2 - Juego de Sombras (2011)", None)
    assert title_with_year("Dune [2021]", "2021") == ("Dune (2021)", None)


def test_sin_año_en_el_nombre_se_añade():
    assert title_with_year("Los Vengadores", "2012") == ("Los Vengadores (2012)", None)
    assert title_with_year("Los Vengadores", "") == ("Los Vengadores", None)


def test_año_distinto_se_avisa_aparte():
    assert title_with_year("Iron Man 3 (2013)", "2008") == ("Iron Man 3 (2013)", "2008")


def test_titulo_que_es_un_año_no_se_toca():
    assert title_with_year("1923", "2022") == ("1923 (2022)", None)
    assert title_with_year("1984", "1984") == ("1984 (1984)", None)


def test_año_en_medio_de_un_nombre_de_archivo_no_se_repite():
    n = "El hombre menguante (2025)-BDRIP.1080p.x264.EAC3.Dual"
    assert title_with_year(n, "2025") == (n, None)
    n = "Tron. Ares [2025 HDrip][Esp+subEsp][1080p][x265-AAC]"
    assert title_with_year(n, "2025") == (n, None)
