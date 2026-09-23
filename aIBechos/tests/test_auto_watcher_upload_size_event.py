"""El evento "uploaded" que AutoWatcher dispara al terminar una subida debe
llevar el tamaño real del archivo subido -- antes se guardaba con size=0 a
propósito en gui/app.py (nunca se lo pasaba AutoWatcher), lo que hacía que
el historial de actividad, el ranking de subidas y las estadísticas por
categoría contaran cada subida automática como "0 B" (bug real reportado:
"pone 3 subidas 0B parecen erroneos" en la pestaña Estadísticas)."""

from pathlib import Path
from unittest.mock import MagicMock

from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, tmdb, ftp, on_file_event):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "rename_local": False,
        "ftp_host": "servidor",
        "ftp_parallel": 1,
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [],
        },
    })
    return AutoWatcher(str(folder), config, tmdb, ftp,
                        on_event=lambda *a, **k: None,
                        on_file_event=on_file_event,
                        ftp_factory=lambda: ftp)


def _make_tmdb():
    tmdb = MagicMock()
    tmdb.search_multi.side_effect = lambda query, *a, **kw: [
        {"id": 1, "title": query, "media_type": "movie",
         "release_date": "2024-01-01", "genre_ids": []}]
    tmdb.build_media_info.side_effect = lambda result, **kw: MediaInfo(
        tmdb_id=1, media_type="movie", title=result["title"],
        original_title=result["title"], year="2024", genre_ids=[])
    return tmdb


def _make_ftp():
    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/peliculas/X/"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = []
    ftp.upload_file.side_effect = lambda local_path, remote_path, **kw: (True, "ok")
    return ftp


def test_uploaded_event_carries_the_real_local_file_size(tmp_path):
    content = b"x" * 12345   # tamano exacto y conocido, distinto de 0
    (tmp_path / "1 Pelicula.mkv").write_bytes(content)

    events = []

    def on_file_event(path, tipo, **kwargs):
        events.append((tipo, kwargs))

    tmdb = _make_tmdb()
    ftp = _make_ftp()
    watcher = _make_watcher(tmp_path, tmdb, ftp, on_file_event)
    watcher._is_stable = lambda path: True

    watcher._scan()

    import time
    deadline = time.monotonic() + 5
    while not any(tipo == "uploaded" for tipo, _ in events) and time.monotonic() < deadline:
        time.sleep(0.05)

    uploaded = [kwargs for tipo, kwargs in events if tipo == "uploaded"]
    assert uploaded, f"nunca se disparo el evento 'uploaded' -- eventos vistos: {events}"
    assert uploaded[0].get("size") == len(content), (
        f"el evento 'uploaded' deberia llevar size={len(content)} (tamano real del archivo), "
        f"recibido: {uploaded[0]}")


def test_uploaded_event_size_matches_disk_even_with_different_content_sizes(tmp_path):
    small = b"a" * 100
    large = b"b" * 999999
    (tmp_path / "1 Pelicula Pequena.mkv").write_bytes(small)
    (tmp_path / "2 Pelicula Grande.mkv").write_bytes(large)

    events = []

    def on_file_event(path, tipo, **kwargs):
        events.append((tipo, kwargs, Path(path).name if path else None))

    tmdb = _make_tmdb()
    ftp = _make_ftp()
    watcher = _make_watcher(tmp_path, tmdb, ftp, on_file_event)
    watcher._is_stable = lambda path: True

    watcher._scan()

    import time
    deadline = time.monotonic() + 5
    while sum(1 for tipo, _, _ in events if tipo == "uploaded") < 2 and time.monotonic() < deadline:
        time.sleep(0.05)

    sizes_seen = sorted(kwargs.get("size") for tipo, kwargs, _ in events if tipo == "uploaded")
    assert sizes_seen == sorted([len(small), len(large)]), (
        f"tamanos incorrectos en los eventos 'uploaded': {sizes_seen}")
