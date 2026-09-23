from core.category_upload_stats import (
    add_category_upload, load_local_cache, save_local_cache,
)
from core.upload_stats import top_uploaders


def test_add_category_upload_returns_new_dict_without_mutating_original():
    original = {}
    result = add_category_upload(original, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    assert original == {}
    assert "cat-tv" in result


def test_add_category_upload_stores_category_name_and_uploader():
    result = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    assert result["cat-tv"]["category_name"] == "Series"
    assert result["cat-tv"]["uploaders"]["jose"] == {
        "display_name": "Jose", "total_bytes": 1000, "total_files": 1,
        "first_upload_ts": 1000.0, "last_upload_ts": 1000.0,
    }


def test_add_category_upload_accumulates_same_person_same_category():
    data = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    data = add_category_upload(data, "cat-tv", "Series", "Jose", 2000, ts=2000.0)
    assert data["cat-tv"]["uploaders"]["jose"]["total_bytes"] == 3000
    assert data["cat-tv"]["uploaders"]["jose"]["total_files"] == 2


def test_add_category_upload_keeps_categories_separate():
    data = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    data = add_category_upload(data, "cat-movie", "Peliculas", "Jose", 5000, ts=1000.0)
    assert data["cat-tv"]["uploaders"]["jose"]["total_bytes"] == 1000
    assert data["cat-movie"]["uploaders"]["jose"]["total_bytes"] == 5000


def test_add_category_upload_keeps_people_within_a_category_separate():
    data = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    data = add_category_upload(data, "cat-tv", "Series", "Ana", 2000, ts=1000.0)
    assert data["cat-tv"]["uploaders"]["jose"]["total_bytes"] == 1000
    assert data["cat-tv"]["uploaders"]["ana"]["total_bytes"] == 2000


def test_add_category_upload_groups_accents_and_capitalization():
    data = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    data = add_category_upload(data, "cat-tv", "Series", "José", 500, ts=2000.0)
    assert len(data["cat-tv"]["uploaders"]) == 1
    assert data["cat-tv"]["uploaders"]["jose"]["total_bytes"] == 1500


def test_top_uploaders_works_on_the_nested_uploaders_dict():
    # top_uploaders() de core.upload_stats se reutiliza tal cual sobre
    # data[category_id]["uploaders"] -- sin ninguna funcion nueva para
    # ordenar/limitar aqui.
    data = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)
    data = add_category_upload(data, "cat-tv", "Series", "Ana", 5000, ts=1000.0)
    top = top_uploaders(data["cat-tv"]["uploaders"])
    assert [e["display_name"] for e in top] == ["Ana", "Jose"]


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.category_upload_stats.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.category_upload_stats.app_data_dir", lambda: tmp_path)
    data = add_category_upload({}, "cat-tv", "Series", "Jose", 1000, ts=1000.0)

    save_local_cache(data)

    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.category_upload_stats.app_data_dir", lambda: tmp_path)
    (tmp_path / "category_upload_stats.json").write_text("{esto no es json", encoding="utf-8")
    assert load_local_cache() == {}
