"""AutoWatcher recorre subcarpetas (excluyendo "procesados"), descomprime
archivos comprimidos cuando está activado, e identifica libros/cómics vía
Google Books/ComicVine además de vídeo vía TMDB."""

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.auto_watcher as autowatcher_mod
from core.auto_watcher import AutoWatcher, _PROTECTED_STATUSES
from core.api_client import MediaInfo


@pytest.fixture(autouse=True)
def _isolate_processed_db(tmp_path, monkeypatch):
    # Sin esto, _mark()/_save_entry() escribirían en el auto_processed.json
    # REAL del usuario (app_data_dir()) en vez de un archivo de prueba aislado.
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: tmp_path / "auto_processed_test.json")


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, config_overrides=None, comicvine=None, book_client=None):
    d = {
        "poll_interval": 10,
        "min_confidence": 0,
        "rename_local": False,
        "libro_template": "{serie}{ext}",
        "comic_template": "{serie} ({año}) #{episodio:02d}{ext}",
    }
    d.update(config_overrides or {})
    config = _FakeConfig(d)
    tmdb = MagicMock()
    ftp = MagicMock()
    watcher = AutoWatcher(str(folder), config, tmdb, ftp,
                          on_event=lambda *a, **k: None,
                          on_file_event=lambda *a, **k: None,
                          ftp_factory=lambda: ftp,
                          comicvine_client=comicvine, book_client=book_client)
    watcher._is_stable = lambda path: True
    return watcher, tmdb, ftp


# ── Recursión ────────────────────────────────────────────────────────────

def test_scan_finds_video_file_in_nested_subfolder(tmp_path):
    nested = tmp_path / "Descargas" / "Serie" / "Temporada 1"
    nested.mkdir(parents=True)
    (nested / "Serie 1x01.mkv").write_bytes(b"contenido")

    watcher, _, _ = _make_watcher(tmp_path)
    found = list(watcher._iter_watch_files())
    assert any(p.name == "Serie 1x01.mkv" for p in found)


def test_scan_excludes_procesados_subfolder_at_any_depth(tmp_path):
    (tmp_path / "procesados").mkdir()
    (tmp_path / "procesados" / "Ya Subido.mkv").write_bytes(b"contenido")
    nested_procesados = tmp_path / "Libros" / "procesados"
    nested_procesados.mkdir(parents=True)
    (nested_procesados / "Ya Descomprimido.zip").write_bytes(b"contenido")

    watcher, _, _ = _make_watcher(tmp_path)
    found = [p.name for p in watcher._iter_watch_files()]
    assert "Ya Subido.mkv" not in found
    assert "Ya Descomprimido.zip" not in found


# ── Archivos comprimidos ─────────────────────────────────────────────────

def test_scan_ignores_archives_when_auto_extract_disabled(tmp_path):
    (tmp_path / "Comic.zip").write_bytes(b"contenido")
    watcher, _, _ = _make_watcher(tmp_path, {"auto_extract_archives": False})
    watcher._scan()
    assert str(tmp_path / "Comic.zip") not in watcher._in_progress


def test_scan_picks_up_archive_when_auto_extract_enabled(tmp_path, monkeypatch):
    archive = tmp_path / "Comic.zip"
    archive.write_bytes(b"contenido")
    watcher, _, _ = _make_watcher(tmp_path, {"auto_extract_archives": True})

    processed = []
    monkeypatch.setattr(watcher, "_process_archive", lambda p: processed.append(p))

    watcher._scan()
    deadline = time.monotonic() + 5
    while not processed and time.monotonic() < deadline:
        time.sleep(0.05)
    assert processed and processed[0].name == "Comic.zip"


def test_process_archive_marks_descomprimido_and_applies_post_action(tmp_path, monkeypatch):
    archive = tmp_path / "Comic.zip"
    archive.write_bytes(b"contenido")
    watcher, _, _ = _make_watcher(tmp_path, {"auto_action": "Eliminar original"})

    monkeypatch.setattr("core.archive_extract.extract_archive",
                        lambda p: (True, str(tmp_path / "Comic")))

    watcher._process_archive(archive)

    assert not archive.exists()   # "Eliminar original" tras descomprimir
    db = watcher._load_db()
    assert db[str(archive)]["status"] == "descomprimido"
    assert "descomprimido" in _PROTECTED_STATUSES


