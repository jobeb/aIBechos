from core.eldoblaje import parse_dub_cutoff, has_absolute_dub_count


def test_hasta_temporada_oculta_posteriores():
    """'Doblada hasta la temporada 2' con huecos en T2-T3: T3 entera en 0,
    T2 visible (sin entrada, doblada entera)."""
    text = "Serie doblada al castellano hasta la temporada 2. La tercera no consta."
    assert parse_dub_cutoff(text, [2, 3]) == {3: 0}


def test_hasta_temporada_en_palabras():
    text = "Se doblaron las dos primeras temporadas dobladas al castellano."
    assert parse_dub_cutoff(text, [1, 2, 3]) == {3: 0}


def test_temporada_sin_doblar_explicita():
    """'La temporada 3 no fue doblada' marca 0 solo en la 3."""
    text = "La temporada 2 se dobló en 2005. La temporada 3 no fue doblada."
    assert parse_dub_cutoff(text, [2, 3]) == {3: 0}


def test_negativo_no_contamina_temporada_vecina():
    """La negación de una frase no puede marcar la temporada de la frase
    anterior aunque esté a menos de 60 caracteres."""
    text = "La temporada 2 se dobló en 2005. La temporada 3 no fue doblada."
    assert parse_dub_cutoff(text, [2]) == {}


def test_absoluta_reparte_por_temporadas():
    """Caso real citado en el módulo (Bleach: 366 episodios, 109 doblados):
    con tamaños de temporada se mapea a corte por temporada y 0 en las
    posteriores."""
    text = "Consta de 366 episodios, de los que solo fueron doblados los 109 primeros."
    assert has_absolute_dub_count(text)
    sizes = {1: 100, 2: 100, 3: 166}
    assert parse_dub_cutoff(text, [1, 2, 3], sizes) == {2: 9, 3: 0}


def test_absoluta_sin_tamanos_no_inventa():
    """Sin season_sizes una cuenta absoluta no se puede repartir: se ignora
    ({} → se sigue con TMDB) en vez de adivinar."""
    text = "Consta de 366 episodios, de los que solo fueron doblados los 109 primeros."
    assert parse_dub_cutoff(text, [1, 2, 3]) == {}


def test_serie_entera_sin_doblar():
    """Negativo global sin temporadas mencionadas: todo el alcance en 0."""
    text = "La serie no fue doblada al castellano, solo se emitió en versión original."
    assert parse_dub_cutoff(text, [1, 2]) == {1: 0, 2: 0}


def test_texto_doblada_no_produce_corte():
    """Ficha normal de serie doblada entera ('doblada al castellano', sin
    peros): {} para no tocar el veredicto TMDB."""
    text = "Serie de animación doblada al castellano en los estudios de Madrid en 2001."
    assert parse_dub_cutoff(text, [1, 2]) == {}
    assert parse_dub_cutoff("", [1]) == {}
    assert parse_dub_cutoff(text, []) == {}
