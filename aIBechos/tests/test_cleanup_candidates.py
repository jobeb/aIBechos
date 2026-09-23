import time

from core.cleanup_candidates import (
    CleanupItem, CleanupFilters, matches_filters, filter_candidates,
    WATCHED_NEVER, WATCHED_NOT_REWATCHED, WATCHED_LOW_PLAYCOUNT, WATCHED_NO_DATA,
    group_loose_files_by_name, merge_usage_entries, find_duplicate_tmdb_ids,
)

NOW = 1_700_000_000.0
MONTH = 30 * 24 * 3600


def _item(**kw):
    base = dict(tmdb_id=1, name="X", media_type="movie", ftp_path="/datos/peliculas/X",
                category_name="Películas")
    base.update(kw)
    return CleanupItem(**base)


def test_no_filters_active_matches_everything():
    item = _item()
    assert matches_filters(item, CleanupFilters(), now=NOW) is True


def test_min_age_months_excludes_recent_items():
    recent = _item(date_added_ts=NOW - 2 * MONTH)
    old = _item(date_added_ts=NOW - 20 * MONTH)
    filters = CleanupFilters(min_age_months=12)
    assert matches_filters(recent, filters, now=NOW) is False
    assert matches_filters(old, filters, now=NOW) is True


def test_min_age_months_excludes_items_without_date():
    item = _item(date_added_ts=None)
    filters = CleanupFilters(min_age_months=12)
    assert matches_filters(item, filters, now=NOW) is False


def test_min_size_gb_filters_by_size():
    small = _item(size_bytes=1 * 1024 ** 3)
    big = _item(size_bytes=10 * 1024 ** 3)
    filters = CleanupFilters(min_size_gb=5)
    assert matches_filters(small, filters, now=NOW) is False
    assert matches_filters(big, filters, now=NOW) is True


def test_watched_never_excludes_watched_items():
    never = _item(play_count=0, fully_watched=False)
    watched = _item(play_count=3, fully_watched=True)
    filters = CleanupFilters(watched_mode=WATCHED_NEVER)
    assert matches_filters(never, filters, now=NOW) is True
    assert matches_filters(watched, filters, now=NOW) is False


def test_watched_not_rewatched_requires_fully_watched_and_old_last_played():
    recently_rewatched = _item(fully_watched=True, last_played_ts=NOW - 1 * MONTH)
    long_ago = _item(fully_watched=True, last_played_ts=NOW - 20 * MONTH)
    not_watched_at_all = _item(fully_watched=False, last_played_ts=None)
    filters = CleanupFilters(watched_mode=WATCHED_NOT_REWATCHED, not_rewatched_months=12)
    assert matches_filters(recently_rewatched, filters, now=NOW) is False
    assert matches_filters(long_ago, filters, now=NOW) is True
    assert matches_filters(not_watched_at_all, filters, now=NOW) is False


def test_watched_low_playcount_filters_by_threshold():
    low = _item(play_count=1)
    high = _item(play_count=10)
    filters = CleanupFilters(watched_mode=WATCHED_LOW_PLAYCOUNT, max_play_count=2)
    assert matches_filters(low, filters, now=NOW) is True
    assert matches_filters(high, filters, now=NOW) is False


def test_media_type_filter():
    movie = _item(media_type="movie")
    show = _item(media_type="tv")
    filters = CleanupFilters(media_types={"tv"})
    assert matches_filters(movie, filters, now=NOW) is False
    assert matches_filters(show, filters, now=NOW) is True


def test_category_name_filter():
    peques = _item(category_name="SeriesPeques")
    normal = _item(category_name="Series")
    filters = CleanupFilters(category_names={"Series"})
    assert matches_filters(peques, filters, now=NOW) is False
    assert matches_filters(normal, filters, now=NOW) is True


def test_media_type_empty_set_excludes_everything():
    # Desmarcar TODOS los tipos (ni Series ni Peliculas) debe excluir
    # todo -- un conjunto vacio no es lo mismo que None (sin filtro).
    movie = _item(media_type="movie")
    show = _item(media_type="tv")
    filters = CleanupFilters(media_types=set())
    assert matches_filters(movie, filters, now=NOW) is False
    assert matches_filters(show, filters, now=NOW) is False


def test_category_names_empty_set_excludes_everything():
    item = _item(category_name="Series")
    filters = CleanupFilters(category_names=set())
    assert matches_filters(item, filters, now=NOW) is False