def test_process_archive_leaves_file_intact_on_extraction_failure(tmp_path, monkeypatch):
    archive = tmp_path / "Malo.rar"
    archive.write_bytes(b"contenido")
    watcher, _, _ = _make_watcher(tmp_path, {"auto_action": "Eliminar original"})

    monkeypatch.setattr("core.archive_extract.extract_archive",
                        lambda p: (False, "unrar no está instalado"))

    watcher._process_archive(archive)

    assert archive.exists()   # no se aplica la acción post-proceso si falló
    db = watcher._load_db()
    assert db[str(archive)]["status"] == "error_descomprimir"


# ── Identificación de libros/cómics ──────────────────────────────────────

def _make_comicvine():
    comicvine = MagicMock()
    comicvine.search_volumes.return_value = [
        {"volume": {"name": "The Promise"}, "id": 1, "start_year": "2012"}]
    comicvine.build_comic_info.side_effect = lambda top, episode=None: MediaInfo(
        tmdb_id=1, media_type="libro", title="The Promise", original_title="The Promise",
        year="2012", genre_ids=["comic"], episode=episode)
    return comicvine


def test_process_identifies_and_uploads_a_comic(tmp_path):
    (tmp_path / "The Promise #01.cbz").write_bytes(b"contenido")
    comicvine = _make_comicvine()
    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/libros/The Promise/"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = []
    uploaded = []
    ftp.upload_file.side_effect = lambda local_path, remote_path, **kw: (
        uploaded.append(Path(local_path).name) or (True, "ok"))

    watcher, _, _ = _make_watcher(
        tmp_path,
        {"ftp_host": "servidor", "ftp_parallel": 1,
         "ftp_categories": {"tv": [], "movie": [], "libro": [
             {"id": "c1", "name": "Libros", "genre_ids": [], "root": "/libros", "template": "{serie}/"}]}},
        comicvine=comicvine, book_client=MagicMock())
    watcher.ftp = ftp
    watcher._ftp_factory = lambda: ftp

    watcher._scan()
    deadline = time.monotonic() + 5
    while not uploaded and time.monotonic() < deadline:
        time.sleep(0.05)

    assert uploaded, "el cómic debería haberse subido"
    comicvine.search_volumes.assert_called()


def test_process_marks_low_confidence_comic_for_review_instead_of_uploading(tmp_path):
    (tmp_path / "Serie Rara #01.cbz").write_bytes(b"contenido")
    comicvine = _make_comicvine()   # siempre devuelve "The Promise", muy distinto de "Serie Rara"
    ftp = MagicMock()
    ftp.upload_file.return_value = (True, "ok")

    watcher, _, _ = _make_watcher(
        tmp_path,
        {"min_confidence": 70, "ftp_host": "servidor",
         "ftp_categories": {"tv": [], "movie": [], "libro": []}},
        comicvine=comicvine, book_client=MagicMock())

    watcher._scan()
    deadline = time.monotonic() + 5
    key = str(tmp_path / "Serie Rara #01.cbz")
    while key not in watcher._load_db() and time.monotonic() < deadline:
        time.sleep(0.05)

    db = watcher._load_db()
    assert db[key]["status"] == "baja_confianza"
    ftp.upload_file.assert_not_called()


def test_process_skips_book_when_no_clients_configured(tmp_path):
    (tmp_path / "Un Libro.pdf").write_bytes(b"contenido")
    watcher, _, _ = _make_watcher(tmp_path)   # sin comicvine_client/book_client

    watcher._scan()
    deadline = time.monotonic() + 5
    key = str(tmp_path / "Un Libro.pdf")
    while key not in watcher._load_db() and time.monotonic() < deadline:
        time.sleep(0.05)

    db = watcher._load_db()
    assert db[key]["status"] == "sin_resultados"
