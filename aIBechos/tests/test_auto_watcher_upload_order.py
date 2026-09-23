"""Cuando _scan() detecta varios archivos nuevos a la vez, cada uno se
procesa en su propio hilo (identificación TMDB incluida) -- si el orden de
SUBIDA dependiera de cuál de esos hilos termina antes (identificación TMDB,
o incluso la propia espera de estabilidad, que en archivos reales no dura
EXACTAMENTE lo mismo por la E/S de disco), un archivo detectado después
podía colarse por delante de uno detectado antes. El turno de subida se
reserva en _scan() -- de un solo hilo, sin ninguna carrera posible -- en
vez de dentro de cada hilo de _process(), donde se probó primero y seguía
siendo inestable (visto de verdad: un archivo detectado 4º subió antes que
uno detectado 3º, con archivos reales donde la espera de estabilidad no
duraba lo mismo para todos)."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, tmdb, ftp):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "rename_local": False,   # asi el local_path subido es el nombre original, facil de rastrear
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
                        on_file_event=lambda *a, **k: None,
                        ftp_factory=lambda: ftp)


def _make_tmdb(slow_query_substring=None, slow_seconds=0.4):
    tmdb = MagicMock()

    def _search(query, *a, **kw):
        if slow_query_substring and slow_query_substring in query:
            time.sleep(slow_seconds)
        return [{"id": 1, "title": query, "media_type": "movie",
                  "release_date": "2024-01-01", "genre_ids": []}]
    tmdb.search_multi.side_effect = _search
    tmdb.build_media_info.side_effect = lambda result, **kw: MediaInfo(
        tmdb_id=1, media_type="movie", title=result["title"],
        original_title=result["title"], year="2024", genre_ids=[])
    return tmdb


def _make_ftp(upload_order: list):
    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/peliculas/X/"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.return_value = []

    def _fake_upload(local_path, remote_path, **kw):
        upload_order.append(Path(local_path).name)
        return True, "ok"
    ftp.upload_file.side_effect = _fake_upload
    return ftp


def test_scan_uploads_in_detection_order_even_if_a_later_file_identifies_faster(tmp_path):
    names = ["1 Pelicula A.mkv", "2 Pelicula B.mkv", "3 Pelicula C.mkv"]
    for n in names:
        (tmp_path / n).write_bytes(b"contenido")

    upload_order = []
    # "A" -- el primero detectado -- tarda MAS en identificarse; aun así
    # debe subir primero, porque el turno se reserva en _scan(), antes de
    # que ningún hilo haga ninguna búsqueda TMDB.
    tmdb = _make_tmdb(slow_query_substring="A")
    ftp = _make_ftp(upload_order)
    watcher = _make_watcher(tmp_path, tmdb, ftp)
    watcher._is_stable = lambda path: True

    watcher._scan()

    deadline = time.monotonic() + 5
    while len(upload_order) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)

    assert upload_order == names, \
        f"deberian subir en orden de deteccion, pero subieron en: {upload_order}"


def test_scan_uploads_in_order_even_with_uneven_stability_wait_timing(tmp_path):
    """El caso real que motivó mover la reserva de vuelta a _scan(): con
    archivos reales, _is_stable() (E/S de disco) no tarda exactamente lo
    mismo para todos -- un archivo detectado DESPUÉS puede terminar su
    espera de estabilidad ANTES que uno detectado antes. El orden de subida
    no debe verse afectado por esto, porque el turno ya quedó fijado en
    _scan(), antes de lanzar ningún hilo."""
    name_detected_first = "1 Pelicula A.mkv"
    name_detected_second_but_stabilizes_faster = "2 Pelicula B.mkv"
    names = [name_detected_first, name_detected_second_but_stabilizes_faster]
    for n in names:
        (tmp_path / n).write_bytes(b"contenido")

    upload_order = []
    tmdb = _make_tmdb()
    ftp = _make_ftp(upload_order)
    watcher = _make_watcher(tmp_path, tmdb, ftp)

    def _is_stable(path):
        # El detectado PRIMERO tarda más en estabilizarse que el segundo --
        # aun así debe subir primero.
        time.sleep(0.3 if "A" in path.name else 0.05)
        return True
    watcher._is_stable = _is_stable

    watcher._scan()

    deadline = time.monotonic() + 5
    while len(upload_order) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)

    assert upload_order == names, \
        f"deberian subir en orden de deteccion, pero subieron en: {upload_order}"


def test_ticket_not_consumed_does_not_block_the_queue(tmp_path):
    """Si un archivo reserva turno pero nunca llega a subir de verdad
    (aquí: confianza insuficiente), ese turno debe liberarse -- si no, el
    contador de turnos se queda parado ahí para siempre y NINGÚN archivo
    posterior (con un ticket mayor) puede subir jamás, aunque haya cupo
    libre."""
    name_fails_identification = "1 Pelicula Rara.mkv"
    name_should_still_upload = "2 Pelicula Normal.mkv"
    detection_order = [name_fails_identification, name_should_still_upload]
    for n in detection_order:
        (tmp_path / n).write_bytes(b"contenido")

    upload_order = []
    tmdb = MagicMock()

    def _search(query, *a, **kw):
        # Título totalmente distinto -> confianza muy baja para el primero.
        if "Rara" in query:
            return [{"id": 1, "title": "Algo Completamente Distinto", "media_type": "movie",
                      "release_date": "2024-01-01", "genre_ids": []}]
        return [{"id": 2, "title": query, "media_type": "movie",
                  "release_date": "2024-01-01", "genre_ids": []}]
    tmdb.search_multi.side_effect = _search
    tmdb.build_media_info.side_effect = lambda result, **kw: MediaInfo(
        tmdb_id=1, media_type="movie", title=result["title"],
        original_title=result["title"], year="2024", genre_ids=[])

    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 70,   # por defecto -- "Rara" vs "Algo Completamente Distinto" no lo pasa
        "rename_local": False,
        "ftp_host": "servidor",
        "ftp_parallel": 1,
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [],
        },
    })
    ftp = _make_ftp(upload_order)
    watcher = AutoWatcher(str(tmp_path), config, tmdb, ftp,
                           on_event=lambda *a, **k: None,
                           on_file_event=lambda *a, **k: None,
                           ftp_factory=lambda: ftp)
    watcher._is_stable = lambda path: True

    watcher._scan()

    deadline = time.monotonic() + 5
    while len(upload_order) < 1 and time.monotonic() < deadline:
        time.sleep(0.05)

    assert upload_order == [name_should_still_upload], (
        "el segundo archivo debería haber subido igualmente -- si esto falla, "
        "el ticket del primero (que nunca llegó a subir) se quedó sin liberar "
        "y bloqueó la cola entera")


def test_ticket_not_consumed_does_not_block_the_queue_when_file_vanishes(tmp_path):
    """Igual que el anterior, pero para una salida temprana DENTRO de
    _process() (archivo desaparecido tras la espera de estabilidad) -- ese
    camino no pasa por _upload_to_ftp() en absoluto, así que depende del
    finally de _process() para liberar el ticket."""
    vanishing_name = "1 Fantasma.mkv"
    normal_name = "2 Normal.mkv"
    vanishing_path = tmp_path / vanishing_name
    vanishing_path.write_bytes(b"contenido")
    (tmp_path / normal_name).write_bytes(b"contenido")

    upload_order = []
    tmdb = _make_tmdb()
    ftp = _make_ftp(upload_order)
    watcher = _make_watcher(tmp_path, tmdb, ftp)

    def _is_stable(path):
        if path.name == vanishing_name:
            path.unlink()   # desaparece justo tras "estabilizarse"
        return True
    watcher._is_stable = _is_stable

    watcher._scan()

    deadline = time.monotonic() + 5
    while len(upload_order) < 1 and time.monotonic() < deadline:
        time.sleep(0.05)

    assert upload_order == [normal_name], \
        "el archivo normal debería haber subido igualmente tras el que desapareció"
