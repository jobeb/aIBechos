from core.missing_episodes import (find_missing_episodes, format_missing_summary,
                                    format_missing_ranges, looks_like_season_split,
                                    apply_season_split_filter,
                                    find_unknown_seasons, looks_like_absolute_numbering,
                                    remap_absolute_episodes, remove_missing_episode, remove_series,
                                    tmdb_season_episode_offsets, normalize_tmdb_season_numbering,
                                    _season_ranges,
                                    has_spanish_availability, episode_has_spanish_text,
                                    episode_has_spanish_translation,
                                    eps_without_confirmed_dub,
                                    filter_missing_by_spanish_dub, filter_missing_by_dub_cutoff,
                                    apply_ignored_filter)


def test_find_missing_episodes_finds_gaps():
    expected = {1: [1, 2, 3, 4, 5]}
    present = {(1, 1), (1, 2), (1, 4)}
    missing = find_missing_episodes(expected, present)
    assert missing == {1: [3, 5]}


def test_find_missing_episodes_no_gaps_returns_empty():
    expected = {1: [1, 2, 3]}
    present = {(1, 1), (1, 2), (1, 3)}
    assert find_missing_episodes(expected, present) == {}


def test_find_missing_episodes_season_fully_missing():
    expected = {1: [1, 2], 2: [1, 2]}
    present = {(1, 1), (1, 2)}
    missing = find_missing_episodes(expected, present)
    assert missing == {2: [1, 2]}


def test_find_missing_episodes_ignores_seasons_without_gaps():
    expected = {1: [1, 2], 2: [1, 2], 3: [1]}
    present = {(1, 1), (1, 2), (2, 1), (2, 2), (3, 1)}
    assert find_missing_episodes(expected, present) == {}


def test_season_ranges_groups_consecutive_episodes():
    assert _season_ranges(1, [1, 2, 3, 7]) == ["T1E01-T1E03", "T1E07"]


def test_season_ranges_single_episode():
    assert _season_ranges(2, [5]) == ["T2E05"]


def test_season_ranges_all_consecutive():
    assert _season_ranges(1, [1, 2, 3, 4]) == ["T1E01-T1E04"]


def test_format_missing_summary_empty_returns_empty_string():
    assert format_missing_summary("Serie X", {}) == ""


def test_format_missing_summary_formats_multiple_seasons():
    missing = {1: [5, 8], 3: [1, 2, 3]}
    summary = format_missing_summary("Serie X", missing)
    assert summary == "Serie X: T1E05, T1E08, T3E01-T3E03"


def test_format_missing_ranges_empty_returns_empty_string():
    assert format_missing_ranges({}) == ""


def test_format_missing_ranges_without_show_name():
    missing = {1: [5, 8], 3: [1, 2, 3]}
    assert format_missing_ranges(missing) == "T1E05, T1E08, T3E01-T3E03"


def test_looks_like_season_split_detects_netflix_style_split():
    # Disenchantment: TMDB cuenta 20 episodios en la temporada 1 (Parte 1 +
    # Parte 2), pero la biblioteca solo tiene los primeros 10 -- exactamente
    # la segunda mitad completa falta.
    season_episodes = list(range(1, 21))
    missing = list(range(11, 21))
    assert looks_like_season_split(season_episodes, missing) is True


def test_looks_like_season_split_false_when_gaps_are_scattered():
    # Huecos sueltos (episodios 5 y 8) no es un patron de "partes" -- son
    # de verdad episodios sueltos que faltan.
    season_episodes = list(range(1, 21))
    missing = [5, 8]
    assert looks_like_season_split(season_episodes, missing) is False


def test_looks_like_season_split_false_for_small_seasons():
    # Una temporada normal de 10 episodios a la que le faltan los ultimos 5
    # no es sospechosa de ser una "parte" de Netflix -- demasiado pequeña.
    season_episodes = list(range(1, 11))
    missing = [6, 7, 8, 9, 10]
    assert looks_like_season_split(season_episodes, missing) is False


def test_looks_like_season_split_false_when_first_half_incomplete():
    # Si tambien falta algo de la primera mitad, no es un split limpio.
    season_episodes = list(range(1, 21))
    missing = [5] + list(range(11, 21))
    assert looks_like_season_split(season_episodes, missing) is False


