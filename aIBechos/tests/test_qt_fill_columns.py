"""Columnas de relleno arrastrables (gui_qt/columns.py).

Real: con QHeaderView.Stretch, "Nombre original" no se podía arrastrar (no
salía el cursor ↔) y ensanchar "Detectado" la estrechaba a ella."""

from pathlib import Path

from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QHeaderView, QTableView, QTreeView

from gui_qt.columns import MIN_FILL_WIDTH, fill_columns


def _view(qtbot, cls=QTableView, cols=4, width=700):
    v = cls()
    v.setModel(QStandardItemModel(3, cols))
    qtbot.addWidget(v)
    hdr = v.horizontalHeader() if cls is QTableView else v.header()
    return v, hdr


def test_todas_arrastrables_y_la_de_relleno_llena_el_ancho(qtbot):
    v, hdr = _view(qtbot)
    fill_columns(v, 1)
    v.resize(700, 300)
    v.show()
    qtbot.waitExposed(v)
    for c in range(hdr.count()):
        assert hdr.sectionResizeMode(c) == QHeaderView.Interactive
    assert hdr.length() == v.viewport().width()


def test_arrastrar_una_columna_no_cambia_las_demas(qtbot):
    v, hdr = _view(qtbot)
    fill_columns(v, 1)
    v.resize(700, 300)
    v.show()
    qtbot.waitExposed(v)
    antes = [hdr.sectionSize(c) for c in range(4)]
    hdr.resizeSection(2, antes[2] + 60)       # como al arrastrar el borde
    despues = [hdr.sectionSize(c) for c in range(4)]
    assert despues[2] == antes[2] + 60
    assert despues[0] == antes[0] and despues[1] == antes[1] and despues[3] == antes[3]
    hdr.resizeSection(1, antes[1] - 40)       # la de relleno también se arrastra
    assert hdr.sectionSize(1) == antes[1] - 40


def test_al_cambiar_el_ancho_de_la_tabla_absorben_las_de_relleno(qtbot):
    v, hdr = _view(qtbot, QTreeView, cols=5)
    fill_columns(v, 0, 2)
    v.resize(800, 300)
    v.show()
    qtbot.waitExposed(v)
    fijas = [hdr.sectionSize(c) for c in (1, 3, 4)]
    v.resize(1000, 300)
    qtbot.waitUntil(lambda: hdr.length() == v.viewport().width())
    assert [hdr.sectionSize(c) for c in (1, 3, 4)] == fijas
    v.resize(300, 300)
    qtbot.waitUntil(lambda: hdr.sectionSize(0) == MIN_FILL_WIDTH)
    assert hdr.sectionSize(2) == MIN_FILL_WIDTH
    assert [hdr.sectionSize(c) for c in (1, 3, 4)] == fijas


def test_ninguna_tabla_usa_stretch():
    """Stretch impide arrastrar la columna; usar gui_qt.columns.fill_columns."""
    root = Path(__file__).resolve().parents[1] / "gui_qt"
    culpables = [str(p.relative_to(root)) for p in root.rglob("*.py")
                 if p.name != "columns.py"
                 and ("QHeaderView.Stretch" in p.read_text(encoding="utf-8")
                      or "QHeaderView.ResizeMode.Stretch" in p.read_text(encoding="utf-8"))]
    assert culpables == []


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


def _persist_view(qtbot, key, width=700):
    v, hdr = _view(qtbot)
    fill_columns(v, 1, key=key)
    for c, w in ((0, 90), (2, 110), (3, 80)):     # anchos de partida de la pestaña
        hdr.resizeSection(c, w)
    v.resize(width, 300)
    v.show()
    qtbot.waitExposed(v)
    return v, hdr


def test_los_anchos_arrastrados_se_recuerdan(qtbot, monkeypatch):
    from gui_qt import columns
    cfg = _FakeConfig()
    monkeypatch.setattr(columns, "_store", cfg)
    monkeypatch.setattr(columns, "SAVE_DELAY_MS", 10)
    v, hdr = _persist_view(qtbot, "t")
    qtbot.wait(50)
    assert cfg.saves == 0          # partida y relleno no son del usuario
    hdr.resizeSection(2, 200)      # el usuario arrastra
    hdr.resizeSection(1, 250)
    qtbot.waitUntil(lambda: cfg.saves > 0)
    guardado = cfg.data["column_widths"]["t"]["widths"]
    assert guardado == [hdr.sectionSize(c) for c in range(4)]
    assert cfg.data["column_widths"]["t"]["viewport"] == v.viewport().width()
    assert guardado[1] == 250 and guardado[2] == 200

    # Siguiente arranque, misma ventana: vuelven tal cual (también el
    # hueco que dejó al estrechar la de relleno).
    v2, hdr2 = _persist_view(qtbot, "t")
    assert [hdr2.sectionSize(c) for c in range(4)] == guardado
    # Otra tabla no los hereda.
    v3, hdr3 = _persist_view(qtbot, "otra")
    assert hdr3.sectionSize(2) == 110


