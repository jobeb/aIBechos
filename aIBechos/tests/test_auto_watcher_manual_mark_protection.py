"""AutoWatcher no debe pisar en disco un estado que la GUI ya fijó a mano
(p.ej. "identificado_manual" tras asignar el TMDB ID a mano) con un
resultado propio obsoleto -- ver AutoWatcher._mark() y el bug real: tras
identificar a mano un archivo de baja confianza, el mensaje "Confianza
insuficiente" seguía reapareciendo cada ciclo porque _save_db() volcaba
TODO el _processed en memoria sin fusionar con la marca manual del disco."""

import json
import threading

import pytest
from unittest.mock import MagicMock

import core.auto_watcher as autowatcher_mod
from core.auto_watcher import AutoWatcher, _PROTECTED_STATUSES
from core.api_client import MediaInfo


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "auto_processed_test.json"
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: p)
    return p


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, min_confidence=50):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": min_confidence,
        "ftp_host": "servidor",
        "ftp_categories": {"movie": [], "tv": []},
    })
    tmdb = MagicMock()
    # Título muy distinto del detectado localmente -> confianza baja.
    tmdb.search_multi.return_value = [{
        "id": 1, "name": "Trollhunters: Cuentos de Arcadia", "media_type": "tv",
        "first_air_date": "2016-01-01", "genre_ids": [],
    }]
    tmdb.build_media_info.return_value = MediaInfo(
        tmdb_id=1, media_type="tv", title="Trollhunters: Cuentos de Arcadia",
        original_title="Trollhunters", year="2016", genre_ids=[])

    watcher = AutoWatcher(str(folder), config, tmdb, MagicMock(),
                           on_event=lambda *a, **k: None,
                           on_file_event=lambda *a, **k: None)
    watcher._is_stable = lambda path: True
    return watcher


def test_low_confidence_marks_baja_confianza_when_nothing_protected(tmp_path, db_path):
    original = tmp_path / "Serie Completamente Distinta 1x01.mkv"
    original.write_bytes(b"contenido")
    watcher = _make_watcher(tmp_path)

    watcher._process(original)

    db = json.loads(db_path.read_text(encoding="utf-8"))
    assert db[str(original)]["status"] == "baja_confianza"
    assert str(original) not in watcher._in_progress


def test_low_confidence_does_not_overwrite_manual_identification(tmp_path, db_path):
    """Simula la asignación manual desde la GUI (_mark_auto_processed)
    ocurriendo mientras un _process() con detección propia obsoleta todavía
    está en marcha para el mismo archivo -- el resultado tardío no debe
    pisar la marca manual en disco."""
    original = tmp_path / "Serie Completamente Distinta 1x01.mkv"
    original.write_bytes(b"contenido")
    key = str(original)

    db_path.write_text(json.dumps({
        key: {"status": "identificado_manual", "new_name": "Trollhunters (2016).mkv", "ts": 0}
    }), encoding="utf-8")

    watcher = _make_watcher(tmp_path)
    # AutoWatcher ya tenía este archivo "en marcha" antes de reprocesar --
    # su copia en memoria de _processed no incluye todavía la marca manual
    # (solo se recarga al principio de _scan(), no dentro de _process()).
    watcher._in_progress.add(key)

    watcher._process(original)

    db = json.loads(db_path.read_text(encoding="utf-8"))
    assert db[key]["status"] == "identificado_manual", \
        "la marca manual no debería haberse perdido"
    assert key not in watcher._in_progress


def test_protected_statuses_include_identificado_manual():
    assert "identificado_manual" in _PROTECTED_STATUSES


def test_already_protected_file_aborts_before_any_tmdb_call_or_event(tmp_path, db_path):
    """No basta con que _mark() descarte el resultado tardío en disco: si el
    archivo ya está protegido, _process() no debe ni llegar a buscar en TMDB
    ni disparar el evento "skip" -- si no, la fila vuelve a parpadear a
    "Omitido" un instante (aunque no llegue a persistirse), y hacía falta
    identificar el mismo archivo a mano DOS veces para que la tabla se
    quedara quieta de verdad."""
    original = tmp_path / "Serie Completamente Distinta 1x01.mkv"
    original.write_bytes(b"contenido")
    key = str(original)

    db_path.write_text(json.dumps({
        key: {"status": "identificado_manual", "new_name": "Trollhunters (2016).mkv", "ts": 0}
    }), encoding="utf-8")

    events = []
    watcher = _make_watcher(tmp_path)
    watcher.on_event = lambda tipo, msg: events.append(("event", tipo, msg))
    watcher.on_file_event = lambda path, tipo, **kw: events.append(("file_event", tipo, kw))
    watcher._in_progress.add(key)

    watcher._process(original)

    watcher.tmdb.search_multi.assert_not_called()
    assert events == [], f"no debería haberse disparado ningún evento, pero se disparó: {events}"
    assert key not in watcher._in_progress


def test_marking_one_file_does_not_erase_a_different_files_manual_mark(tmp_path, db_path):
    """Bug real: mientras _process() esperaba la estabilidad/TMDB de UN
    archivo (varios segundos), la GUI marcaba a mano OTRO archivo distinto
    (identificado/subido manualmente) directamente en el JSON. El _mark()
    de este hilo, al terminar, volcaba TODO self._processed (desactualizado
    desde el último _scan(), que es lo único que lo sincroniza con disco)
    y borraba sin querer esa marca reciente de un archivo que ni siquiera
    tocaba -- así que tras reiniciar la app, AutoWatcher volvía a detectar
    como "nuevos" archivos que ya se habían subido a mano."""
    original = tmp_path / "Serie Completamente Distinta 1x01.mkv"
    original.write_bytes(b"contenido")
    key = str(original)

    watcher = _make_watcher(tmp_path)
    # self._processed en memoria NO tiene todavía la marca del otro
    # archivo -- solo se sincroniza con disco al principio de _scan(),
    # no mientras _process() está en marcha.
    assert watcher._processed == {}

    other_key = str(tmp_path / "Otra Serie Ya Subida A Mano.mkv")
    db_path.write_text(json.dumps({
        other_key: {"status": "subido", "new_name": "Otra Serie.mkv", "ts": 0}
    }), encoding="utf-8")

    watcher._process(original)

    db = json.loads(db_path.read_text(encoding="utf-8"))
    assert db[key]["status"] == "baja_confianza"
    assert db.get(other_key, {}).get("status") == "subido", \
        "la marca 'subido' de un archivo distinto no debería haberse perdido"


def test_concurrent_save_entry_does_not_lose_updates(tmp_path, db_path):
    """Bug real: "Subidas simultáneas" > 1 hace que varias subidas manuales
    (gui/app.py::_mark_auto_processed) o una subida manual y AutoWatcher
    (_save_entry) terminen casi a la vez, cada una con su propio
    leer-modificar-escribir sin coordinarse -- sin _DB_LOCK, la última en
    escribir parte de una lectura ya desactualizada y borra las marcas que
    las demás acababan de guardar. Con muchos hilos guardando cada uno su
    propia clave a la vez, todas deben sobrevivir."""
    watcher = _make_watcher(tmp_path)
    n = 30
    threads = [
        threading.Thread(target=watcher._save_entry,
                          args=(f"archivo_{i}.mkv", {"status": "subido", "new_name": "", "ts": 0}))
        for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db = json.loads(db_path.read_text(encoding="utf-8"))
    assert len(db) == n, f"se perdieron marcas: esperaba {n}, quedaron {len(db)}"