def test_apply_season_split_filter_removes_high_confidence_split_seasons():
    # Caso real: (Des)encanto -- TMDB cuenta 20 episodios en la temporada 1
    # y 20 en la 2, pero el servidor los tiene organizados como Netflix los
    # lanzó (5 "Partes" de 10 episodios cada una) -- los 50 episodios están
    # completos, pero antes de este filtro se reportaban 20 como "que
    # faltan" (la segunda mitad de cada una de las dos primeras temporadas).
    expected = {1: list(range(1, 21)), 2: list(range(1, 21)), 3: list(range(1, 11))}
    missing = {1: list(range(11, 21)), 2: list(range(11, 21))}
    filtered, split_seasons = apply_season_split_filter(missing, expected)
    assert filtered == {}
    assert split_seasons == {1, 2}


def test_apply_season_split_filter_keeps_real_gaps_untouched():
    expected = {1: list(range(1, 21))}
    missing = {1: [5, 8]}   # huecos sueltos, no un patron de "partes"
    filtered, split_seasons = apply_season_split_filter(missing, expected)
    assert filtered == missing
    assert split_seasons == set()


def test_apply_season_split_filter_only_removes_the_matching_season():
    expected = {1: list(range(1, 21)), 2: list(range(1, 11))}
    missing = {1: list(range(11, 21)), 2: [3]}   # T1 es split, T2 es un hueco real
    filtered, split_seasons = apply_season_split_filter(missing, expected)
    assert filtered == {2: [3]}
    assert split_seasons == {1}


def test_find_unknown_seasons_detects_seasons_tmdb_does_not_know():
    # Anfibilandia/Amphibia: el servidor tiene temporadas 1, 2 y 3, pero el
    # ID de TMDB emparejado (posiblemente equivocado) solo conoce la 1.
    present = {(1, 1), (1, 2), (2, 1), (3, 1)}
    expected_seasons = [1]
    assert find_unknown_seasons(present, expected_seasons) == {2, 3}


def test_find_unknown_seasons_empty_when_all_seasons_known():
    present = {(1, 1), (1, 2), (2, 1)}
    expected_seasons = [1, 2]
    assert find_unknown_seasons(present, expected_seasons) == set()


def test_find_unknown_seasons_empty_when_present_is_empty():
    assert find_unknown_seasons(set(), [1, 2]) == set()


def test_looks_like_absolute_numbering_detects_naruto_style():
    # Naruto Shippuden: TMDB tiene varias temporadas, pero el servidor
    # reporta todo bajo "temporada 1" con episodios numerados de corrido
    # muy por encima de lo que esa temporada debería tener.
    tmdb_season_counts = {1: 10, 2: 10, 3: 10}
    present = {(1, n) for n in range(1, 26)}
    assert looks_like_absolute_numbering(tmdb_season_counts, present) is True


def test_looks_like_absolute_numbering_false_for_normal_per_season_numbering():
    tmdb_season_counts = {1: 10, 2: 10, 3: 10}
    present = {(1, n) for n in range(1, 11)} | {(2, n) for n in range(1, 11)} | {(3, n) for n in range(1, 6)}
    assert looks_like_absolute_numbering(tmdb_season_counts, present) is False


def test_looks_like_absolute_numbering_false_for_single_season_show():
    tmdb_season_counts = {1: 20}
    present = {(1, n) for n in range(1, 21)}
    assert looks_like_absolute_numbering(tmdb_season_counts, present) is False


def test_looks_like_absolute_numbering_false_when_present_empty():
    assert looks_like_absolute_numbering({1: 10, 2: 10}, set()) is False


def test_remap_absolute_episodes_converts_to_per_season():
    tmdb_season_counts = {1: 10, 2: 10, 3: 10}
    present = {(1, n) for n in range(1, 26)}   # numeracion absoluta 1..25, todo bajo "temporada 1"
    remapped = remap_absolute_episodes(tmdb_season_counts, present)
    expected = ({(1, n) for n in range(1, 11)}
                | {(2, n) for n in range(1, 11)}
                | {(3, n) for n in range(1, 6)})
    assert remapped == expected


def _missing_row(tmdb_id=1, name="Serie X", missing=None, unknown_seasons=None):
    missing = missing if missing is not None else {1: [2, 3]}
    return {"tmdb_id": tmdb_id, "name": name, "missing": missing,
            "summary": format_missing_summary(name, missing),
            "unknown_seasons": unknown_seasons or set()}


