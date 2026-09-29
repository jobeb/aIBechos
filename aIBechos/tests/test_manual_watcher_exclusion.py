"""La subida manual debe omitir archivos que el modo automático está
procesando ahora mismo (misma ruta en core/auto_watcher.py::_in_progress):
sin esto las dos subidas abrían el mismo archivo a la vez y una fallaba
al desaparecer el archivo bajo sus pies ("No se pudo abrir", "Archivo
local no encontrado"). Típico con adelgazados en Incoming (= vigilada).

Solo se prueba App._auto_is_processing con dobles mínimos (sin
construir la GUI): la decisión de omitir vive ahí; _upload_entry_with
la usa tal cual al empezar cada archivo.
"""

import json
import os
from types import SimpleNamespace

import pytest

import core.auto_watcher as autowatcher_mod
import core.slim_pending as slim_pending_mod
import gui.app as appmod
from core.path_key import canon_path
from gui.app import App, _file_status_text


def _stub(in_progress=None, running=True):
    watcher = SimpleNamespace(running=running, _in_progress=set(in_progress or ()))
    return SimpleNamespace(_watcher=watcher)


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def test_misma_ruta_en_proceso_se_detecta():
    app = _stub({_norm(r"C:\Users\Jose\Downloads\eMule\Incoming\1x01 foo.avi")})
    assert App._auto_is_processing(app, r"C:\Users\Jose\Downloads\eMule\Incoming\1x01 foo.avi") is True


def test_ruta_distinta_no_se_detecta():
    app = _stub({_norm(r"C:\Users\Jose\Downloads\eMule\Incoming\1x01 foo.avi")})
    assert App._auto_is_processing(app, r"C:\Users\Jose\Downloads\eMule\Incoming\1x02 bar.avi") is False


def test_comparacion_insensible_a_separadores_y_mayusculas():
    app = _stub({"c:/users/jose/downloads/emule/incoming/1x01 foo.avi"})
    assert App._auto_is_processing(app, r"C:\Users\Jose\Downloads\eMule\Incoming\1X01 FOO.AVI") is True


def test_sin_watcher_o_detenido_no_bloquea():
    assert App._auto_is_processing(SimpleNamespace(_watcher=None), "C:\\x.avi") is False
    app = _stub({_norm("C:\\x.avi")}, running=False)
    assert App._auto_is_processing(app, "C:\\x.avi") is False


def test_ruta_vacia_no_bloquea():
    app = _stub({_norm("C:\\x.avi")})
    assert App._auto_is_processing(app, "") is False
    assert App._auto_is_processing(app, None) is False


# ---- entrega crítica de callbacks (filas que si no quedan desincronizadas)


def _clock(monkeypatch):
    state = {"t": 1000.0}

    def fake_monotonic():
        return state["t"]

    def fake_sleep(s):
        state["t"] += s

    import time as _time_mod
    monkeypatch.setattr(_time_mod, "monotonic", fake_monotonic)
    monkeypatch.setattr(_time_mod, "sleep", fake_sleep)
    return state


def test_critical_retries_until_main_thread_responds(monkeypatch):
    _clock(monkeypatch)
    calls = {"n": 0}
    done = []

    def fake_after(ms, func):
        calls["n"] += 1
        if calls["n"] < 4:
            raise RuntimeError("main thread is not in main loop")
        done.append(True)
        func()

    stub = SimpleNamespace(after=fake_after)
    assert App._after_from_worker(stub, lambda: None, critical=True) is True
    assert done == [True]
    assert calls["n"] == 4


def test_noncritical_gives_up_after_timeout(monkeypatch):
    _clock(monkeypatch)
    calls = {"n": 0}

    def fake_after(ms, func):
        calls["n"] += 1
        raise RuntimeError("main thread is not in main loop")

    stub = SimpleNamespace(after=fake_after)
    assert App._after_from_worker(stub, lambda: None, timeout_s=0.2) is False
    assert calls["n"] > 1  # reintentó antes de rendirse


