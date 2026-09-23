import core.missing_episodes_cache as mec


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(mec, "app_data_dir", lambda: tmp_path)
    mec._reset_cache_for_tests()
    return mec


def test_load_cache_returns_empty_when_no_file(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    assert mod.load_cache() == {}


def test_save_and_reload_cache(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    data = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    mod.save_cache(data)

    mod._reset_cache_for_tests()
    assert mod.load_cache() == data


def test_save_cache_updates_in_memory_copy_immediately(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.save_cache({"a": 1})
    assert mod.load_cache() == {"a": 1}   # sin reiniciar la cache, ya deberia estar actualizada


def test_load_cache_degrades_to_empty_on_corrupt_file(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    (tmp_path / mod._FILENAME).write_text("esto no es json valido", encoding="utf-8")
    assert mod.load_cache() == {}


def test_remove_missing_episode_from_cache_removes_episode():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3, 5]}}}
    assert mec.remove_missing_episode_from_cache(cache, 1396, 1, 3) is True
    assert cache["1396"]["missing"] == {"1": [5]}


def test_remove_missing_episode_from_cache_drops_empty_season_key():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    assert mec.remove_missing_episode_from_cache(cache, 1396, 1, 3) is True
    assert cache["1396"]["missing"] == {}


def test_remove_missing_episode_from_cache_false_when_not_present():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    assert mec.remove_missing_episode_from_cache(cache, 1396, 1, 99) is False
    assert cache["1396"]["missing"] == {"1": [3]}   # sin tocar


def test_remove_missing_episode_from_cache_false_for_unknown_tmdb_id():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    assert mec.remove_missing_episode_from_cache(cache, 999, 1, 3) is False


def test_remove_series_from_cache_removes_whole_entry():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3, 5]}},
             "2": {"name": "Otra", "missing": {"1": [1]}}}
    assert mec.remove_series_from_cache(cache, 1396) is True
    assert cache == {"2": {"name": "Otra", "missing": {"1": [1]}}}


def test_remove_series_from_cache_false_for_unknown_tmdb_id():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    assert mec.remove_series_from_cache(cache, 999) is False
    assert "1396" in cache


def test_strip_personal_fields_removes_ignored_and_ai_verdict():
    cache = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]},
                      "ignored": True, "ai_verdict": {"veredicto": "si"}}}
    result = mec.strip_personal_fields(cache)
    assert result["1396"] == {"name": "Breaking Bad", "missing": {"1": [3]}}
    # no muta el original
    assert cache["1396"]["ignored"] is True


def test_strip_personal_fields_keeps_meta_as_is():
    cache = {"_meta": {"last_scan_ts": 123.0, "scanned_by": "Jose"},
             "1396": {"name": "Breaking Bad", "ignored": False}}
    result = mec.strip_personal_fields(cache)
    assert result["_meta"] == {"last_scan_ts": 123.0, "scanned_by": "Jose"}


def test_merge_remote_into_local_takes_remote_facts():
    local = {}
    remote = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    result = mec.merge_remote_into_local(local, remote)
    assert result["1396"]["name"] == "Breaking Bad"
    assert result["1396"]["missing"] == {"1": [3]}


def test_merge_remote_into_local_preserves_local_ignored_and_ai_verdict():
    local = {"1396": {"name": "Breaking Bad (old)", "ignored": True,
                      "ai_verdict": {"veredicto": "si"}}}
    remote = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]}}}
    result = mec.merge_remote_into_local(local, remote)
    assert result["1396"]["name"] == "Breaking Bad"   # dato objetivo -- del remoto
    assert result["1396"]["ignored"] is True           # personal -- conservado del local
    assert result["1396"]["ai_verdict"] == {"veredicto": "si"}   # personal -- conservado del local


def test_merge_remote_into_local_defaults_when_no_local_entry():
    result = mec.merge_remote_into_local({}, {"1396": {"name": "Breaking Bad"}})
    assert result["1396"]["ignored"] is False
    assert result["1396"]["ai_verdict"] is None


def test_merge_remote_into_local_drops_series_not_in_remote():
    local = {"1396": {"name": "Breaking Bad", "ignored": True},
             "999": {"name": "Serie Borrada En Otro Cliente", "ignored": False}}
    remote = {"1396": {"name": "Breaking Bad"}}
    result = mec.merge_remote_into_local(local, remote)
    assert "999" not in result
    assert "1396" in result


def test_merge_remote_into_local_takes_meta_from_remote():
    local = {"_meta": {"last_scan_ts": 1.0, "scanned_by": "Jose"}}
    remote = {"_meta": {"last_scan_ts": 2.0, "scanned_by": "Ana"}}
    result = mec.merge_remote_into_local(local, remote)
    assert result["_meta"] == {"last_scan_ts": 2.0, "scanned_by": "Ana"}


def test_strip_then_merge_round_trip_hides_personal_fields_from_the_wire():
    local_before = {"1396": {"name": "Breaking Bad", "missing": {"1": [3]},
                             "ignored": True, "ai_verdict": {"veredicto": "si"}}}
    # Lo que de verdad viaja por la red -- sin campos personales.
    on_the_wire = mec.strip_personal_fields(local_before)
    assert "ignored" not in on_the_wire["1396"]
    assert "ai_verdict" not in on_the_wire["1396"]
    # Otro cliente, con la MISMA entrada local (sus propios ignored/ai_verdict),
    # aplica lo recibido y no pierde sus campos personales.
    other_client_local = {"1396": {"name": "Breaking Bad", "ignored": False, "ai_verdict": None}}
    merged = mec.merge_remote_into_local(other_client_local, on_the_wire)
    assert merged["1396"]["missing"] == {"1": [3]}
    assert merged["1396"]["ignored"] is False
    assert merged["1396"]["ai_verdict"] is None