def test_remove_missing_episode_removes_just_that_episode():
    row = _missing_row(missing={1: [2, 3]})
    results = [row]
    assert remove_missing_episode(results, 1, 1, 2) is True
    assert row["missing"] == {1: [3]}
    assert row["summary"] == "Serie X: T1E03"


def test_remove_missing_episode_removes_season_when_empty():
    row = _missing_row(missing={1: [2], 2: [5]})
    results = [row]
    assert remove_missing_episode(results, 1, 1, 2) is True
    assert row["missing"] == {2: [5]}


def test_remove_missing_episode_drops_row_when_nothing_left_missing():
    row = _missing_row(missing={1: [2]})
    results = [row]
    assert remove_missing_episode(results, 1, 1, 2) is True
    assert results == []


def test_remove_missing_episode_keeps_row_if_unknown_seasons_remain():
    row = _missing_row(missing={1: [2]}, unknown_seasons={99})
    results = [row]
    assert remove_missing_episode(results, 1, 1, 2) is True
    assert results == [row]
    assert row["missing"] == {}


def test_remove_missing_episode_returns_false_when_not_actually_missing():
    row = _missing_row(missing={1: [2, 3]})
    results = [row]
    assert remove_missing_episode(results, 1, 1, 99) is False
    assert row["missing"] == {1: [2, 3]}   # sin tocar


def test_remove_missing_episode_returns_false_for_unknown_tmdb_id():
    results = [_missing_row(tmdb_id=1)]
    assert remove_missing_episode(results, 999, 1, 2) is False


def test_remove_series_removes_whole_row():
    row = _missing_row(tmdb_id=1, missing={1: [2, 3], 2: [5]})
    other = _missing_row(tmdb_id=2, missing={1: [1]})
    results = [row, other]
    assert remove_series(results, 1) is True
    assert results == [other]


def test_remove_series_returns_false_for_unknown_tmdb_id():
    results = [_missing_row(tmdb_id=1)]
    assert remove_series(results, 999) is False
    assert len(results) == 1


def test_format_missing_ranges_ignores_empty_season_list():
    """Caché corrupta (ver el arreglo real: una entrada con una temporada
    de lista vacía tumbaba el arranque de la app con IndexError) -- nunca
    debería llegar así, pero si llega no debe reventar."""
    assert format_missing_ranges({1: []}) == ""
    assert format_missing_ranges({1: [], 2: [3]}) == "T2E03"


def test_has_spanish_availability_true_when_es_region_present():
    providers = {"results": {"ES": {"flatrate": [{"provider_name": "Netflix"}]}, "US": {}}}
    assert has_spanish_availability(providers) is True


def test_has_spanish_availability_false_when_es_region_missing():
    providers = {"results": {"US": {}}}
    assert has_spanish_availability(providers) is False


def test_has_spanish_availability_false_when_no_results_key():
    assert has_spanish_availability({}) is False


def test_episode_has_spanish_text_true_with_overview():
    assert episode_has_spanish_text({"overview": "Un resumen en castellano.", "name": ""}) is True


def test_episode_has_spanish_text_true_with_only_name():
    assert episode_has_spanish_text({"overview": "", "name": "El principio"}) is True


def test_episode_has_spanish_text_false_when_both_empty():
    assert episode_has_spanish_text({"overview": "", "name": ""}) is False


def test_episode_has_spanish_text_false_when_only_whitespace():
    assert episode_has_spanish_text({"overview": "   ", "name": "\n"}) is False


def test_filter_missing_by_spanish_dub_hides_confirmed_non_dubbed():
    missing = {1: [1, 2, 3]}
    dub = {"1x01": True, "1x02": False, "1x03": True}
    assert filter_missing_by_spanish_dub(missing, dub) == {1: [1, 3]}


def test_filter_missing_by_spanish_dub_keeps_unchecked_episodes_visible():
    missing = {1: [1, 2]}
    dub = {"1x01": False}   # "1x02" todavía no se ha comprobado
    assert filter_missing_by_spanish_dub(missing, dub) == {1: [2]}


def test_filter_missing_by_spanish_dub_drops_season_if_all_hidden():
    missing = {1: [1], 2: [1]}
    dub = {"1x01": False, "2x01": True}
    assert filter_missing_by_spanish_dub(missing, dub) == {2: [1]}


