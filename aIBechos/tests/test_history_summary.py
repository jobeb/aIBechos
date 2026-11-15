"""Historial: resumen de lo subido (total, hoy, 7 días) y peso legible."""

import time

from gui_qt.history.tab import _size, upload_summary

GB = 1024 ** 3


def test_resumen_de_subidas():
    now = time.time()
    rows = [{"status": "ok", "size": 2 * GB, "ts": now},
            {"status": "ok", "size": 1 * GB, "ts": now - 3 * 86400},
            {"status": "ok", "size": 4 * GB, "ts": now - 30 * 86400},
            {"status": "error", "size": 9 * GB, "ts": now},           # no cuenta
            {"kind": "borrado", "status": "ok", "size": 9 * GB, "ts": now}]
    text = upload_summary(rows, now=now)
    assert text.startswith("3 subidas correctas")
    assert _size(7 * GB) in text and f"hoy {_size(2 * GB)}" in text
    assert f"últimos 7 días {_size(3 * GB)}" in text
    assert upload_summary([{"status": "error"}]) == ""


def test_peso_pasa_de_mb():
    assert "MB" not in _size(1400 * GB)
    assert _size(None) == _size(0)
