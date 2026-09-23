"""Pruebas de la comprobación de espacio libre antes de subir, en modo
automático (mismo comportamiento que ya tenía la subida manual)."""

import pytest
from unittest.mock import MagicMock

import core.auto_watcher as autowatcher_mod
from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


@pytest.fixture(autouse=True)
def _isolate_processed_db(tmp_path, monkeypatch):
    db_path = tmp_path / "auto_processed_test.json"
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: db_path)


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, free_space, upload_calls):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "ftp_host": "servidor",
        "rename_local": True,
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [],
        },
    })

    tmdb = MagicMock()
    tmdb.search_multi.return_value = [{
        "id": 1, "title": "Mi Pelicula", "media_type": "movie",
        "release_date": "2024-01-01", "genre_ids": [],
    }]
    tmdb.build_media_info.return_value = MediaInfo(
        tmdb_id=1, media_type="movie", title="Mi Pelicula",
        original_title="Mi Pelicula", year="2024", genre_ids=[])

    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/peliculas/Mi Pelicula/"
    ftp.get_free_space.return_value = free_space
    ftp.get_remote_size.return_value = None   # no existe nada remoto con ese nombre
    ftp.list_files.return_value = []   # carpeta vacia -- sin duplicados

    def _fake_upload(local_path, remote_path, **kw):
        upload_calls.append({"local_path": local_path, **kw})
        return True, "ok"
    ftp.upload_file.side_effect = _fake_upload

    watcher = AutoWatcher(str(folder), config, tmdb, ftp,
                           on_event=lambda *a, **k: None,
                           on_file_event=lambda *a, **k: None,
                           ftp_factory=lambda: ftp)
    watcher._is_stable = lambda path: True
    return watcher, ftp


def test_uploads_normally_when_server_does_not_support_space_check(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher(tmp_path, free_space=None, upload_calls=upload_calls)

    watcher._process(original)

    assert len(upload_calls) == 1
    ftp.get_free_space.assert_called_once_with("/peliculas/Mi Pelicula/")


def test_uploads_normally_when_enough_free_space(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")   # unos pocos bytes
    upload_calls = []
    watcher, ftp = _make_watcher(tmp_path, free_space=10 * 1024**3, upload_calls=upload_calls)

    watcher._process(original)

    assert len(upload_calls) == 1


def test_blocks_upload_when_not_enough_free_space(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"x" * 1000)
    upload_calls = []
    watcher, ftp = _make_watcher(tmp_path, free_space=500, upload_calls=upload_calls)

    watcher._process(original)

    assert upload_calls == [], "no deberia haber intentado subir con espacio insuficiente"
    assert any(v.get("status") == "disco_lleno" for v in watcher._processed.values()), watcher._processed


def _make_watcher_with_jellyfin(tmp_path, upload_calls, jellyfin_free_space):
    import core.media_server_refresh as msr
    msr._reset_storage_cache_for_tests()
    watcher, ftp = _make_watcher(tmp_path, free_space=None, upload_calls=upload_calls)
    watcher.config.d.update({
        "jellyfin_enabled": True,
        "jellyfin_host": "http://jellyfin:8096",
        "jellyfin_api_key": "key123",
    })
    from unittest.mock import patch
    patcher = patch(
        "core.media_server_refresh.get_jellyfin_storage_folders",
        return_value=[{"Path": "/home/administrador/peliculas", "FreeSpace": jellyfin_free_space}],
    )
    patcher.start()
    return watcher, ftp, patcher


def test_falls_back_to_jellyfin_when_ftp_does_not_support_space_check(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"x" * 1000)
    upload_calls = []
    watcher, ftp, patcher = _make_watcher_with_jellyfin(tmp_path, upload_calls, jellyfin_free_space=500)
    try:
        watcher._process(original)
        assert upload_calls == [], "deberia bloquear usando el dato de Jellyfin"
        assert any(v.get("status") == "disco_lleno" for v in watcher._processed.values())
    finally:
        patcher.stop()


def test_jellyfin_fallback_allows_upload_with_enough_space(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"x" * 1000)
    upload_calls = []
    watcher, ftp, patcher = _make_watcher_with_jellyfin(tmp_path, upload_calls, jellyfin_free_space=10 * 1024**3)
    try:
        watcher._process(original)
        assert len(upload_calls) == 1
    finally:
        patcher.stop()
