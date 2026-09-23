"""_upload_to_ftp() esperaba su turno de "Subidas simultaneas"
(upload_slots.acquire(), un cupo GLOBAL compartido con la subida manual)
mientras seguia agarrado el candado de conexion FTP compartido (_ftp_lock) --
si el cupo lo ocupaba una subida manual durante varios minutos, cualquier
otra parte de la app que tambien necesite esa misma conexion (refresco de
espacio libre, "Probar conexion"...) se quedaba bloqueada todo ese tiempo,
aunque no hubiera ninguna transferencia real en curso. Este test comprueba
que el candado esta LIBRE mientras se espera el cupo."""

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


def _make_watcher(folder, upload_calls, on_file_event=None):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "ftp_host": "servidor",
        "ftp_parallel": 1,
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
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = []

    def _fake_upload(local_path, remote_path, **kw):
        upload_calls.append({"local_path": local_path, **kw})
        return True, "ok"
    ftp.upload_file.side_effect = _fake_upload

    watcher = AutoWatcher(str(folder), config, tmdb, ftp,
                           on_event=lambda *a, **k: None,
                           on_file_event=on_file_event or (lambda *a, **k: None),
                           ftp_factory=lambda: ftp)
    watcher._is_stable = lambda path: True
    return watcher


def test_ftp_lock_is_free_while_waiting_for_an_upload_slot(tmp_path):
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    watcher = _make_watcher(tmp_path, upload_calls)

    # Simula una subida manual ya en marcha ocupando el unico cupo
    # disponible (ftp_parallel=1) -- upload_slots es el MISMO gestor que
    # usaria la cola manual de la GUI.
    assert watcher.upload_slots.acquire()

    done = threading.Event()

    def _run():
        watcher._process(original)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Dar tiempo a que el hilo llegue a esperar el cupo (todo lo previo --
    # conectar, categoria, duplicados, espacio -- es instantaneo con mocks).
    time.sleep(0.3)
    assert not done.is_set(), "no deberia haber terminado todavia: el cupo sigue ocupado"

    # El punto central de este test: mientras _process() esta bloqueado
    # esperando el cupo, _ftp_lock debe estar LIBRE -- si siguiera agarrado
    # (el bug original), este acquire() no conseguido fallaria.
    got_lock = watcher._ftp_lock.acquire(blocking=False)
    assert got_lock, "_ftp_lock no deberia seguir agarrado mientras se espera el cupo de subida"
    watcher._ftp_lock.release()

    watcher.upload_slots.release()
    t.join(timeout=5)
    assert done.is_set()
    assert len(upload_calls) == 1


def test_fires_queued_before_slot_and_uploading_only_after(tmp_path):
    """Antes, el evento "uploading" se disparaba ANTES de esperar turno de
    upload_slots -- si varios archivos se detectaban a la vez, los 5 hilos
    marcaban la fila como "Subiendo" aunque solo uno pudiera transferir de
    verdad al mismo tiempo. Ahora debe dispararse "queued" mientras espera
    turno, y "uploading" recien al conseguirlo."""
    original = tmp_path / "1.mi.pelicula.WEB-DL.2024.mkv"
    original.write_bytes(b"contenido")
    upload_calls = []
    events = []
    watcher = _make_watcher(
        tmp_path, upload_calls,
        on_file_event=lambda path, tipo, **kw: events.append(tipo))

    assert watcher.upload_slots.acquire()   # ocupa el unico cupo (ftp_parallel=1)

    done = threading.Event()

    def _run():
        watcher._process(original)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.3)

    assert not done.is_set()
    assert "queued" in events, "deberia marcarse en cola mientras espera turno"
    assert "uploading" not in events, \
        "no deberia marcarse como subiendo todavia -- el cupo sigue ocupado"

    watcher.upload_slots.release()
    t.join(timeout=5)
    assert done.is_set()
    assert events.index("queued") < events.index("uploading") < events.index("uploaded")