def test_filter_missing_by_dub_cutoff_hides_episodes_past_cutoff():
    # Caso real: Bleach, doblado al castellano solo hasta el 1x109 de 366.
    missing = {1: [108, 109, 110, 111]}
    dub_cutoff = {1: 109}
    assert filter_missing_by_dub_cutoff(missing, dub_cutoff) == {1: [108, 109]}


def test_filter_missing_by_dub_cutoff_keeps_season_visible_without_verdict():
    missing = {1: [1, 2], 2: [1]}
    dub_cutoff = {1: 109}   # sin veredicto para la temporada 2
    assert filter_missing_by_dub_cutoff(missing, dub_cutoff) == {1: [1, 2], 2: [1]}


def test_filter_missing_by_dub_cutoff_drops_season_if_all_past_cutoff():
    missing = {1: [200], 2: [1]}
    dub_cutoff = {1: 109}
    assert filter_missing_by_dub_cutoff(missing, dub_cutoff) == {2: [1]}


def test_filter_missing_by_dub_cutoff_empty_cutoff_keeps_everything():
    missing = {1: [1, 2, 3]}
    assert filter_missing_by_dub_cutoff(missing, {}) == missing


def test_apply_ignored_filter_removes_ignored_episode():
    missing = {1: [1, 2, 3]}
    assert apply_ignored_filter(missing, [], {1: [2]}) == {1: [1, 3]}


def test_apply_ignored_filter_removes_whole_ignored_season():
    missing = {1: [1, 2], 2: [1]}
    assert apply_ignored_filter(missing, [1], {}) == {2: [1]}


def test_apply_ignored_filter_drops_season_if_all_episodes_ignored():
    missing = {1: [1, 2]}
    assert apply_ignored_filter(missing, [], {1: [1, 2]}) == {}


def test_apply_ignored_filter_ignored_season_wins_over_ignored_episode_of_same_season():
    # Una temporada ignorada entera no deja "huérfano" un episodio suelto
    # ignorado de esa misma temporada -- simplemente ya no aparece, sin
    # necesidad de que ambos coincidan.
    missing = {1: [1, 2, 3]}
    assert apply_ignored_filter(missing, [1], {1: [1]}) == {}


def test_apply_ignored_filter_no_ignored_anything_keeps_everything():
    missing = {1: [1, 2, 3]}
    assert apply_ignored_filter(missing, None, None) == missing


def test_apply_ignored_filter_does_not_mutate_input():
    missing = {1: [1, 2, 3]}
    original = {1: [1, 2, 3]}
    apply_ignored_filter(missing, [], {1: [2]})
    assert missing == original


def test_episode_has_spanish_translation_true_with_es():
    """Hay traducción real al español (cualquier región): se sigue con el
    chequeo de texto, no se descarta aquí."""
    payload = {"translations": [
        {"iso_639_1": "en", "iso_3166_1": "US", "name": "English"},
        {"iso_639_1": "es", "iso_3166_1": "ES", "name": "Español"},
    ]}
    assert episode_has_spanish_translation(payload) is True


def test_episode_has_spanish_translation_false_without_es():
    """Caso real (El Arca 3x01): TMDB devuelve texto en INGLÉS por fallback
    cuando no hay traducción, así que el chequeo de texto solo daba
    "doblado". La lista de traducciones no tiene fallback: sin "es", no
    hay ni texto, y se marca sin doblaje."""
    payload = {"translations": [
        {"iso_639_1": "en", "iso_3166_1": "US", "name": "English"},
        {"iso_639_1": "fr", "iso_3166_1": "FR", "name": "Français"},
    ]}
    assert episode_has_spanish_translation(payload) is False
    assert episode_has_spanish_translation({"translations": []}) is False
    assert episode_has_spanish_translation({}) is False
    assert episode_has_spanish_translation(None) is False


def test_eps_without_confirmed_dub_por_corte():
    """Con corte (IA o eldoblaje) para la temporada: avisa los episodios
    más allá del corte, incluidas temporadas en 0 (nada doblado)."""
    assert eps_without_confirmed_dub(3, [1, 2], dub_cutoff={3: 1}) == [2]
    assert eps_without_confirmed_dub(3, [1], dub_cutoff={3: 0}) == [1]
    assert eps_without_confirmed_dub(2, [1, 2], dub_cutoff={3: 0}) == []
    # Claves como texto tras recargar la caché de disco.
    assert eps_without_confirmed_dub(3, [2], dub_cutoff={"3": 1}) == [2]


