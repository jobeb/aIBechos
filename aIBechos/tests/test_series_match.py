from core.series_match import (
    normalize_series_name,
    series_similarity,
    best_match,
    best_match_with_year,
    match_names_exclusively,
)


def test_normalize_strips_accents_case_and_article():
    assert normalize_series_name("¡The Boys!") == "boys"
    assert normalize_series_name("El Chavo del Ocho") == "chavo del ocho"


def test_similarity_identical_after_normalization_is_one():
    assert series_similarity("Breaking Bad", "breaking bad") == 1.0


def test_similarity_short_name_contained_in_long_name_is_high():
    # nombre corto de carpeta vs. nombre largo que da TMDB
    assert series_similarity("Peaky Blinders", "Peaky Blinders UK") >= 0.90


def test_similarity_unrelated_titles_is_low():
    assert series_similarity("Breaking Bad", "The Wire") < 0.55


def test_best_match_picks_highest_ratio_above_threshold():
    candidates = ["The Wire", "Breaking Bad USA", "Better Call Saul"]
    name, ratio = best_match("Breaking Bad", candidates, min_ratio=0.55)
    assert name == "Breaking Bad USA"
    assert ratio >= 0.55


def test_best_match_returns_none_when_nothing_close_enough():
    candidates = ["The Wire", "The Sopranos"]
    name, ratio = best_match("Breaking Bad", candidates, min_ratio=0.55)
    assert name is None
    assert ratio == 0.0


def test_similarity_short_normalized_title_does_not_false_positive_on_substring():
    # "El 47" se normaliza a "47" al quitarle el artículo -- una cadena de
    # 2 caracteres que aparecería como subcadena de cualquier título con un
    # "47" en cualquier parte (un año, un número de carpeta...), dando un
    # falso positivo de "es la misma película" si no se protege.
    assert series_similarity("El 47", "Otra Pelicula (1947)") < 0.55
    assert series_similarity("El 47", "0047-Titulo Cualquiera") < 0.55


def test_similarity_still_boosts_meaningful_short_titles():
    # 4+ caracteres normalizados sigue considerándose una coincidencia
    # fuerte -- solo se protege contra fragmentos casi vacíos como "47".
    assert series_similarity("Coco", "Coco (2017)") >= 0.90


def test_similarity_same_series_split_across_two_folders_clears_ftp_threshold():
    # Caso real opuesto al de Arcadia: la MISMA serie repartida en dos
    # carpetas del servidor, una con el título en castellano (solo la T6) y
    # otra con el original (T1-T5) -- el cruce con el FTP debe unirlas, o
    # los 129 episodios de la segunda salen como "te faltan" estando ahí.
    # 0.75 es el umbral de gui/app.py::_FTP_PRESENT_MIN_RATIO; se comprueba
    # aquí para que un cambio en series_similarity que baje este parecido
    # por debajo del listón no pase desapercibido.
    ratio = series_similarity("Prodigiosa: Las aventuras de Ladybug",
                              "Miraculous las aventuras de Ladybug")
    assert ratio >= 0.75
    # ...pero sin llegar al 0.90 que hace falta para REUTILIZAR la carpeta
    # al subir (ver _find_category_with_existing_folder): subir ahí sería
    # mucho más grave que solo contar episodios.
    assert ratio < 0.90


def test_similarity_arcadia_stays_below_the_ftp_merge_threshold():
    # El contrapunto del test de arriba: bajar el listón del cruce FTP a
    # 0.75 NO debe reabrir el caso Arcadia (series distintas).
    assert series_similarity("Arcadia", "Los 3 de Adabo: Cuentos de Arcadia") < 0.75


def test_similarity_does_not_false_positive_on_word_buried_at_the_end():
    # Caso real: "Arcadia" y "Los 3 de Adabo: Cuentos de Arcadia" se
    # fusionaron en la misma carpeta del FTP y se subieron episodios de
    # una serie a la carpeta de la otra -- "arcadia" es una subcadena
    # literal de "los 3 de adabo cuentos de arcadia" (normalizado), pero
    # SOLO aparece al final, no como el título corto de verdad. El impulso
    # de subcadena solo debe dispararse cuando el nombre corto es PREFIJO
    # del largo (ver "Coco"/"Coco (2017)" arriba), no "aparece en
    # cualquier parte".
    assert series_similarity("Arcadia", "Los 3 de Adabo: Cuentos de Arcadia") < 0.55
    # "Cuentos de Arcadia" vs "Arcadia" comparten más texto (ratio bruto de
    # difflib un poco por encima de 0.55), pero lo que de verdad protege de
    # fusionar carpetas sin preguntar es el umbral de 0.90 que usa
    # find_existing_category_folder -- eso NUNCA debe alcanzarse sin que el
    # nombre corto sea prefijo del largo.
    assert series_similarity("Cuentos de Arcadia", "Arcadia") < 0.90


