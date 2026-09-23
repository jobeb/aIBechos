"""Pruebas de la detección de duplicados antes de subir, en modo automático."""

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


def _make_watcher(existing_remote_files, upload_calls, media_type="movie",
                   season=None, episode=None):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "tv_template": "{serie} {temporada}x{episodio:02d} {titulo}{ext}",
        "min_confidence": 0,
        "rename_local": True,
        "ftp_host": "servidor",
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [{"id": "c2", "name": "Series", "genre_ids": [],
                    "root": "/series", "template": "{serie}/Temporada {temporada:02d}/"}],
        },
    })

    tmdb = MagicMock()
    if media_type == "movie":
        tmdb.search_multi.return_value = [{
            "id": 1, "title": "Mi Pelicula", "media_type": "movie",
            "release_date": "2024-01-01", "genre_ids": [],
        }]
        info = MediaInfo(tmdb_id=1, media_type="movie", title="Mi Pelicula",
                          original_title="Mi Pelicula", year="2024", genre_ids=[])
    else:
        tmdb.search_multi.return_value = [{
            "id": 1, "name": "Mi Serie", "media_type": "tv", "genre_ids": [],
        }]
        info = MediaInfo(tmdb_id=1, media_type="tv", title="Mi Serie",
                          original_title="Mi Serie", year="2024",
                          season=season, episode=episode, genre_ids=[])
        tmdb.get_episode_info.return_value = {}   # sin titulo de episodio -> no se reemplaza media_info
    tmdb.build_media_info.return_value = info

    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/destino/"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = existing_remote_files

    def _fake_upload(local_path, remote_path, **kw):
        upload_calls.append({"local_path": local_path, **kw})
        return True, "ok"
    ftp.upload_file.side_effect = _fake_upload

    watcher = AutoWatcher("/carpeta", config, tmdb, ftp,
                           on_event=lambda *a, **k: None,
                           on_file_event=lambda *a, **k: None,
                           ftp_factory=lambda: ftp)
    watcher._is_stable = lambda path: True
    return watcher, ftp


def test_movie_skips_upload_when_duplicate_exists(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher(["Mi Pelicula OtraVersion.mkv"], upload_calls, media_type="movie")

    watcher._process(original)

    assert upload_calls == [], "no deberia subir si ya hay otra version del mismo contenido"
    assert any(v.get("status") == "duplicado" for v in watcher._processed.values()), watcher._processed


def test_movie_uploads_when_folder_empty(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher([], upload_calls, media_type="movie")

    watcher._process(original)

    assert len(upload_calls) == 1


def test_movie_skips_upload_when_same_remote_name_already_exists(tmp_path):
    """Caso real: "La Odisea (2026)" descargada dos veces -- el nombre limpio
    coincide y la 2ª subida machacaba en silencio la copia ya subida (find_duplicate
    excluye a propósito el nombre exacto de la comparación). Con el archivo remoto
    ya completo, se omite en vez de sobrescribir."""
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher(["Mi Pelicula (2024).mkv"], upload_calls, media_type="movie")
    # El archivo remoto con el MISMO nombre ya existe y está completo
    ftp.get_remote_size.return_value = 9   # >= tamaño local ("contenido" = 9 bytes)

    watcher._process(original)

    assert upload_calls == [], "no debería sobrescribir un archivo remoto ya completo"
    assert any(v.get("status") == "duplicado" for v in watcher._processed.values()), watcher._processed


def test_movie_uploads_when_remote_partial_is_smaller(tmp_path):
    """Un parcial remoto más pequeño que el local no cuenta como "ya existe":
    se sube para completarlo (igual que la subida manual)."""
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher([], upload_calls, media_type="movie")
    ftp.get_remote_size.return_value = 3   # parcial menor que los 9 bytes locales

    watcher._process(original)

    assert len(upload_calls) == 1


def test_tv_skips_upload_when_same_episode_exists_under_different_name(tmp_path):
    original = tmp_path / "1.mi.serie.s01e06.WEB-DL.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher(
        ["Mi Serie 1x06 OtroGrupo.mkv"], upload_calls,
        media_type="tv", season=1, episode=6)

    watcher._process(original)

    assert upload_calls == []
    assert any(v.get("status") == "duplicado" for v in watcher._processed.values()), watcher._processed


def test_tv_uploads_when_episode_not_present_yet(tmp_path):
    original = tmp_path / "1.mi.serie.s01e06.WEB-DL.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher(
        ["Mi Serie 1x01.mkv", "Mi Serie 1x02.mkv"], upload_calls,
        media_type="tv", season=1, episode=6)

    watcher._process(original)

    assert len(upload_calls) == 1


def test_scan_does_not_reprocess_file_already_marked_duplicado(tmp_path):
    # Caso real reportado: "Dr. Stone 4x32" se detectaba como duplicado
    # correctamente, pero al activar el modo automático volvía a aparecer
    # una y otra vez -- _scan() no tenía "duplicado" en la lista de
    # estados estables, así que en cada escaneo lo borraba de la base de
    # datos y lo procesaba de cero otra vez.
    original = tmp_path / "1.mi.serie.s01e06.WEB-DL.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher, ftp = _make_watcher(
        ["Mi Serie 1x06 OtroGrupo.mkv"], upload_calls,
        media_type="tv", season=1, episode=6)
    watcher.folder = tmp_path

    watcher._process(original)
    assert any(v.get("status") == "duplicado" for v in watcher._processed.values())

    process_calls = []
    watcher._process = lambda path: process_calls.append(path)
    watcher._scan()

    assert process_calls == [], "un archivo ya marcado como duplicado no debe reprocesarse en el siguiente escaneo"
