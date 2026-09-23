import core.cleanup_candidates_cache as ccc
from core.cleanup_candidates import CleanupItem


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ccc, "app_data_dir", lambda: tmp_path)
    return ccc


def test_load_cache_returns_empty_when_no_file(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    assert mod.load_cache() == {}


def test_save_and_reload_cache(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    items = [
        CleanupItem(tmdb_id=1, name="Serie X", media_type="tv", ftp_path="/datos2/series/Serie X",
                    category_name="Series", size_bytes=1000, fully_watched=True, play_count=2),
        CleanupItem(tmdb_id=None, name="Pelicula Y", media_type="movie",
                    ftp_path="/datos/peliculas/Pelicula Y", category_name="Peliculas",
                    size_bytes=2000, loose_file_paths=["/datos/peliculas/Pelicula Y.mkv"]),
    ]
    mod.save_cache(items, last_scan_ts=1234.5)

    loaded = mod.load_cache()
    assert loaded["last_scan_ts"] == 1234.5
    assert len(loaded["items"]) == 2
    assert loaded["items"][0] == items[0]
    assert loaded["items"][1] == items[1]
    assert loaded["items"][1].loose_file_paths == ["/datos/peliculas/Pelicula Y.mkv"]


def test_load_cache_degrades_to_empty_on_corrupt_file(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    (tmp_path / mod._FILENAME).write_text("esto no es json valido", encoding="utf-8")
    assert mod.load_cache() == {}


def test_load_cache_degrades_to_empty_on_incompatible_format(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    (tmp_path / mod._FILENAME).write_text(
        '{"items": [{"campo_que_no_existe": 1}], "last_scan_ts": 1.0}', encoding="utf-8")
    assert mod.load_cache() == {}


def test_wrap_for_remote_round_trips_through_unwrap():
    items = [
        CleanupItem(tmdb_id=1, name="Serie X", media_type="tv", ftp_path="/datos2/series/Serie X",
                    category_name="Series", size_bytes=1000, fully_watched=True, play_count=2),
    ]
    payload = ccc.wrap_for_remote(items, last_scan_ts=1234.5, scanned_by="Jose")
    result = ccc.unwrap_from_remote(payload)
    assert result["items"] == items
    assert result["last_scan_ts"] == 1234.5
    assert result["scanned_by"] == "Jose"


def test_unwrap_from_remote_none_for_non_dict_payload():
    assert ccc.unwrap_from_remote(None) is None
    assert ccc.unwrap_from_remote([]) is None
    assert ccc.unwrap_from_remote("no es un dict") is None


def test_unwrap_from_remote_none_for_incompatible_items():
    payload = {"items": [{"campo_que_no_existe": 1}], "last_scan_ts": 1.0}
    assert ccc.unwrap_from_remote(payload) is None


def test_unwrap_from_remote_empty_items_list_is_valid():
    payload = ccc.wrap_for_remote([], last_scan_ts=1.0, scanned_by="Ana")
    result = ccc.unwrap_from_remote(payload)
    assert result == {"items": [], "last_scan_ts": 1.0, "scanned_by": "Ana"}


def test_save_and_reload_cache_preserves_scanned_by(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.save_cache([], last_scan_ts=1.0, scanned_by="Maria")
    loaded = mod.load_cache()
    assert loaded["scanned_by"] == "Maria"
