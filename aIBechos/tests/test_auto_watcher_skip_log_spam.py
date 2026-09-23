"""El log del watcher no puede repetir el mismo "Saltado" en cada ciclo.

El fallo real que esto evita: _should_process escribía una línea por cada
archivo ya procesado en CADA escaneo. Con 80 archivos en la carpeta vigilada
y un ciclo de 10 s son ~480 líneas por minuto, así que auto_watcher.log
llegaba a los 2 MB de la rotación (core/applog.py) en unos 10 minutos y se
llevaba por delante todo el historial anterior -- subidas, renombrados y
errores incluidos. Al pedirle el log a un usuario para diagnosticar un
problema de subidas llegaban 4.901 líneas de las que 4.661 eran
"Saltado (subido)", cubriendo diez minutos y ninguna subida: parecía que la
app no registraba nada cuando en realidad se había borrado a sí misma la
prueba.
"""

import logging

import pytest

from core import auto_watcher


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    """AutoWatcher sin __init__ (necesita config/FTP), con solo el estado que
    usa _should_process -- mismo atajo que test_discard_marker.py."""
    monkeypatch.setattr(auto_watcher, "_processed_db_path",
                        lambda: tmp_path / "auto_processed.json")
    w = auto_watcher.AutoWatcher.__new__(auto_watcher.AutoWatcher)
    w._processed = {}
    w._in_progress = set()
    w._pending_attempts = {}
    w._logged_skips = set()
    return w


def _skip_lines(caplog):
    return [r.message for r in caplog.records if r.message.startswith("Saltado")]


def test_un_archivo_ya_subido_solo_se_registra_una_vez(watcher, caplog):
    key = r"C:\vigilada\serie 1x01.mkv"
    watcher._processed[key] = {"status": "subido"}

    with caplog.at_level(logging.DEBUG, logger="aIBechos.auto"):
        for _ in range(50):          # 50 ciclos de escaneo seguidos
            assert watcher._should_process(key, "serie 1x01.mkv") is False

    assert _skip_lines(caplog) == ["Saltado (subido): serie 1x01.mkv"]


def test_cada_archivo_conserva_su_propia_linea(watcher, caplog):
    for n in (1, 2, 3):
        watcher._processed[rf"C:\vigilada\serie 1x0{n}.mkv"] = {"status": "subido"}

    with caplog.at_level(logging.DEBUG, logger="aIBechos.auto"):
        for _ in range(10):
            for n in (1, 2, 3):
                watcher._should_process(rf"C:\vigilada\serie 1x0{n}.mkv",
                                        f"serie 1x0{n}.mkv")

    assert len(_skip_lines(caplog)) == 3


def test_si_cambia_el_motivo_se_vuelve_a_registrar(watcher, caplog):
    """Un motivo distinto sí es información nueva: "en proceso" -> "subido"
    cuenta la historia de un archivo que terminó, y eso debe quedar en el log."""
    key = r"C:\vigilada\serie 1x01.mkv"
    watcher._in_progress.add(key)

    with caplog.at_level(logging.DEBUG, logger="aIBechos.auto"):
        watcher._should_process(key, "serie 1x01.mkv")
        watcher._should_process(key, "serie 1x01.mkv")     # mismo motivo, no repite
        watcher._in_progress.discard(key)
        watcher._processed[key] = {"status": "subido"}
        watcher._should_process(key, "serie 1x01.mkv")

    assert _skip_lines(caplog) == [
        "Saltado (en proceso): serie 1x01.mkv",
        "Saltado (subido): serie 1x01.mkv",
    ]