def test_con_otro_ancho_de_ventana_solo_se_ajusta_la_de_relleno(qtbot, monkeypatch):
    from gui_qt import columns
    cfg = _FakeConfig({"column_widths": {"t": {"widths": [100, 300, 200, 90], "viewport": 690}}})
    monkeypatch.setattr(columns, "_store", cfg)
    v, hdr = _persist_view(qtbot, "t", width=900)
    assert [hdr.sectionSize(c) for c in (0, 2, 3)] == [100, 200, 90]
    assert hdr.sectionSize(1) == 300 + v.viewport().width() - 690
    assert cfg.saves == 0


def test_guardado_de_otra_version_con_distintas_columnas_se_ignora(qtbot, monkeypatch):
    from gui_qt import columns
    monkeypatch.setattr(columns, "_store", _FakeConfig(
        {"column_widths": {"t": {"widths": [300, 300], "viewport": 690}}}))
    v, hdr = _persist_view(qtbot, "t")
    assert hdr.sectionSize(0) == 90


def test_estrechar_y_volver_a_ensanchar_deja_la_tabla_igual(qtbot):
    v, hdr = _view(qtbot, cols=4)
    fill_columns(v, 1)
    for c, w in ((0, 200), (2, 200), (3, 200)):
        hdr.resizeSection(c, w)
    v.resize(900, 300)
    v.show()
    qtbot.waitExposed(v)
    antes = [hdr.sectionSize(c) for c in range(4)]
    v.resize(400, 300)                     # la de relleno topa en el mínimo
    qtbot.waitUntil(lambda: hdr.sectionSize(1) == MIN_FILL_WIDTH)
    v.resize(900, 300)
    qtbot.waitUntil(lambda: [hdr.sectionSize(c) for c in range(4)] == antes)


def test_tabla_en_pestana_oculta_llena_al_volver(qtbot):
    """Real: al cambiar el tamaño de la ventana con otra pestaña delante,
    Qt entrega el resize al mostrarse sin oldSize y la tabla no llenaba."""
    from PySide6.QtWidgets import QLabel, QTabWidget
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    v = QTableView()
    v.setModel(QStandardItemModel(3, 4))
    fill_columns(v, 1)
    tabs.addTab(v, "tabla")
    tabs.addTab(QLabel("otra"), "otra")
    tabs.resize(700, 300)
    tabs.show()
    qtbot.waitExposed(tabs)
    hdr = v.horizontalHeader()
    assert hdr.length() == v.viewport().width()
    tabs.setCurrentIndex(1)
    tabs.resize(1100, 300)
    qtbot.wait(20)
    tabs.setCurrentIndex(0)
    qtbot.waitUntil(lambda: hdr.length() == v.viewport().width())


def test_arrancar_pequena_y_maximizar_deja_lo_guardado(qtbot, monkeypatch):
    """Real: al arrancar maximizada la tabla pasa antes por el tamaño normal;
    las de relleno topaban en el mínimo y al crecer se repartían a partes
    iguales, así que lo guardado volvía descolocado (660/222 → 553/346)."""
    from gui_qt import columns
    cfg = _FakeConfig({"column_widths": {"t": {"widths": [100, 500, 90, 80], "viewport": 900}}})
    monkeypatch.setattr(columns, "_store", cfg)
    v, hdr = _view(qtbot)
    fill_columns(v, 1, 2, key="t")
    v.resize(400, 300)                       # tamaño normal: no cabe
    v.show()
    qtbot.waitExposed(v)
    vp = v.viewport().width()
    v.resize(400 + 900 - vp, 300)            # maximizada: la tabla mide lo guardado
    qtbot.waitUntil(lambda: v.viewport().width() == 900)
    assert [hdr.sectionSize(c) for c in range(4)] == [100, 500, 90, 80]
    assert cfg.saves == 0


