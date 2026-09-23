import core.missing_movies_cache as mmc


def _entry(title="Dune", in_server=False, watch=None, certification=""):
    return {"media_type": "movie", "title": title, "year": "2024", "release_date": "2024-05-01",
            "overview": "Sinopsis", "poster_url": "https://x/p.jpg",
            "genres": ["Acción"], "genre_ids": [28], "vote_average": 7.0,
            "popularity": 10.0, "list": "trending", "in_server": in_server,
            "watch": watch or {}, "certification": certification}


def _tv_entry(title="Serie", in_server=False):
    return {"media_type": "tv", "title": title, "year": "2024", "release_date": "2024-05-01",
            "overview": "Sinopsis", "poster_url": "https://x/p.jpg",
            "genres": ["Drama"], "genre_ids": [18], "vote_average": 8.0,
            "popularity": 9.0, "list": "trending", "in_server": in_server,
            "watch": {}, "certification": ""}


def test_mark_movie_in_server_sets_flag_and_reports_change():
    cache = {"movie:123": _entry(in_server=False)}
    assert mmc.mark_movie_in_server(cache, 123) is True
    assert cache["movie:123"]["in_server"] is True


def test_mark_movie_in_server_idempotent_when_already_marked():
    cache = {"movie:123": _entry(in_server=True)}
    assert mmc.mark_movie_in_server(cache, 123) is False
    assert cache["movie:123"]["in_server"] is True


def test_mark_movie_in_server_false_for_unknown_id():
    cache = {}
    assert mmc.mark_movie_in_server(cache, 999) is False


def test_mark_tv_in_server_uses_tv_key_and_does_not_touch_movie_with_same_id():
    # TMDB numera películas y series por separado: el mismo tmdb_id puede ser
    # las dos cosas, y marcar una no debe tocar la otra.
    cache = {"movie:123": _entry(in_server=False), "tv:123": _tv_entry(in_server=False)}
    assert mmc.mark_movie_in_server(cache, 123, media_type="tv") is True
    assert cache["tv:123"]["in_server"] is True
    assert cache["movie:123"]["in_server"] is False


def test_remove_movie_from_cache_uses_media_type_key():
    cache = {"movie:123": _entry(), "tv:123": _tv_entry()}
    assert mmc.remove_movie_from_cache(cache, 123, media_type="tv") is True
    assert "tv:123" not in cache
    assert "movie:123" in cache


def test_cache_key_and_parse_roundtrip():
    assert mmc.cache_key("movie", 123) == "movie:123"
    assert mmc.cache_key("tv", 456) == "tv:456"
    assert mmc.parse_cache_key("movie:123") == ("movie", 123)
    assert mmc.parse_cache_key("tv:456") == ("tv", 456)
    assert mmc.parse_cache_key("_meta") is None
    assert mmc.parse_cache_key("123") is None


def test_normalize_cache_migrates_legacy_numeric_keys_to_movie():
    cache = {"123": _entry("Dune"), "tv:456": _tv_entry(), "_meta": {"last_scan_ts": 1.0}}
    result = mmc.normalize_cache(cache)
    assert "movie:123" in result
    assert "123" not in result
    assert result["movie:123"]["title"] == "Dune"
    assert result["movie:123"]["media_type"] == "movie"
    assert "tv:456" in result
    assert result["_meta"] == {"last_scan_ts": 1.0}


def test_merge_movies_cache_keeps_entries_present_in_only_one_side():
    local = {"movie:123": _entry("Dune")}
    remote = {"movie:456": _entry("Alien", in_server=True)}
    result = mmc.merge_movies_cache(local, remote)
    assert set(result) == {"movie:123", "movie:456"}
    assert result["movie:123"]["title"] == "Dune"
    assert result["movie:456"]["in_server"] is True


