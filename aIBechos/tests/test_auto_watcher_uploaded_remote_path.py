"""El evento "uploaded" debe llevar la ruta REAL en el servidor.

Sin ella, gui/app.py guardaba en el historial la ruta LOCAL dentro del campo
"remote" (`_save_history_entry(fname, path, ...)`, donde `path` es local). En
una instalación real, 241 de 500 registros no sabían dónde había quedado el
archivo en el servidor -- justo el dato que hace falta para poder ofrecer
"quitar de la lista y borrar también del servidor".
"""

from unittest.mock import MagicMock

from core.api_client import MediaInfo
from core.auto_watcher import AutoWatcher
from core.remote_presence import looks_like_remote_path


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
    ftp.build_remote_path.return_value = "/peliculas/Mi Pelicula (2024)"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = []
    ftp.upload_file.side_effect = lambda local_path, remote_path, **kw: (True, "ok")
    return ftp


def _upload_and_get_event(tmp_path):
    (tmp_path / "1 Mi Pelicula.mkv").write_bytes(b"x" * 100)

    events = []
    watcher = _make_watcher(tmp_path, _make_tmdb(), _make_ftp(),
                             lambda path, tipo, **kw: events.append((tipo, kw)))
    watcher._is_stable = lambda path: True
    watcher._scan()

    import time
    deadline = time.monotonic() + 5
    while not any(t == "uploaded" for t, _ in events) and time.monotonic() < deadline:
        time.sleep(0.05)

    subidos = [kw for t, kw in events if t == "uploaded"]
    assert subidos, f"nunca se disparó 'uploaded' -- eventos: {events}"
    return subidos[0]


def test_uploaded_lleva_la_ruta_remota(tmp_path):
    kwargs = _upload_and_get_event(tmp_path)

    remote = kwargs.get("remote_full")
    assert remote, f"'uploaded' debería traer remote_full, recibido: {kwargs}"
    assert remote.startswith("/peliculas/"), remote
    assert remote.endswith(".mkv"), remote


def test_la_ruta_remota_no_se_confunde_con_una_local(tmp_path):
    """La comprobación que hace el diálogo de borrado antes de ofrecer
    'borrar en el servidor'."""
    kwargs = _upload_and_get_event(tmp_path)

    assert looks_like_remote_path(kwargs.get("remote_full"))


def test_la_ruta_remota_incluye_el_nombre_final_del_archivo(tmp_path):
    """Tiene que ser la ruta del ARCHIVO, no la de su carpeta: es lo que se
    le pasará al servidor para borrarlo."""
    kwargs = _upload_and_get_event(tmp_path)

    remote = kwargs["remote_full"]
    assert remote.rsplit("/", 1)[-1] == kwargs.get("new_name")
