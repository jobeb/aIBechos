from core.auto_series import (
    add_auto_owner, remove_auto_owner, auto_owners, other_owners,
    transfer_auto_owner, remove_all_auto_by_owner,
    load_local_cache, save_local_cache,
)


def test_add_owner_no_mutates_and_keeps_others():
    original = {}
    result = add_auto_owner(original, "tv", 1234, "Serie", "Jose", at=1789700000)
    assert original == {}
    assert auto_owners(result, "tv", 1234) == {"Jose": 1789700000}
    result2 = add_auto_owner(result, "tv", 1234, "Serie", "Ana", at=1789700100)
    assert auto_owners(result2, "tv", 1234) == {"Jose": 1789700000, "Ana": 1789700100}
    assert auto_owners(result, "tv", 1234) == {"Jose": 1789700000}  # original intacto


def test_remove_owner_keeps_other_owners():
    data = add_auto_owner({}, "tv", 1234, "Serie", "Jose", at=1)
    data = add_auto_owner(data, "tv", 1234, "Serie", "Ana", at=2)
    result = remove_auto_owner(data, "tv", 1234, "Jose")
    assert auto_owners(result, "tv", 1234) == {"Ana": 2}


def test_remove_last_owner_drops_entry():
    data = add_auto_owner({}, "tv", 1234, "Serie", "Jose", at=1)
    result = remove_auto_owner(data, "tv", 1234, "Jose")
    assert result == {}
    assert auto_owners(result, "tv", 1234) == {}


def test_other_owners_excludes_self():
    data = add_auto_owner({}, "tv", 1234, "Serie", "Jose", at=1)
    data = add_auto_owner(data, "tv", 1234, "Serie", "Ana", at=2)
    assert other_owners(data, "tv", 1234, "Jose") == {"Ana": 2}
    assert other_owners(data, "tv", 1234, "Nadie") == {"Jose": 1, "Ana": 2}
    assert other_owners({}, "tv", 9999, "Jose") == {}


def test_legacy_or_corrupt_entries_do_not_break():
    assert auto_owners({"tv:1234": "basura"}, "tv", 1234) == {}
    assert auto_owners({"tv:1234": {"name": "Serie"}}, "tv", 1234) == {}
    assert auto_owners({}, "tv", 1234) == {}


def test_transfer_keeps_coowners_and_existing_date():
    data = add_auto_owner({}, "tv", 1, "A", "Jose", at=10)
    data = add_auto_owner(data, "tv", 1, "A", "Ana", at=20)
    result = transfer_auto_owner(data, "Jose", "Luis")
    assert auto_owners(result, "tv", 1) == {"Ana": 20, "Luis": 10}


def test_remove_all_by_owner_keeps_series_with_other_owners():
    data = add_auto_owner({}, "tv", 1, "A", "Jose", at=10)
    data = add_auto_owner(data, "tv", 1, "A", "Ana", at=20)
    data = add_auto_owner(data, "tv", 2, "B", "Jose", at=30)
    result = remove_all_auto_by_owner(data, "Jose")
    assert auto_owners(result, "tv", 1) == {"Ana": 20}
    assert auto_owners(result, "tv", 2) == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.auto_series.app_data_dir", lambda: tmp_path)
    data = add_auto_owner({}, "tv", 1234, "Serie", "Jose", at=1789700000)
    save_local_cache(data)
    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.auto_series.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}