# ── Emparejamiento exclusivo (sin robar el mismo target dos veces) ─────────

def test_match_names_exclusively_basic_case():
    result = match_names_exclusively(["Breaking Bad"], ["Breaking Bad", "Better Call Saul"])
    assert result == {"Breaking Bad": "Breaking Bad"}


def test_match_names_exclusively_prevents_two_candidates_stealing_same_target():
    # "Breaking Bad" y "Breaking Bad El Camino" son parecidos entre sí --
    # con best_match() independiente, AMBOS podrían intentar quedarse con
    # el único título real "Breaking Bad" de Jellyfin/Plex. Aquí solo uno
    # de los dos (el de mayor parecido) debe llevarselo.
    candidates = ["Breaking Bad", "Breaking Bad El Camino"]
    targets = ["Breaking Bad"]
    result = match_names_exclusively(candidates, targets, min_ratio=0.55)
    assert len(result) == 1
    # El de mayor parecido (coincidencia exacta) se lleva el target real.
    assert result.get("Breaking Bad") == "Breaking Bad"
    assert "Breaking Bad El Camino" not in result


def test_match_names_exclusively_assigns_different_targets_to_different_candidates():
    candidates = ["Breaking Bad", "Better Call Saul"]
    targets = ["Breaking Bad", "Better Call Saul", "Otra Serie"]
    result = match_names_exclusively(candidates, targets, min_ratio=0.55)
    assert result == {"Breaking Bad": "Breaking Bad", "Better Call Saul": "Better Call Saul"}


def test_match_names_exclusively_no_match_below_min_ratio():
    result = match_names_exclusively(["Serie Completamente Distinta"], ["Breaking Bad"], min_ratio=0.55)
    assert result == {}


def test_similarity_different_years_never_match_even_if_text_is_close():
    # Mismo nombre, año distinto = pelicula distinta -- ni en modo normal
    # ni en modo estricto debe considerarse un parecido alto, por muy
    # pocos caracteres que cambien entre "1954" y "2014".
    assert series_similarity("Godzilla (1954)", "Godzilla (2014)") == 0.0
    assert series_similarity("Godzilla (1954)", "Godzilla (2014)", strict=True) == 0.0


def test_similarity_only_one_side_has_year_does_not_trigger_year_check():
    # Si solo uno de los dos trae año identificable, la comprobación de
    # "años distintos" no debe aplicarse (deja pasar al resto de la
    # lógica con normalidad).
    assert series_similarity("Coco", "Coco (2017)") >= 0.90


def test_similarity_strict_mode_blocks_unrelated_titles_sharing_a_prefix_word():
    # "Animal" y "Animal Crackers" son peliculas DISTINTAS, no la misma
    # con o sin subtitulo -- en modo estricto (usado para liberar
    # espacio, donde un falso positivo puede llevar a mostrar datos de
    # visionado equivocados) no debe dispararse el impulso de subcadena.
    assert series_similarity("Animal", "Animal Crackers", strict=True) < 0.75
    # En modo normal (por defecto, usado para reutilizar carpetas o
    # detectar duplicados de subida con otro nombre de release) SÍ sigue
    # dandose el impulso -- ese comportamiento existente no debe romperse.
    assert series_similarity("Animal", "Animal Crackers") >= 0.90


def test_similarity_strict_mode_still_allows_same_year_release_name_variants():
    # Mismo año en ambos = señal suficiente de que es el mismo título,
    # incluso en modo estricto, aunque sobren palabras de la release.
    assert series_similarity("Pelicula (2024)", "Pelicula 2024 OtraVersion WEB-DL",
                             strict=True) >= 0.90