def test_eps_without_confirmed_dub_por_tmdb():
    """Sin corte: solo los False explícitos de TMDB avisan; sin comprobar
    no se avisa (sin dato no se puede saber)."""
    dub = {"3x01": True, "3x02": False}
    assert eps_without_confirmed_dub(3, [1, 2], dub_episodes=dub) == [2]
    assert eps_without_confirmed_dub(3, [3], dub_episodes=dub) == []
    assert eps_without_confirmed_dub(3, [1], dub_episodes={}) == []
    assert eps_without_confirmed_dub(3, []) == []


def test_eps_without_confirmed_dub_corte_manda_sobre_tmdb():
    """El corte manda aunque TMDB diga True (falso positivo del texto)."""
    dub = {"3x01": True}
    assert eps_without_confirmed_dub(3, [1], dub_episodes=dub, dub_cutoff={3: 0}) == [1]


# ---- TMDB con numeración continua (no reinicia por temporada) ----

def _hxh_expected():
    """Hunter x Hunter 2011 real (TMDB): T1 1-62, T2 63-136, T3 137-148."""
    return {1: list(range(1, 63)), 2: list(range(63, 137)), 3: list(range(137, 149))}


def test_tmdb_season_episode_offsets_hxh():
    assert tmdb_season_episode_offsets(_hxh_expected()) == {1: 0, 2: 62, 3: 136}


def test_tmdb_season_episode_offsets_normal_es_cero():
    assert tmdb_season_episode_offsets({1: [1, 2, 3], 2: [1, 2]}) == {1: 0, 2: 0}
    assert tmdb_season_episode_offsets({}) == {}
    # Datos raros (primer número 0): ante la duda no se toca nada.
    assert tmdb_season_episode_offsets({1: [0, 1, 2]}) == {1: 0}


def test_normalize_tmdb_season_numbering_hxh():
    expected = _hxh_expected()
    titles = {s: {e: f"T{s}E{e}" for e in eps} for s, eps in expected.items()}
    new_expected, new_titles, new_dates, offsets = normalize_tmdb_season_numbering(
        expected, titles, None)
    assert offsets == {1: 0, 2: 62, 3: 136}
    assert new_expected[1] == list(range(1, 63))
    assert new_expected[2] == list(range(1, 75))
    assert new_expected[3] == list(range(1, 13))
    # Los títulos viajan con su episodio: el TMDB S2E63 es el 2x01.
    assert new_titles[2][1] == "T2E63"
    assert new_titles[3][12] == "T3E148"
    assert new_dates == {}


def test_normalize_tmdb_season_numbering_normal_es_vacio():
    expected = {1: [1, 2, 3], 2: [1, 2]}
    new_expected, new_titles, new_dates, offsets = normalize_tmdb_season_numbering(expected)
    assert offsets == {1: 0, 2: 0}
    assert new_expected == expected


def test_hxh_completa_en_numeracion_usuario_no_falta_nada():
    """La serie entera con numeración por temporada (como la tiene el
    usuario) contra TMDB normalizado: sin huecos."""
    expected, _, _, _ = normalize_tmdb_season_numbering(_hxh_expected())
    present = {(1, e) for e in range(1, 63)} | {(2, e) for e in range(1, 75)} \
        | {(3, e) for e in range(1, 13)}
    assert find_missing_episodes(expected, present) == {}


def test_hxh_detecta_hueco_real_en_numeracion_usuario():
    """Falta el 2x05 del usuario (= TMDB S2E67): se reporta como 2x05,
    que es lo que hay que buscar/descargar."""
    expected, _, _, _ = normalize_tmdb_season_numbering(_hxh_expected())
    present = {(1, e) for e in range(1, 63)} | {(2, e) for e in range(1, 75) if e != 5} \
        | {(3, e) for e in range(1, 13)}
    assert find_missing_episodes(expected, present) == {2: [5]}


def test_hxh_sin_normalizar_daria_falsos_faltan():
    """Sin normalizar, la misma serie completa saldría con decenas de
    falsos huecos (del 75 al 136 y la T3 entera: el solape 63-74 entre
    ambas numeraciones es casualidad)."""
    missing = find_missing_episodes(_hxh_expected(),
                                    {(1, e) for e in range(1, 63)}
                                    | {(2, e) for e in range(1, 75)}
                                    | {(3, e) for e in range(1, 13)})
    assert missing[2] == list(range(75, 137))
    assert missing[3] == list(range(137, 149))
