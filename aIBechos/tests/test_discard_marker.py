"""El marcador que escribe la GUI al quitar una fila a mano.

Complementa a test_auto_watcher_retry_limit.py, que cubre el LECTOR
(_should_process respeta "descartado"). Aquí se cubre el ESCRITOR, que hasta
ahora vivía dentro de gui/app.py::App y por tanto no se podía probar: App no
se puede instanciar sin tkinter, así que nadie comprobaba que lo que escribe
la GUI sea exactamente lo que el watcher espera leer.

El fallo que esto evita: quitas una fila, el siguiente escaneo la vuelve a
detectar, dispara el evento "start" y la fila reaparece sola.
"""

import json

import pytest

from core import auto_watcher
from core.auto_watcher import _PROTECTED_STATUSES, mark_discarded


@pytest.fixture
def db(tmp_path, monkeypatch):
    """auto_processed.json aislado -- nunca el real del usuario."""
    p = tmp_path / "auto_processed.json"
    monkeypatch.setattr(auto_watcher, "_processed_db_path", lambda: p)
    return p


def _read(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _watcher_sees(p, key):
    """¿Volvería el watcher a procesar *key*? (lo que decide si la fila
    reaparece en la lista)."""
    w = auto_watcher.AutoWatcher.__new__(auto_watcher.AutoWatcher)
    w._processed = w._load_db()
    w._in_progress = set()
    w._pending_attempts = {}
    w._logged_skips = set()
    return w._should_process(key, "loquesea.mkv")


def test_escribe_la_marca_en_un_archivo_sin_entrada_previa(db):
    key = r"C:\vigilada\peli.mkv"

    assert mark_discarded(key) is True
    assert _read(db)[key]["status"] == "descartado"


def test_el_watcher_deja_de_procesarlo(db):
    """La prueba de fuego: exactamente lo que fallaba."""
    key = r"C:\vigilada\no lo quiero.mkv"
    assert _watcher_sees(db, key) is True      # antes de quitarlo, se procesa

    mark_discarded(key)

    assert _watcher_sees(db, key) is False     # después, ya no


def test_pisa_un_estado_de_fallo_no_protegido(db):
    """El caso real: un archivo que el watcher reintenta porque no lo
    identifica es justo el que el usuario acaba quitando a mano."""
    key = r"C:\vigilada\parodia inexistente.mkv"
    db.write_text(json.dumps({key: {"status": "sin_resultados", "attempts": 2}}),
                  encoding="utf-8")

    assert mark_discarded(key) is True
    assert _read(db)[key]["status"] == "descartado"
    assert _watcher_sees(db, key) is False


@pytest.mark.parametrize("protegido", _PROTECTED_STATUSES)
def test_no_pisa_un_estado_protegido(db, protegido):
    """Un archivo ya subido/identificado a mano ya hace que el watcher lo
    ignore, y su estado lleva información que no conviene perder."""
    key = r"C:\vigilada\ya gestionado.mkv"
    db.write_text(json.dumps({key: {"status": protegido, "new_name": "X.mkv"}}),
                  encoding="utf-8")

    assert mark_discarded(key) is False
    assert _read(db)[key]["status"] == protegido
    assert _read(db)[key]["new_name"] == "X.mkv"


def test_no_toca_las_entradas_de_los_demas_archivos(db):
    otros = {
        r"C:\vigilada\a.mkv": {"status": "subido", "new_name": "A.mkv"},
        r"C:\vigilada\b.mkv": {"status": "baja_confianza"},
    }
    db.write_text(json.dumps(otros), encoding="utf-8")

    mark_discarded(r"C:\vigilada\c.mkv")

    data = _read(db)
    assert len(data) == 3
    assert data[r"C:\vigilada\a.mkv"] == otros[r"C:\vigilada\a.mkv"]
    assert data[r"C:\vigilada\b.mkv"] == otros[r"C:\vigilada\b.mkv"]


def test_sobrevive_a_que_el_watcher_marque_despues_un_fallo(db):
    """Secuencia real: el watcher está procesando el archivo (ya borró su
    entrada para reintentarlo) cuando el usuario pulsa la ✕. El resultado
    tardío de ese hilo NO debe borrar la marca."""
    key = r"C:\vigilada\en curso.mkv"
    mark_discarded(key)

    w = auto_watcher.AutoWatcher.__new__(auto_watcher.AutoWatcher)
    w._processed = {}
    w._in_progress = {key}
    w._pending_attempts = {}
    w._logged_skips = set()
    w._mark(key, "sin_resultados")

    assert _read(db)[key]["status"] == "descartado"
    assert _watcher_sees(db, key) is False


def test_una_subida_correcta_si_puede_pisar_el_descarte(db):
    """Al revés que el anterior: si el archivo acaba subiéndose de verdad,
    ese sí es un estado que debe prevalecer."""
    key = r"C:\vigilada\acaba subiendose.mkv"
    mark_discarded(key)

    w = auto_watcher.AutoWatcher.__new__(auto_watcher.AutoWatcher)
    w._processed = {}
    w._in_progress = {key}
    w._pending_attempts = {}
    w._logged_skips = set()
    w._mark(key, "subido", new_name="Peli (2024).mkv")

    assert _read(db)[key]["status"] == "subido"


def test_un_json_corrupto_no_impide_marcar(db):
    db.write_text("{esto no es json", encoding="utf-8")

    assert mark_discarded(r"C:\vigilada\x.mkv") is True
    assert _read(db)[r"C:\vigilada\x.mkv"]["status"] == "descartado"