def test_match_names_exclusively_is_strict_by_default():
    # Por defecto (a diferencia de series_similarity), match_names_exclusively
    # usa strict=True -- su único uso real es liberar espacio, donde
    # "Animal"/"Animal Crackers" no deben confundirse.
    result = match_names_exclusively(["Animal", "Animal Crackers"], ["Animal Crackers"], min_ratio=0.75)
    assert result == {"Animal Crackers": "Animal Crackers"}
    assert "Animal" not in result


def test_match_names_exclusively_more_candidates_than_targets():
    # 3 candidatos parecidos compitiendo por un unico target real -- solo
    # el de mayor parecido se lo lleva, los otros dos se quedan sin pareja.
    candidates = ["Serie X Temporada 1", "Serie X", "Serie X Extendida"]
    targets = ["Serie X"]
    result = match_names_exclusively(candidates, targets, min_ratio=0.55)
    assert len(result) == 1
    assert result.get("Serie X") == "Serie X"


def test_match_names_exclusively_lax_fallback_matches_short_folder_to_long_title():
    # Caso real (Boruto): la carpeta del servidor solo trae el título
    # corto y Jellyfin/Plex el largo -- el modo estricto no lo casa
    # (0.33 < 0.75), igual que el cruce FTP de "Episodios que faltan"
    # (series_similarity laxa). lax_fallback recupera esa pareja.
    candidates = ["Boruto"]
    targets = ["Boruto: Naruto Next Generations"]
    assert match_names_exclusively(candidates, targets, min_ratio=0.75) == {}
    result = match_names_exclusively(candidates, targets, min_ratio=0.75, lax_fallback=True)
    assert result == {"Boruto": "Boruto: Naruto Next Generations"}


def test_match_names_exclusively_lax_fallback_does_not_steal_exact_target():
    # Cuando el título completo TAMBIÉN existe como candidato, la pasada
    # estricta se lo lleva a él y el fallback laxo NO debe quitárselo al
    # candidato corto -- mismo espíritu que
    # test_match_names_exclusively_is_strict_by_default ("Animal" no roba
    # "Animal Crackers").
    candidates = ["Boruto", "Boruto: Naruto Next Generations"]
    targets = ["Boruto: Naruto Next Generations"]
    result = match_names_exclusively(candidates, targets, min_ratio=0.75, lax_fallback=True)
    assert result == {"Boruto: Naruto Next Generations": "Boruto: Naruto Next Generations"}


def test_match_names_exclusively_lax_fallback_respects_exclusivity():
    # El fallback laxo tampoco asigna el mismo target a dos candidatos:
    # "Animal" y "Animal Crackers" compiten por el único target, y solo
    # uno (el más parecido) se lo lleva.
    candidates = ["Animal", "Animal Crackers"]
    targets = ["Animal Crackers"]
    result = match_names_exclusively(candidates, targets, min_ratio=0.75, lax_fallback=True)
    assert len(result) == 1
    assert result.get("Animal Crackers") == "Animal Crackers"
    assert "Animal" not in result


# ── best_match_with_year (caso real: Ranma ½ 1989 vs remake 2024) ──────────

def test_best_match_with_year_finds_folder_whose_own_year_matches_known_year():
    # "Ranma ½" no trae año -- embebido a mano en el propio texto el ratio
    # normal (series_similarity) se queda en ~0.83, por debajo de 0.90 (ver
    # docstring de best_match_with_year); comparando el candidato SIN su
    # año sí llega.
    candidates = ["Ranma (1989)", "Ranma (2024)"]
    name, ratio = best_match_with_year("Ranma ½", candidates, known_year="1989")
    assert name == "Ranma (1989)"
    assert ratio >= 0.90


def test_best_match_with_year_never_confuses_the_remake():
    candidates = ["Ranma (1989)", "Ranma (2024)"]
    name, ratio = best_match_with_year("Ranma1/2", candidates, known_year="2024")
    assert name == "Ranma (2024)"
    assert ratio >= 0.90


def test_best_match_with_year_ignores_candidates_with_a_different_year():
    # Ninguna carpeta lleva el año conocido -- no hay con qué comparar.
    candidates = ["Ranma (2024)"]
    name, ratio = best_match_with_year("Ranma ½", candidates, known_year="1989")
    assert name is None
    assert ratio == 0.0


def test_best_match_with_year_without_known_year_returns_nothing():
    candidates = ["Ranma (1989)", "Ranma (2024)"]
    name, ratio = best_match_with_year("Ranma ½", candidates, known_year=None)
    assert name is None
    assert ratio == 0.0
