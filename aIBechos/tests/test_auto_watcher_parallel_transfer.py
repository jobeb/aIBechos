"""AutoWatcher usaba self.ftp (una única conexión compartida bajo
self._ftp_lock) también para la transferencia REAL, así que aunque
upload_slots concediera varios turnos a la vez, todas las subidas seguían
serializadas detrás de ese mismo candado/socket -- se veían varios archivos
en estado "Subiendo" a la vez pero solo uno transfiriendo de verdad. Estas
pruebas comprueban que la transferencia usa una conexión FTP PROPIA por
hilo (vía ftp_factory), permitiendo solapamiento real, y que self.ftp (la
conexión compartida) nunca se usa para el upload_file en sí."""

import threading
import time
from unittest.mock import MagicMock

from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def test_two_uploads_transfer_concurrently_with_dedicated_connections(tmp_path):
    names = ["1 Pelicula A.mkv", "2 Pelicula B.mkv"]
    for n in names:
        (tmp_path / n).write_bytes(b"contenido")

    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "rename_local": False,
        "ftp_host": "servidor",
        "ftp_parallel": 2,
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

    # Conexión de CONTROL compartida -- nunca debe recibir upload_file.
    shared_ftp = MagicMock()
    shared_ftp.is_connected.return_value = True
    shared_ftp.build_remote_path.return_value = "/peliculas/X/"
    shared_ftp.get_free_space.return_value = None
    shared_ftp.get_remote_size.return_value = None
    shared_ftp.list_files.return_value = []

    # Cada llamada a la fábrica crea una conexión DEDICADA nueva -- si de
    # verdad hay dos transferencias en paralelo, ambas deben poder entrar
    # en la barrera a la vez; si siguieran serializadas detrás de un único
    # candado, la segunda no llegaría hasta que la primera ya hubiera
    # terminado y la barrera nunca se completaría dentro del timeout.
    barrier = threading.Barrier(2, timeout=5)
    created_connections = []

    def _ftp_factory():
        conn = MagicMock()
        conn.connect.return_value = (True, "ok")

        def _fake_upload(local_path, remote_path, **kw):
            barrier.wait()   # lanza BrokenBarrierError si no hay solapamiento real
            return True, "ok"
        conn.upload_file.side_effect = _fake_upload
        created_connections.append(conn)
        return conn

    watcher = AutoWatcher(str(tmp_path), config, tmdb, shared_ftp,
                           on_event=lambda *a, **k: None,
                           on_file_event=lambda *a, **k: None,
                           ftp_factory=_ftp_factory)
    watcher._is_stable = lambda path: True

    watcher._scan()

    deadline = time.monotonic() + 5
    while len(created_connections) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    # Deja que ambos hilos terminen de subir (o se rompa la barrera).
    for conn in created_connections:
        deadline2 = time.monotonic() + 5
        while not conn.disconnect.called and time.monotonic() < deadline2:
            time.sleep(0.05)

    assert len(created_connections) == 2, \
        "cada transferencia deberia abrir su propia conexion dedicada"
    for conn in created_connections:
        assert conn.upload_file.called
        assert conn.disconnect.called, "la conexion dedicada debe cerrarse tras la transferencia"
    assert not shared_ftp.upload_file.called, \
        "la transferencia real no debe usar la conexion de control compartida"
