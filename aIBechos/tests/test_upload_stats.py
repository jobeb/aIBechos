from core.upload_stats import (
    add_upload, top_uploaders, load_local_cache, save_local_cache,
)


def test_add_upload_returns_new_dict_without_mutating_original():
    original = {}
    result = add_upload(original, "Jose", 1000, ts=1000.0)
    assert original == {}   # no mutado
    assert "jose" in result


def test_add_upload_stores_expected_fields():
    result = add_upload({}, "Jose", 1000, ts=1000.0)
    assert result["jose"] == {
        "display_name": "Jose", "total_bytes": 1000, "total_files": 1,
        "first_upload_ts": 1000.0, "last_upload_ts": 1000.0,
    }


def test_add_upload_accumulates_for_same_person():
    data = add_upload({}, "Jose", 1000, ts=1000.0)
    data = add_upload(data, "Jose", 2000, ts=2000.0)
    assert data["jose"]["total_bytes"] == 3000
    assert data["jose"]["total_files"] == 2
    assert data["jose"]["first_upload_ts"] == 1000.0
    assert data["jose"]["last_upload_ts"] == 2000.0


def test_add_upload_groups_different_capitalizations_together():
    data = add_upload({}, "Jose", 1000, ts=1000.0)
    data = add_upload(data, "jose", 500, ts=2000.0)
    data = add_upload(data, "  JOSE  ", 500, ts=3000.0)
    assert len(data) == 1
    assert data["jose"]["total_bytes"] == 2000
    assert data["jose"]["total_files"] == 3


def test_add_upload_display_name_keeps_most_recent_spelling():
    data = add_upload({}, "jose", 1000, ts=1000.0)
    data = add_upload(data, "José", 1000, ts=2000.0)
    assert data["jose"]["display_name"] == "José"


def test_add_upload_does_not_touch_other_people():
    data = add_upload({}, "Jose", 1000, ts=1000.0)
    data = add_upload(data, "Ana", 2000, ts=1000.0)
    assert "jose" in data and "ana" in data
    assert data["jose"]["total_bytes"] == 1000
    assert data["ana"]["total_bytes"] == 2000


def test_add_upload_without_person_is_a_no_op():
    assert add_upload({}, "", 1000) == {}
    assert add_upload({}, "   ", 1000) == {}


def test_add_upload_uses_current_time_when_not_given():
    result = add_upload({}, "Jose", 1000)
    assert result["jose"]["first_upload_ts"] > 0


def test_top_uploaders_sorted_by_total_bytes_descending():
    data = add_upload({}, "Jose", 1000, ts=1000.0)
    data = add_upload(data, "Ana", 5000, ts=1000.0)
    data = add_upload(data, "Bea", 3000, ts=1000.0)
    top = top_uploaders(data)
    assert [e["display_name"] for e in top] == ["Ana", "Bea", "Jose"]


def test_top_uploaders_respects_limit():
    data = {}
    for i in range(15):
        data = add_upload(data, f"user{i}", (i + 1) * 100, ts=1000.0)
    assert len(top_uploaders(data, limit=10)) == 10
    assert len(top_uploaders(data, limit=3)) == 3


def test_top_uploaders_empty_data():
    assert top_uploaders({}) == []


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.upload_stats.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.upload_stats.app_data_dir", lambda: tmp_path)
    data = add_upload({}, "Jose", 1000, ts=1000.0)

    save_local_cache(data)

    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.upload_stats.app_data_dir", lambda: tmp_path)
    (tmp_path / "upload_stats.json").write_text("{esto no es json", encoding="utf-8")
    assert load_local_cache() == {}
