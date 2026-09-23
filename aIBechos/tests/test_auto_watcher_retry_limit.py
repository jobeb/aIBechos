"""El vigilante no debe reintentar para siempre un archivo que nunca se va a
identificar (caso real: una parodia que no existe en TMDB, reintentada cada
5-10 s indefinidamente), ni volver a meter en la lista uno que el usuario ha
quitado a mano."""
import json

import pytest

from core import auto_watcher
from core.auto_watcher import _MAX_RETRY_ATTEMPTS, _PROTECTED_STATUSES


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    """AutoWatcher con su base de datos aislada en tmp_path -- nunca el
    auto_processed.json real del usuario."""
    db = tmp_path / "auto_processed.json"
    monkeypatch.setattr(auto_watcher, "_processed_db_path", lambda: db)
    w = auto_watcher.AutoWatcher.__new__(auto_watcher.AutoWatcher)
    w._processed = {}
    w._in_progress = set()
    w._pending_attempts = {}
    w._logged_skips = set()
    return w


def _cycle(w, key, name="peli.mp4", status="sin_resultados"):
    """Un ciclo de escaneo: decide si procesar y, si procesa, vuelve a
    marcarlo como fallido (que es lo que pasa de verdad con este archivo)."""
    if not w._should_process(key, name):
        return False
    w._mark(key, status)
    return True


def test_unidentifiable_file_stops_being_retried(watcher, monkeypatch):
    monkeypatch.setattr(watcher, "_save_entry", lambda k, e: None)
    monkeypatch.setattr(watcher, "_delete_entry", lambda k: None)
    monkeypatch.setattr(watcher, "_load_db", lambda: {})
    key = "/vigilada/Parody Porn Wars 1 (Version Porno Star Wars).LoPaH.mp4"

    procesados = sum(_cycle(watcher, key) for _ in range(20))

    # 1 intento inicial + los reintentos permitidos, y ni uno más:
    # antes de esto los 20 ciclos procesaban las 20 veces, sin fin.
    assert procesados == _MAX_RETRY_ATTEMPTS + 1
    assert watcher._processed[key]["attempts"] == _MAX_RETRY_ATTEMPTS


def test_attempt_count_survives_the_entry_being_deleted(watcher, monkeypatch):
    # _should_process borra la entrada para reprocesar; si la cuenta se
    # perdiera ahí, el contador volvería a 0 y el tope no serviría de nada.
    monkeypatch.setattr(watcher, "_save_entry", lambda k, e: None)
    monkeypatch.setattr(watcher, "_delete_entry", lambda k: None)
    monkeypatch.setattr(watcher, "_load_db", lambda: {})
    key = "/vigilada/otra.mp4"

    _cycle(watcher, key)
    assert "attempts" not in watcher._processed[key]   # primer intento, aún sin reintentos
    _cycle(watcher, key)
    assert watcher._processed[key]["attempts"] == 1
    _cycle(watcher, key)
    assert watcher._processed[key]["attempts"] == 2


def test_success_does_not_carry_an_attempt_count(watcher, monkeypatch):
    monkeypatch.setattr(watcher, "_save_entry", lambda k, e: None)
    monkeypatch.setattr(watcher, "_delete_entry", lambda k: None)
    monkeypatch.setattr(watcher, "_load_db", lambda: {})
    key = "/vigilada/buena.mp4"

    _cycle(watcher, key)                       # falla una vez
    _cycle(watcher, key, status="subido")      # y a la siguiente se sube bien
    assert "attempts" not in watcher._processed[key]
    assert watcher._should_process(key, "buena.mp4") is False


def test_discarded_status_is_protected_so_the_watcher_leaves_it_alone(watcher):
    # Lo que escribe la GUI al quitar una fila a mano (ver
    # gui/app.py::_discard_from_auto_watcher).
    assert "descartado" in _PROTECTED_STATUSES
    key = "/vigilada/no lo quiero.mp4"
    watcher._processed[key] = {"status": "descartado"}
    assert watcher._should_process(key, "no lo quiero.mp4") is False


def test_discard_marker_is_written_to_the_db(tmp_path, monkeypatch):
    """El marcador que escribe la GUI debe ser legible por el watcher."""
    db = tmp_path / "auto_processed.json"
    db.write_text(json.dumps({"/x/ya subido.mp4": {"status": "subido"}}), encoding="utf-8")
    monkeypatch.setattr(auto_watcher, "_processed_db_path", lambda: db)

    data = json.loads(db.read_text(encoding="utf-8"))
    data["/x/descartado.mp4"] = {"status": "descartado", "new_name": "", "ts": 0}
    db.write_text(json.dumps(data), encoding="utf-8")

    w = auto_watcher.AutoWatcher.__new__(auto_watcher.AutoWatcher)
    w._processed = w._load_db()
    w._in_progress = set()
    w._pending_attempts = {}
    w._logged_skips = set()
    assert w._should_process("/x/descartado.mp4", "descartado.mp4") is False
    # y no debe haber pisado el estado del que ya estaba subido
    assert w._processed["/x/ya subido.mp4"]["status"] == "subido"
