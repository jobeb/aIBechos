"""AutoWatcher.cancel_file() cancela la subida automática de UN archivo sin
parar el resto del modo automático. Antes la única cancelación era parar el
watcher entero: "Quitar y borrar" sobre una subida automática no la paraba,
la fila desaparecía y la subida seguía 23 minutos hasta el final (real: La
Odisea, 11/10)."""

import threading
import time
from unittest.mock import MagicMock

import core.auto_watcher as autowatcher_mod
from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo
from core.path_key import canon_path


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, ftp, on_file_event, parallel=1):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "rename_local": False,
        "ftp_host": "servidor",
        "ftp_parallel": parallel,
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [],
        },
    })
    tmdb = MagicMock()
    tmdb.search_multi.side_effect = lambda query, *a, **kw: [
        {"id": 1, "title": query, "media_type": "movie",
         "release_date": "2024-01-01", "genre_ids": []}]
    tmdb.build_media_info.side_effect = lambda result, **kw: MediaInfo(
        tmdb_id=1, media_type="movie", title=result["title"],
        original_title=result["title"], year="2024", genre_ids=[])
    w = AutoWatcher(str(folder), config, tmdb, ftp,
                    on_event=lambda *a, **k: None,
                    on_file_event=on_file_event,
                    ftp_factory=lambda: ftp)
    w._is_stable = lambda path: True
    return w


def _make_ftp(upload_impl):
    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/peliculas/X/"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = ftp.list_files_checked.return_value = []
    ftp.delete_file.return_value = (True, "")
    ftp.upload_file.side_effect = upload_impl
    return ftp


def _wait(cond, timeout=5):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.02)
    return cond()


def _blocking_upload(started):
    """upload_file falso que, como el real, solo para con su skip_event."""
    def impl(local_path, remote_path, **kw):
        started.set()
        skip = kw.get("skip_event")
        cancel = kw.get("cancel_event")
        for _ in range(500):
            if cancel is not None and cancel.is_set():
                return False, "cancelado"
            if skip is not None and skip.is_set():
                return False, "saltado"
            time.sleep(0.01)
        return True, "ok"
    return impl


def test_cancel_file_stops_only_that_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: tmp_path / "db.json")
    src = tmp_path / "w"
    src.mkdir()
    f = src / "Pelicula.mkv"
    f.write_bytes(b"x" * 100)

    events = []
    started = threading.Event()
    ftp = _make_ftp(_blocking_upload(started))
    w = _make_watcher(src, ftp, lambda p, t, **k: events.append((t, k)))
    w._scan()

    assert started.wait(5), "la subida nunca empezó"
    assert w.cancel_file(str(f)) is True
    assert _wait(lambda: any(t == "skip" for t, _ in events)), events
    assert not any(t == "uploaded" for t, _ in events)
    assert not w._stop.is_set(), "cancelar un archivo no debe parar el watcher"
    # El parcial no se queda en el servidor, y no se vuelve a coger.
    ftp.delete_file.assert_called()
    assert _wait(lambda: w._processed.get(canon_path(str(f)), {}).get("status") == "descartado")
    assert w._skip_events == {}


def test_cancel_file_while_waiting_for_a_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: tmp_path / "db.json")
    src = tmp_path / "w"
    src.mkdir()
    a = src / "Alfa.mkv"
    b = src / "Beta.mkv"
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    events = []
    started = threading.Event()
    ftp = _make_ftp(_blocking_upload(started))
    w = _make_watcher(src, ftp, lambda p, t, **k: events.append((p, t)))
    w._scan()

    assert started.wait(5)
    # Uno sube, el otro espera turno (1 cupo): cancelar el que espera.
    uploading = ftp.upload_file.call_args[0][0]
    waiting = str(b) if canon_path(uploading) == canon_path(str(a)) else str(a)
    assert _wait(lambda: canon_path(waiting) in w._skip_events)
    assert w.cancel_file(waiting) is True
    assert _wait(lambda: (waiting, "skip") in events), events
    assert ftp.upload_file.call_count == 1, "el cancelado no debe llegar a subir"
    w.cancel_file(uploading)   # liberar el hilo que sigue subiendo


def test_cancel_file_unknown_path_returns_false(tmp_path):
    w = _make_watcher(tmp_path, _make_ftp(lambda *a, **k: (True, "ok")), lambda *a, **k: None)
    assert w.cancel_file(str(tmp_path / "no_existe.mkv")) is False