def test_merge_movies_cache_prefers_local_data_but_or_in_server():
    local = {"movie:123": _entry("Dune", in_server=False, watch={"flatrate": ["Netflix"]})}
    remote = {"movie:123": _entry("Dune (remoto)", in_server=True, watch={"rent": ["Amazon"]})}
    result = mmc.merge_movies_cache(local, remote)
    assert result["movie:123"]["title"] == "Dune"            # dato objetivo -- local gana
    assert result["movie:123"]["in_server"] is True          # OR: remoto dice en-servidor
    assert result["movie:123"]["watch"] == {"flatrate": ["Netflix"]}   # local ya tenía datos


def test_merge_movies_cache_fills_empty_local_fields_from_remote():
    local = {"movie:123": _entry("Dune", in_server=True, certification="")}
    remote = {"movie:123": _entry("Dune", in_server=False, certification="12")}
    result = mmc.merge_movies_cache(local, remote)
    assert result["movie:123"]["certification"] == "12"      # hueco local rellenado del remoto
    assert result["movie:123"]["in_server"] is True          # OR


def test_merge_movies_cache_normalizes_legacy_remote_keys():
    local = {"movie:123": _entry(in_server=False)}
    remote = {"123": _entry(in_server=True)}                 # formato antiguo
    result = mmc.merge_movies_cache(local, remote)
    assert "123" not in result
    assert result["movie:123"]["in_server"] is True          # OR tras normalizar


def test_merge_movies_cache_keeps_meta():
    local = {"_meta": {"last_scan_ts": 1.0}}
    remote = {}
    result = mmc.merge_movies_cache(local, remote)
    assert result["_meta"] == {"last_scan_ts": 1.0}


def test_merge_movies_cache_preserves_media_type_per_entry():
    local = {"movie:123": _entry("Dune")}
    remote = {"tv:123": _tv_entry("Serie")}
    result = mmc.merge_movies_cache(local, remote)
    assert result["movie:123"]["media_type"] == "movie"
    assert result["tv:123"]["media_type"] == "tv"


def test_apply_remote_in_server_propagates_flag():
    local = {"movie:123": _entry(in_server=False)}
    remote = {"movie:123": _entry(in_server=True)}
    result, changed = mmc.apply_remote_in_server(local, remote)
    assert changed is True
    assert result["movie:123"]["in_server"] is True


def test_apply_remote_in_server_does_not_remove_or_add_entries():
    local = {"movie:123": _entry(in_server=False)}
    remote = {"movie:456": _entry(in_server=True)}
    result, changed = mmc.apply_remote_in_server(local, remote)
    assert changed is False
    assert "movie:456" not in result      # no reintroduce películas que el local no tiene
    assert "movie:123" in result


def test_apply_remote_in_server_respects_local_dismissal():
    # El usuario descartó con 🚫 la 456: no está en la caché local. Aunque
    # otro cliente la tenga compartida como en-servidor, NO debe reaparecer.
    local = {"movie:123": _entry(in_server=False)}
    remote = {"movie:123": _entry(in_server=True), "movie:456": _entry(in_server=True)}
    result, changed = mmc.apply_remote_in_server(local, remote)
    assert changed is True
    assert "movie:456" not in result
    assert result["movie:123"]["in_server"] is True


def test_apply_remote_in_server_normalizes_legacy_remote_keys():
    local = {"movie:123": _entry(in_server=False)}
    remote = {"123": _entry(in_server=True)}                 # formato antiguo
    result, changed = mmc.apply_remote_in_server(local, remote)
    assert changed is True
    assert result["movie:123"]["in_server"] is True


def test_apply_remote_in_server_no_change_when_already_marked():
    local = {"movie:123": _entry(in_server=True)}
    remote = {"movie:123": _entry(in_server=True)}
    result, changed = mmc.apply_remote_in_server(local, remote)
    assert changed is False
    assert result["movie:123"]["in_server"] is True


def test_apply_remote_in_server_ignores_meta_and_non_dict_entries():
    local = {"_meta": {"last_scan_ts": 1.0}, "movie:123": _entry(in_server=False)}
    remote = {"_meta": {"last_scan_ts": 2.0}, "movie:123": "no es dict",
              "movie:456": _entry(in_server=True)}
    result, changed = mmc.apply_remote_in_server(local, remote)
    assert changed is False
    assert result["_meta"] == {"last_scan_ts": 1.0}