def test_filters_combine_with_and_logic():
    # Cumple tamaño y antigüedad, pero NO el criterio de visionado -> debe excluirse
    item = _item(size_bytes=10 * 1024 ** 3, date_added_ts=NOW - 20 * MONTH,
                 fully_watched=False, play_count=5)
    filters = CleanupFilters(min_size_gb=5, min_age_months=12, watched_mode=WATCHED_NEVER)
    assert matches_filters(item, filters, now=NOW) is False


def test_filter_candidates_returns_only_matching_items():
    items = [
        _item(name="A", size_bytes=10 * 1024 ** 3),
        _item(name="B", size_bytes=1 * 1024 ** 3),
        _item(name="C", size_bytes=8 * 1024 ** 3),
    ]
    result = filter_candidates(items, CleanupFilters(min_size_gb=5), now=NOW)
    assert [it.name for it in result] == ["A", "C"]


# ── Agrupar peliculas guardadas como archivos sueltos (sin carpeta propia) ──

def test_group_loose_files_groups_video_and_companions():
    files = [
        ("A Pleno Sol (1960).mkv", 4_000_000_000),
        ("A Pleno Sol (1960)-poster.jpg", 100_000),
        ("A Pleno Sol (1960)-backdrop.jpg", 200_000),
        ("A Pleno Sol (1960).nfo", 500),
    ]
    groups = group_loose_files_by_name(files)
    assert list(groups.keys()) == ["A Pleno Sol (1960)"]
    g = groups["A Pleno Sol (1960)"]
    assert g["size_bytes"] == 4_000_000_000 + 100_000 + 200_000 + 500
    assert sorted(g["file_names"]) == sorted(f[0] for f in files)


def test_group_loose_files_keeps_separate_movies_separate():
    files = [
        ("Movie A (2020).mkv", 1000),
        ("Movie A (2020)-poster.jpg", 10),
        ("Movie B (2021).avi", 2000),
        ("Movie B (2021)-poster.jpg", 20),
    ]
    groups = group_loose_files_by_name(files)
    assert set(groups.keys()) == {"Movie A (2020)", "Movie B (2021)"}
    assert groups["Movie A (2020)"]["size_bytes"] == 1010
    assert groups["Movie B (2021)"]["size_bytes"] == 2020


def test_group_loose_files_discards_orphan_companions_without_video():
    files = [
        ("Huerfano-poster.jpg", 100),
        ("Huerfano.nfo", 50),
    ]
    groups = group_loose_files_by_name(files)
    assert groups == {}


def test_group_loose_files_ignores_unrelated_non_video_files():
    files = [("readme.txt", 10), ("cover.jpg", 20)]
    assert group_loose_files_by_name(files) == {}


# ── Fusionar datos de uso de Jellyfin + Plex para el mismo titulo ──────────

def test_merge_usage_entries_watched_if_either_source_says_so():
    jellyfin = {"name": "X", "fully_watched": False, "play_count": 0}
    plex = {"name": "X", "fully_watched": True, "play_count": 3}
    merged = merge_usage_entries(jellyfin, plex)
    assert merged["fully_watched"] is True


def test_merge_usage_entries_sums_play_counts():
    a = {"name": "X", "play_count": 2}
    b = {"name": "X", "play_count": 5}
    assert merge_usage_entries(a, b)["play_count"] == 7


def test_merge_usage_entries_takes_most_recent_last_played():
    a = {"name": "X", "last_played": "2024-01-01T00:00:00Z"}
    b = {"name": "X", "last_played": "2024-06-01T00:00:00Z"}
    merged = merge_usage_entries(a, b)
    import datetime
    assert merged["last_played"] == datetime.datetime.fromisoformat("2024-06-01T00:00:00+00:00").timestamp()


def test_merge_usage_entries_takes_earliest_date_added():
    a = {"name": "X", "date_added": "2024-06-01T00:00:00Z"}
    b = {"name": "X", "date_added": "2023-01-01T00:00:00Z"}
    merged = merge_usage_entries(a, b)
    import datetime
    assert merged["date_added"] == datetime.datetime.fromisoformat("2023-01-01T00:00:00+00:00").timestamp()


def test_merge_usage_entries_fills_missing_tmdb_id_and_size_from_either_source():
    a = {"name": "X", "tmdb_id": None, "size_bytes": 0}
    b = {"name": "X", "tmdb_id": 555, "size_bytes": 12345}
    merged = merge_usage_entries(a, b)
    assert merged["tmdb_id"] == 555
    assert merged["size_bytes"] == 12345


