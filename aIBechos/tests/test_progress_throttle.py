"""Estrangulado de ticks de progreso del watcher (ver
core/auto_watcher.py::_upload_tick_due): reenviar los miles de ticks por
bloque saturaba el hilo principal y congelaba filas y barras."""

from core.auto_watcher import _upload_tick_due


def test_primer_tick_siempre():
    assert _upload_tick_due(None, 0.0, 0.0, 1000.0) is True


def test_avance_mayor_o_igual_paso_emite():
    assert _upload_tick_due(0.50, 1000.0, 0.51, 1000.1) is True
    assert _upload_tick_due(0.50, 1000.0, 0.509, 1000.1) is False


def test_sin_avance_emite_como_mucho_cada_dos_segundos():
    assert _upload_tick_due(0.50, 1000.0, 0.505, 1001.9) is False
    assert _upload_tick_due(0.50, 1000.0, 0.505, 1002.0) is True


def test_basura_no_tumba():
    assert _upload_tick_due("x", "y", "z", "w") is True
