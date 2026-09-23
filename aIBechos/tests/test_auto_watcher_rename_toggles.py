"""Pruebas de los toggles "Renombrar en origen" / "Renombrar en destino"
aplicados al pipeline de AutoWatcher._process()."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

import core.auto_watcher as autowatcher_mod
from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


@pytest.fixture(autouse=True)
def _isolate_processed_db(tmp_path, monkeypatch):
    """_process() escribe en auto_processed.json vía _processed_db_path() —
    redirigir a un fichero temporal para no tocar el real del usuario."""
    db_path = tmp_path / "auto_processed_test.json"
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: db_path)


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, extra_config, upload_calls):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "ftp_host": "servidor",
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [],
        },
        **extra_config,
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
    ftp.get_free_space.return_value = None   # servidor sin soporte (p.ej. vsftpd) -- caso mas comun
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = []   # carpeta vacia -- sin duplicados

    def _fake_upload(local_path, remote_path, **kw):
        upload_calls.append({"local_path": local_path, **kw})
        return True, "ok"
    ftp.upload_file.side_effect = _fake_upload

    watcher = AutoWatcher(str(folder), config, tmdb, ftp,
                           on_event=lambda *a, **k: None,
                           on_file_event=lambda *a, **k: None,
                           ftp_factory=lambda: ftp)
    watcher._is_stable = lambda path: True   # evita el sleep(6) real
    return watcher


def test_rename_local_true_renames_file_on_disk(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher = _make_watcher(tmp_path, {"rename_local": True}, upload_calls)

    watcher._process(original)

    assert not original.exists(), "el archivo original debería haberse renombrado"
    renamed = tmp_path / "Mi Pelicula (2024).mkv"
    assert renamed.exists(), "debería existir el archivo con el nombre limpio"
    assert len(upload_calls) == 1
    assert upload_calls[0]["local_path"] == str(renamed)
    assert upload_calls[0]["remote_filename"] == "Mi Pelicula (2024).mkv"


def test_rename_local_false_keeps_original_file_but_uploads_clean_name(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher = _make_watcher(
        tmp_path, {"rename_local": False, "rename_remote": True}, upload_calls)

    watcher._process(original)

    assert original.exists(), "el archivo original NO debería tocarse"
    assert not (tmp_path / "Mi Pelicula (2024).mkv").exists()
    assert len(upload_calls) == 1
    assert upload_calls[0]["local_path"] == str(original), \
        "se debe leer y subir el archivo desde su ubicación original"
    assert upload_calls[0]["remote_filename"] == "Mi Pelicula (2024).mkv", \
        "el nombre remoto debe seguir siendo el limpio aunque no se renombrara en local"


def test_rename_remote_false_uploads_with_original_filename(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher = _make_watcher(
        tmp_path, {"rename_local": True, "rename_remote": False}, upload_calls)

    watcher._process(original)

    renamed = tmp_path / "Mi Pelicula (2024).mkv"
    assert renamed.exists(), "en local sí se renombra (rename_local=True)"
    assert len(upload_calls) == 1
    assert upload_calls[0]["remote_filename"] == original.name, \
        "en el servidor debe conservarse el nombre original, no el limpio"