# ---- etiqueta Adelgazando (solo muestra; el estado interno no cambia)


def test_file_status_text_adelgazando_solo_slim_subiendo():
    assert _file_status_text(SimpleNamespace(status="subiendo", is_slim=True)) == "Adelgazando"
    assert _file_status_text(SimpleNamespace(status="subiendo", is_slim=False)) == "Subiendo"
    assert _file_status_text(SimpleNamespace(status="subiendo")) == "Subiendo"
    assert _file_status_text(SimpleNamespace(status="en_cola", is_slim=True)) == "En cola"
    assert _file_status_text(SimpleNamespace(status="listo", is_slim=True)) == "Listo"


# ---- renombrado manual de un ligero pendiente no lo bloquea


def _slim_setup(tmp_path, monkeypatch):
    pend_path = tmp_path / "slim_pending_test.json"
    monkeypatch.setattr(slim_pending_mod, "_path", lambda: pend_path)
    db_path = tmp_path / "auto_processed_test.json"
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: db_path)
    return db_path


def _plain(name):
    """La función pelada tras un @staticmethod de App (App.X da la
    función, pero asignarla como atributo de clase la volvería a enlazar
    y le pasaría el stub como primer argumento)."""
    return App.__dict__[name].__func__


class _GuiStub:
    """Doble mínimo de App para _mark/_unmark/_reserve/_release."""
    _db_key = staticmethod(_plain("_db_key"))
    _restore_or_delete_entry = staticmethod(_plain("_restore_or_delete_entry"))

    def __init__(self):
        self._slim_pending_for_path = lambda path: App._slim_pending_for_path(self, path)


def _gui():
    return _GuiStub()


def _slim_file(tmp_path, name="Mi Serie 1x06 ligera.mkv", size=800):
    from core.api_client import detect_episode
    from core.slim_pending import norm_key, record
    import time as _t
    f = tmp_path / name
    f.write_bytes(b"x" * size)
    det = detect_episode(name) or {}
    record({
        "media_type": "tv", "key_norm": norm_key(det.get("title", "")),
        "season": det.get("season"), "episode": det.get("episode"), "year": "",
        "heavy_remote_file": "/series/gordo.mkv", "heavy_size": 1000,
        "max_light_size": 850, "query": "q", "added_ts": _t.time(),
        "added_by": "tester",
    })
    return f


def _read_db(db_path):
    import json as _json
    return _json.loads(db_path.read_text(encoding="utf-8")) if db_path.exists() else {}


def test_renombrado_de_ligero_pendiente_no_protege(tmp_path, monkeypatch):
    db_path = _slim_setup(tmp_path, monkeypatch)
    f = _slim_file(tmp_path)

    App._mark_auto_processed(_gui(), str(f), "renombrado", "Mi Serie 1x06.mkv")

    assert _read_db(db_path) == {}, "el pendiente debe seguir visible para el automático"


def test_renombrado_sin_pendiente_si_protege(tmp_path, monkeypatch):
    db_path = _slim_setup(tmp_path, monkeypatch)
    f = tmp_path / "Otra Serie 2x03.mkv"
    f.write_bytes(b"x" * 800)

    App._mark_auto_processed(_gui(), str(f), "renombrado", "Otra Serie 2x03.mkv")

    assert _read_db(db_path)[canon_path(str(f))]["status"] == "renombrado"


def test_subiendo_de_ligero_pendiente_si_protege(tmp_path, monkeypatch):
    """La exención es solo para renombrado/identificado: una subida manual
    en curso sí reserva el archivo (si no, competirían)."""
    db_path = _slim_setup(tmp_path, monkeypatch)
    f = _slim_file(tmp_path)

    App._mark_auto_processed(_gui(), str(f), "subiendo", "Mi Serie 1x06.mkv")

    assert _read_db(db_path)[canon_path(str(f))]["status"] == "subiendo"


# ---- reserva temprana en_cola_manual


