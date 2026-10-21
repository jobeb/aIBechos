"""Paneles divididos y pestaña abierta recordados (gui_qt/ui_state.py)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSplitter, QTabWidget

from gui_qt import columns, ui_state


class _FakeConfig:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.saves = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1


def _splitter(qtbot, key):
    s = QSplitter(Qt.Horizontal)
    s.addWidget(QLabel("tabla"))
    s.addWidget(QLabel("panel"))
    s.setStretchFactor(0, 1)
    s.setSizes([1000, 290])
    ui_state.remember_splitter(s, key)
    qtbot.addWidget(s)
    s.resize(1000, 300)
    s.show()
    qtbot.waitExposed(s)
    return s


def test_el_reparto_del_panel_se_recuerda(qtbot, monkeypatch):
    cfg = _FakeConfig()
    monkeypatch.setattr(columns, "_store", cfg)
    s = _splitter(qtbot, "files")
    assert cfg.saves == 0                       # el de partida no cuenta
    s.moveSplitter(600, 1)                      # como al arrastrar
    ui_state.flush_pending()
    guardado = cfg.data["splitter_sizes"]["files"]
    assert guardado == s.sizes()
    s2 = _splitter(qtbot, "files")              # siguiente arranque
    assert s2.sizes() == guardado


def test_reparto_guardado_con_otro_numero_de_paneles_se_ignora(qtbot, monkeypatch):
    monkeypatch.setattr(columns, "_store", _FakeConfig({"splitter_sizes": {"files": [1, 2, 3]}}))
    s = _splitter(qtbot, "files")
    assert s.sizes()[1] > 100


def _tabs(qtbot, key):
    t = QTabWidget()
    for name in ("a", "b", "c"):
        t.addTab(QLabel(name), name)
    ui_state.remember_tab(t, key, ["a", "b", "c"])
    qtbot.addWidget(t)
    return t


def test_la_pestana_abierta_se_recuerda(qtbot, monkeypatch):
    cfg = _FakeConfig()
    monkeypatch.setattr(columns, "_store", cfg)
    t = _tabs(qtbot, "info")
    assert t.currentIndex() == 0
    t.setCurrentIndex(2)
    assert cfg.data["last_tabs"]["info"] == "c"
    assert _tabs(qtbot, "info").currentIndex() == 2


def test_pestana_guardada_que_ya_no_existe_se_ignora(qtbot, monkeypatch):
    monkeypatch.setattr(columns, "_store", _FakeConfig({"last_tabs": {"info": "borrada"}}))
    assert _tabs(qtbot, "info").currentIndex() == 0
