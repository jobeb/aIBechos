"""Relleno de columnas (gui_qt/columns.py): sin vaivén infinito de barras
de desplazamiento. Real: la pestaña Archivos se colgaba (100 % de CPU) con
29 filas que llenaban justo el alto: llenar el ancho sin barra vertical
hacía asomar la horizontal, que quitaba alto, sobraba la vertical, se
iba, volvía el ancho... para siempre."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtGui import QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import QTableView  # noqa: E402

import gui_qt.columns as cols  # noqa: E402


def test_settle_width_rebote_entre_dos_anchos_usa_el_menor():
    h = []
    assert cols.settle_width(h, 0.0, 1000) == 1000
    assert cols.settle_width(h, 0.01, 983) == 983
    assert cols.settle_width(h, 0.02, 1000) == 1000
    assert cols.settle_width(h, 0.03, 983) == 983   # 3 cambios: aún no
    assert cols.settle_width(h, 0.04, 1000) == 983  # vaivén: el estrecho


def test_settle_width_arrastrar_a_mano_no_cuenta_como_vaiven():
    h = []
    for i, w in enumerate(range(900, 1000, 10)):
        assert cols.settle_width(h, i * 0.01, w) == w
    # Pasado el plazo, un ancho nuevo vuelve a mandar.
    h = [(0.0, 1000), (0.01, 983), (0.02, 1000), (0.03, 983)]
    assert cols.settle_width(h, 5.0, 1000) == 1000


class _Store(dict):
    def set(self, k, v):
        self[k] = v

    def save(self):
        pass


def test_tabla_archivos_no_entra_en_bucle(qtbot, monkeypatch):
    # Anchos guardados reales y el tamaño exacto en el que se colgaba.
    monkeypatch.setattr(cols, "_store", _Store({cols.CONFIG_KEY: {"files": {
        "widths": [667, 91, 229, 150, 80, 90, 64, 64, 30, 30, 100], "viewport": 1594}}}))
    m = QStandardItemModel(29, 11)
    for r in range(29):
        for c in range(11):
            m.setItem(r, c, QStandardItem("x"))
    v = QTableView()
    qtbot.addWidget(v)
    v.setModel(m)
    v.verticalHeader().hide()
    v.verticalHeader().setDefaultSectionSize(26)
    cols.fill_columns(v, 0, 2, key="files")
    v.horizontalHeader().setMinimumSectionSize(24)
    n = [0]

    class _Count(QObject):
        def eventFilter(self, o, e):
            if e.type() == QEvent.Resize:
                n[0] += 1
            return False
    counter = _Count()
    v.viewport().installEventFilter(counter)
    v.resize(900, 500)
    v.show()
    qtbot.wait(50)
    n[0] = 0
    v.resize(1372, 772)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.3:
        qtbot.wait(10)
    assert n[0] < 40, f"{n[0]} cambios de tamaño: la tabla rebota sin parar"
