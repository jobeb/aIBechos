from core.favorites import (
    add_favorite, remove_favorite, is_favorite, favorite_info,
    load_local_cache, save_local_cache,
)


def test_add_favorite_returns_new_dict_without_mutating_original():
    original = {}
    result = add_favorite(original, "tv", 1234, "Breaking Bad")
    assert original == {}   # no mutado
    assert is_favorite(result, "tv", 1234) is True


def test_add_favorite_stores_expected_fields():
    result = add_favorite({}, "tv", 1234, "Breaking Bad")
    assert result["tv:1234"] == {"media_type": "tv", "tmdb_id": 1234, "name": "Breaking Bad",
                                 "added_by": "", "added_at": 0}


def test_add_favorite_stores_who_and_when():
    result = add_favorite({}, "tv", 1234, "Breaking Bad", by="Jose", at=1789700000)
    assert result["tv:1234"]["added_by"] == "Jose"
    assert result["tv:1234"]["added_at"] == 1789700000


def test_favorite_info_returns_entry_or_empty():
    data = add_favorite({}, "tv", 1234, "Breaking Bad", by="Jose", at=1789700000)
    assert favorite_info(data, "tv", 1234)["added_by"] == "Jose"
    assert favorite_info(data, "tv", 9999) == {}
    assert favorite_info({"tv:1234": "basura"}, "tv", 1234) == {}


def test_legacy_favorite_without_attribution_still_counts():
    data = {"tv:1234": {"media_type": "tv", "tmdb_id": 1234, "name": "Breaking Bad"}}
    assert is_favorite(data, "tv", 1234) is True
    assert favorite_info(data, "tv", 1234).get("added_by", "") == ""


def test_remove_favorite_returns_new_dict_without_the_key():
    data = add_favorite({}, "movie", 42, "Inception")
    result = remove_favorite(data, "movie", 42)
    assert is_favorite(data, "movie", 42) is True    # original intacto
    assert is_favorite(result, "movie", 42) is False


def test_remove_favorite_is_a_no_op_when_not_present():
    result = remove_favorite({}, "movie", 999)
    assert result == {}


def test_is_favorite_distinguishes_media_type_with_same_tmdb_id():
    data = add_favorite({}, "tv", 1234, "Serie")
    assert is_favorite(data, "tv", 1234) is True
    assert is_favorite(data, "movie", 1234) is False


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.favorites.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.favorites.app_data_dir", lambda: tmp_path)
    data = add_favorite({}, "tv", 1234, "Breaking Bad")

    save_local_cache(data)

    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.favorites.app_data_dir", lambda: tmp_path)
    (tmp_path / "favorites.json").write_text("{esto no es json", encoding="utf-8")
    assert load_local_cache() == {}