def test_merge_usage_entries_takes_year_from_either_source():
    a = {"name": "X", "year": ""}
    b = {"name": "X", "year": "2020"}
    assert merge_usage_entries(a, b)["year"] == "2020"
    assert merge_usage_entries(b, a)["year"] == "2020"
    assert merge_usage_entries(a, {"name": "X"})["year"] == ""


def test_merge_usage_entries_prefers_first_source_for_source_and_server_id():
    """El llamador (gui/app.py::_scan_cleanup_candidates) procesa Jellyfin
    antes que Plex -- por eso "a" gana aquí, respetando la preferencia
    "Jellyfin primero" ya establecida para abrir en el servidor de medios."""
    jellyfin = {"name": "X", "source": "jellyfin", "server_id": "jf-123"}
    plex = {"name": "X", "source": "plex", "server_id": "plex-456"}
    merged = merge_usage_entries(jellyfin, plex)
    assert merged["source"] == "jellyfin"
    assert merged["server_id"] == "jf-123"


def test_merge_usage_entries_falls_back_to_second_source_when_first_has_none():
    only_plex = {"name": "X"}
    plex = {"name": "X", "source": "plex", "server_id": "plex-456"}
    merged = merge_usage_entries(only_plex, plex)
    assert merged["source"] == "plex"
    assert merged["server_id"] == "plex-456"


# ── Busqueda por nombre ─────────────────────────────────────────────────────

def test_name_query_filters_by_substring_case_insensitive():
    item = _item(name="Breaking Bad")
    other = _item(name="Better Call Saul")
    filters = CleanupFilters(name_query="breaking")
    assert matches_filters(item, filters, now=NOW) is True
    assert matches_filters(other, filters, now=NOW) is False


def test_name_query_empty_does_not_filter():
    item = _item(name="Cualquier Cosa")
    filters = CleanupFilters(name_query="")
    assert matches_filters(item, filters, now=NOW) is True


# ── Sin datos de visionado ──────────────────────────────────────────────────

def test_watched_no_data_matches_only_items_without_tmdb_match():
    unmatched = _item(tmdb_id=None)
    matched = _item(tmdb_id=42, fully_watched=False, play_count=0)
    filters = CleanupFilters(watched_mode=WATCHED_NO_DATA)
    assert matches_filters(unmatched, filters, now=NOW) is True
    assert matches_filters(matched, filters, now=NOW) is False


# ── Duplicados (mismo tmdb_id en varias candidatas) ────────────────────────

def test_find_duplicate_tmdb_ids_detects_repeated_ids():
    items = [
        _item(name="A", tmdb_id=1),
        _item(name="A copia", tmdb_id=1),
        _item(name="B", tmdb_id=2),
        _item(name="C", tmdb_id=None),
    ]
    assert find_duplicate_tmdb_ids(items) == {(1, "movie")}


def test_find_duplicate_tmdb_ids_does_not_confuse_movie_and_tv_sharing_same_id():
    # TMDB numera series y peliculas en espacios INDEPENDIENTES -- una
    # serie y una pelicula pueden compartir el mismo numero de tmdb_id
    # por pura casualidad sin ser el mismo contenido (visto de verdad:
    # "Dragon Ball" (serie) y "Socorro, soy un pez" (pelicula) con el
    # mismo numero, tratadas como duplicadas sin tener nada que ver).
    items = [
        _item(name="Dragon Ball", tmdb_id=42, media_type="tv"),
        _item(name="Socorro, soy un pez", tmdb_id=42, media_type="movie"),
    ]
    assert find_duplicate_tmdb_ids(items) == set()


def test_only_duplicates_filter_shows_only_repeated_items():
    a1 = _item(name="A", tmdb_id=1)
    a2 = _item(name="A copia", tmdb_id=1)
    b = _item(name="B", tmdb_id=2)
    no_match = _item(name="C", tmdb_id=None)
    items = [a1, a2, b, no_match]

    result = filter_candidates(items, CleanupFilters(only_duplicates=True), now=NOW)
    assert {it.name for it in result} == {"A", "A copia"}


def test_only_duplicates_with_no_duplicates_returns_empty():
    items = [_item(name="A", tmdb_id=1), _item(name="B", tmdb_id=2)]
    result = filter_candidates(items, CleanupFilters(only_duplicates=True), now=NOW)
    assert result == []
