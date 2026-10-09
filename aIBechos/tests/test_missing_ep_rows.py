"""Tests de core/missing_ep_rows.py -- la lógica de filas/filtros de
"Episodios que faltan" que comparten la interfaz Tk y la Qt."""

from core import missing_ep_rows as mer


def _cache():
    return {
        "_meta": {"last_scan_ts": 1},
        "10": {"name": "Bleach", "missing": {"1": [3, 4], "2": [1]},
               "expected": {"1": [1, 2, 3, 4], "2": [1, 2]},
               "episode_titles": {"1": {"3": "Tres"}},
               "ignored_seasons": [], "ignored_episodes": {"1": [4]},
               "first_air_date": "2004-10-05",
               "ai_verdict": {"veredicto": "hueco_real", "doblaje_castellano": {"1": 3}}},
        "20": {"name": "Completa", "missing": {}, "present_season_counts": {"1": 10}},
        "30": {"name": "Rara", "missing": {}, "unknown_seasons": [5]},
    }


def test_rows_from_cache_splits_complete_and_incomplete():
    incomplete = mer.rows_from_cache(_cache(), complete=False)
    complete = mer.rows_from_cache(_cache(), complete=True)
    assert sorted(r["name"] for r in incomplete) == ["Bleach", "Rara"]
    assert [r["name"] for r in complete] == ["Completa"]
    bleach = next(r for r in incomplete if r["tmdb_id"] == 10)
    assert bleach["missing"] == {1: [3, 4], 2: [1]}
    assert bleach["ignored_episodes"] == {1: {4}}
    # claves del veredicto reconvertidas a int
    assert bleach["ai_verdict"]["doblaje_castellano"] == {1: 3}


def test_complete_count_in_cache():
    assert mer.complete_count_in_cache(_cache()) == 1


def test_visible_row_applies_ignored_episode_filter():
    rows = mer.rows_from_cache(_cache(), complete=False)
    bleach = next(r for r in rows if r["tmdb_id"] == 10)
    shown = mer.visible_row(bleach, mer.MissingEpFilters(), {})
    assert shown["missing"] == {1: [3], 2: [1]}
    # con "Mostrar ignoradas" la fila sale tal cual
    assert mer.visible_row(bleach, mer.MissingEpFilters(show_ignored=True), {}) is bleach


def test_visible_row_hides_complete_unless_switch_off():
    complete = mer.rows_from_cache(_cache(), complete=True)[0]
    assert mer.visible_row(complete, mer.MissingEpFilters(hide_complete=True), {}) is None
    assert mer.visible_row(complete, mer.MissingEpFilters(hide_complete=False), {}) is complete


def test_visible_row_query_and_ignored_series():
    rows = mer.rows_from_cache(_cache(), complete=False)
    bleach = next(r for r in rows if r["tmdb_id"] == 10)
    assert mer.visible_row(bleach, mer.MissingEpFilters(query="naruto"), {}) is None
    assert mer.visible_row(bleach, mer.MissingEpFilters(query="BLE"), {}) is not None
    bleach["ignored"] = True
    assert mer.visible_row(bleach, mer.MissingEpFilters(), {}) is None


def test_visible_row_dub_filter_uses_ai_cutoff():
    rows = mer.rows_from_cache(_cache(), complete=False)
    bleach = next(r for r in rows if r["tmdb_id"] == 10)
    shown = mer.visible_row(bleach, mer.MissingEpFilters(hide_no_dub=True), {})
    # IA: T1 doblada hasta el 3 -> 1x03 sí; 1x04 ignorado; T2 sin corte -> oculta
    assert shown["missing"] == {1: [3]}


def test_merge_all_rows_dedupes_complete_rows():
    results = [{"tmdb_id": 1, "name": "A"}]
    complete = [{"tmdb_id": "1", "name": "A"}, {"tmdb_id": 2, "name": "B"}]
    assert [r["tmdb_id"] for r in mer.merge_all_rows(results, complete, hide_complete=False)] == [1, 2]
    assert mer.merge_all_rows(results, complete, hide_complete=True) == results


def test_merged_dub_cut_takes_minimum():
    assert mer.merged_dub_cut({1: 10}, {"1": 7}, 1) == 7
    assert mer.merged_dub_cut({}, {}, 1) is None


def test_episode_lines_uses_template():
    rows = mer.rows_from_cache(_cache(), complete=False)
    bleach = next(r for r in rows if r["tmdb_id"] == 10)
    lines = mer.episode_lines(bleach, "{serie} {temporada}x{episodio:02d} {titulo}{ext}")
    assert lines[0][:4] == (1, 3, "Tres", "Bleach 1x03 Tres")
    assert len(lines) == 3


def test_row_summary_tones():
    rows = mer.rows_from_cache(_cache(), complete=False)
    rara = next(r for r in rows if r["tmdb_id"] == 30)
    text, tone, _ = mer.row_summary(rara, {})
    assert text.startswith("Posible ID equivocado") and tone == "warning"
    complete = mer.rows_from_cache(_cache(), complete=True)[0]
    text, tone, _ = mer.row_summary(complete, {})
    assert text == "Completa (10 episodios)" and tone == "success"


def test_ignore_setters_roundtrip():
    cache = _cache()
    assert mer.set_episode_ignored(cache, 10, 1, 3, True)
    assert cache["10"]["ignored_episodes"] == {"1": [3, 4]}
    assert mer.set_episode_ignored(cache, 10, 1, 3, False)
    assert mer.set_episode_ignored(cache, 10, 1, 4, False)
    assert cache["10"]["ignored_episodes"] == {}
    assert mer.set_season_ignored(cache, 10, 2, True)
    assert cache["10"]["ignored_seasons"] == [2]
    assert not mer.set_series_ignored(cache, 999, True)


def test_season_header_text():
    assert mer.season_header_text(2, 5, "2020-01-31") == "Temporada 2 (5 episodios) -- estrenada 31/01/2020"
    assert mer.season_header_text(1, 1, "", arrow=">") == "> Temporada 1 (1 episodios)"