def test_en_cola_manual_es_protegido_y_transitorio(tmp_path, monkeypatch):
    """La reserva guarda el previo, pero ni se restaura como previo ni
    como estado: al soltarse desaparece (ver _restore_or_delete_entry)."""
    db_path = _slim_setup(tmp_path, monkeypatch)
    stub = _gui()
    f = tmp_path / "Serie 5x01.mkv"
    f.write_bytes(b"x" * 10)

    App._mark_auto_processed(stub, str(f), "identificado_manual", "Serie 5x01.mkv")
    App._mark_auto_processed(stub, str(f), "en_cola_manual", "Serie 5x01.mkv")
    key = canon_path(str(f))
    assert _read_db(db_path)[key]["status"] == "en_cola_manual"
    assert _read_db(db_path)[key]["prev_status"] == "identificado_manual"
    assert "en_cola_manual" in autowatcher_mod._PROTECTED_STATUSES

    App._unmark_auto_processed(stub, str(f))
    assert _read_db(db_path)[key]["status"] == "identificado_manual"

    # ...pero si lo previo era otra reserva transitoria, se borra.
    App._mark_auto_processed(stub, str(f), "en_cola_manual", "Serie 5x01.mkv")
    App._mark_auto_processed(stub, str(f), "subiendo", "Serie 5x01.mkv")
    App._unmark_auto_processed(stub, str(f))
    assert key not in _read_db(db_path)


def test_restore_or_delete_entry_nunca_restaura_transitorio():
    from gui.app import App as _App
    db = {"k": {"status": "en_cola_manual", "new_name": "", "ts": 0}}
    _App._restore_or_delete_entry(db, "k")
    assert db == {}


def test_reserve_marks_batch_skipping_unidentified(tmp_path, monkeypatch):
    """La reserva es en una sola pasada, guarda el previo y no toca a
    los no identificados (que la manual no puede subir de todas formas)."""
    db_path = _slim_setup(tmp_path, monkeypatch)
    stub = _gui()
    ok = tmp_path / "Serie 5x02.mkv"
    ok.write_bytes(b"x" * 10)
    raw = tmp_path / "SinIdentificar.mkv"
    raw.write_bytes(b"x" * 10)
    e_ok = SimpleNamespace(path=str(ok), new_name="Serie 5x02.mkv", media_info=object())
    e_raw = SimpleNamespace(path=str(raw), new_name="", media_info=None)

    App._mark_auto_processed(stub, str(ok), "identificado_manual", "Serie 5x02.mkv")

    class _ReserveStub:
        """Doble mínimo (ambos helpers solo usan _db_key)."""
        _db_key = staticmethod(_plain("_db_key"))

    App._reserve_manual_entries(_ReserveStub(), [e_ok, e_raw])
    data = _read_db(db_path)
    assert data[canon_path(str(ok))]["status"] == "en_cola_manual"
    assert data[canon_path(str(ok))]["prev_status"] == "identificado_manual"
    assert canon_path(str(raw)) not in data


def test_release_only_touches_leftovers(tmp_path, monkeypatch):
    db_path = _slim_setup(tmp_path, monkeypatch)
    stub = _gui()
    a = tmp_path / "A.mkv"
    a.write_bytes(b"x" * 10)
    b = tmp_path / "B.mkv"
    b.write_bytes(b"x" * 10)
    App._mark_auto_processed(stub, str(a), "en_cola_manual", "A.mkv")
    App._mark_auto_processed(stub, str(b), "subido", "B.mkv")

    class _RelStub:
        _db_key = staticmethod(_plain("_db_key"))
        _restore_or_delete_entry = staticmethod(_plain("_restore_or_delete_entry"))

    App._release_manual_entries(_RelStub(), [
        SimpleNamespace(path=str(a)), SimpleNamespace(path=str(b))])
    data = _read_db(db_path)
    assert canon_path(str(a)) not in data
    assert data[canon_path(str(b))]["status"] == "subido"