def test_ensanchar_de_mas_una_columna_no_encoge_las_de_relleno(qtbot):
    """Al sobresalir sale la barra horizontal (la tabla cambia de alto):
    eso no debe hacer que las de relleno compensen lo arrastrado."""
    v, hdr = _view(qtbot)
    fill_columns(v, 1)
    v.resize(700, 300)
    v.show()
    qtbot.waitExposed(v)
    antes = [hdr.sectionSize(c) for c in range(4)]
    hdr.resizeSection(2, antes[2] + 400)
    qtbot.waitUntil(lambda: v.horizontalScrollBar().isVisible())
    qtbot.wait(20)
    assert [hdr.sectionSize(c) for c in (0, 1, 3)] == [antes[0], antes[1], antes[3]]


def test_al_salir_se_guarda_lo_que_aun_esperaba(qtbot, monkeypatch):
    """La app acaba con os._exit: un arrastre justo antes de salir se perdía."""
    from gui_qt import columns
    cfg = _FakeConfig()
    monkeypatch.setattr(columns, "_store", cfg)
    v, hdr = _persist_view(qtbot, "t")
    hdr.resizeSection(2, 200)
    assert cfg.saves == 0              # aún dentro de la espera
    columns.flush_pending()
    assert cfg.data["column_widths"]["t"]["widths"][2] == 200
    columns.flush_pending()            # nada pendiente: no vuelve a escribir
    assert cfg.saves == 1


def test_ocultar_columna_se_recuerda_y_vuelve_con_su_ancho(qtbot, monkeypatch):
    from gui_qt import columns
    cfg = _FakeConfig()
    monkeypatch.setattr(columns, "_store", cfg)
    v, hdr = _persist_view(qtbot, "t")
    f = v._fill_columns
    f.set_visible(2, False)
    assert hdr.isSectionHidden(2)
    assert hdr.length() == v.viewport().width()      # la de relleno ocupa el hueco
    columns.flush_pending()
    entry = cfg.data["column_widths"]["t"]
    assert entry["hidden"] == [2] and entry["widths"][2] == 110

    v2, hdr2 = _persist_view(qtbot, "t")             # siguiente arranque
    assert hdr2.isSectionHidden(2)
    v2._fill_columns.set_visible(2, True)
    assert hdr2.sectionSize(2) == 110
    assert hdr2.length() == v2.viewport().width()


def test_no_se_puede_ocultar_la_ultima_de_relleno(qtbot):
    v, hdr = _view(qtbot)
    f = fill_columns(v, 1)
    v.resize(700, 300)
    v.show()
    qtbot.waitExposed(v)
    f.set_visible(1, False)
    assert not hdr.isSectionHidden(1)
    menu = f.build_menu()
    acciones = [a for a in menu.actions() if a.isCheckable()]
    assert len(acciones) == 4
    assert not acciones[1].isEnabled() and acciones[0].isEnabled()


def test_restablecer_vuelve_a_los_de_partida_y_olvida_lo_guardado(qtbot, monkeypatch):
    from gui_qt import columns
    cfg = _FakeConfig({"column_widths": {"t": {"widths": [200, 300, 50, 60], "viewport": 690,
                                               "hidden": [3]}}})
    monkeypatch.setattr(columns, "_store", cfg)
    v, hdr = _persist_view(qtbot, "t")
    assert hdr.sectionSize(0) == 200 and hdr.isSectionHidden(3)
    v._fill_columns.reset()
    assert [hdr.sectionSize(c) for c in (0, 2, 3)] == [90, 110, 80]
    assert not hdr.isSectionHidden(3)
    assert hdr.length() == v.viewport().width()
    assert "t" not in cfg.data["column_widths"]


def test_el_menu_de_la_cabecera_usa_los_titulos(qtbot):
    from PySide6.QtGui import QStandardItemModel
    v = QTableView()
    m = QStandardItemModel(1, 3)
    m.setHorizontalHeaderLabels(["Nombre", "Tamaño", ""])
    v.setModel(m)
    qtbot.addWidget(v)
    f = fill_columns(v, 0)
    textos = [a.text() for a in f.build_menu().actions() if a.isCheckable()]
    assert textos == ["Nombre", "Tamaño", "Columna 3"]
