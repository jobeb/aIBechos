"""Pestaña Descargas (gui_qt/downloads/tab.py): reponer el resaltado del
resultado elegido tras cada lote de resultados en vivo NO debe repintar su
ficha (bug real: la ficha volvía a "Cargando…", relanzaba la búsqueda TMDB y
la descarga del póster con cada actualización de la lista, y solo terminaba
de cargar al acabar la búsqueda)."""

import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_qt.downloads.tab import DownloadsTab  # noqa: E402


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


class _Ctx:
    def set_status(self, *a):
        pass


class _Host:
    def __init__(self):
        self.config_data = _Cfg({"amule_search_type": "Kad"})
        self.ctx = _Ctx()
        self._amule_ec_lock = threading.Lock()

    def _downloads_open_ec(self):
        return None

    def _eta_for_download(self, d):
        return ""

    def _status_label_for_download(self, status):
        return ""


def _res(name, sources=5):
    return SimpleNamespace(name=name, size_human="700 MB", sources=sources,
                           complete=True, number=1)


def _tab(qtbot):
    tab = DownloadsTab(_Host())
    qtbot.addWidget(tab)
    tab._best = lambda: None   # fuera del alcance: elección del recomendado
    return tab


def test_render_no_repinta_la_ficha_del_seleccionado(qtbot):
    tab = _tab(qtbot)
    r1, r2 = _res("Serie 1x05 HDTV"), _res("Serie 1x05 720p")
    tab._results = [r1, r2]
    tab._render()
    assert tab.rmodel.rowCount() == 2

    # El usuario elige el segundo resultado: la ficha se muestra una vez.
    calls = []
    tab._show_result = lambda row: calls.append(row)
    tab.results_view.selectRow(1)
    assert calls == [1]
    from gui_qt.downloads.tab import _key
    tab._selected_key = _key(r2)

    # Llegan lotes en vivo (el merge actualiza las fuentes de los mismos
    # objetos): la lista se reordena/actualiza pero la ficha ni se toca.
    for sources in (7, 12, 20):
        r1.sources, r2.sources = sources, sources + 1
        tab._results = [r1, r2]
        tab._render()
    assert calls == [1]
    # ...y el resaltado sigue en el resultado elegido.
    assert tab.results_view.selectionModel().currentIndex().row() == 1


def test_click_del_usuario_si_muestra_la_ficha(qtbot):
    tab = _tab(qtbot)
    tab._results = [_res("Serie 1x05 HDTV"), _res("Serie 1x05 720p")]
    tab._render()
    calls = []
    tab._show_result = lambda row: calls.append(row)
    tab.results_view.selectRow(0)
    tab.results_view.selectRow(1)
    assert calls == [0, 1]


def test_serie_sin_numeracion_con_is_movie_false_si_tiene_candidato(qtbot):
    """Real: 🔍 de una serie buscando solo su nombre ("Margo tiene problemas
    de dinero", is_movie=False explícito) no destacaba ningún resultado: la
    query sin NxNN se trataba igualmente como película y se excluían todos
    los capítulos."""
    tab = DownloadsTab(_Host())
    qtbot.addWidget(tab)
    tab.search = lambda *a, **k: None
    tab.query.setText("Margo tiene problemas de dinero")
    tab._is_movie = False
    r = SimpleNamespace(
        name="Margo Tiene Problemas De Dinero 1x01 Alma Insaciable (Spanish English Subs) "
             "Webrip 1080P x265-Eac3 By Legan.mkv",
        size_human="1,35 GB", sources=1, complete=False, number=1)
    tab._results = [r]
    assert tab._best() is r
    tab._is_movie = True   # el ⬇ de Películas sí sigue excluyendo capítulos
    assert tab._best() is None


# ── Ordenar "Descargas en aMule" pulsando la cabecera ──
# Antes la tabla de descargas activas no ordenaba por ninguna columna.

def _dl(name, size, done, speed, sources, status=2):
    return {"hash_hex": name, "name": name, "size_full": size, "size_done": done,
            "percent": done / size * 100, "speed": speed, "sources": sources, "status": status}


def test_ordenar_cola_por_columnas():
    from gui_qt.downloads.tab import sort_active_rows
    a = _dl("b peli", 1000, 100, 0, 3)
    b = _dl("A serie", 3000, 2900, 50, 10)
    c = _dl("c otra", 2000, 1000, 10, 1)
    names = lambda rows: [d["name"] for d in rows]
    assert names(sort_active_rows([a, b, c], "name", True)) == ["A serie", "b peli", "c otra"]
    assert names(sort_active_rows([a, b, c], "size", False)) == ["A serie", "c otra", "b peli"]
    assert names(sort_active_rows([a, b, c], "speed", False)) == ["A serie", "c otra", "b peli"]
    assert names(sort_active_rows([a, b, c], "pct", True)) == ["b peli", "c otra", "A serie"]
    # Restante: 2 s (b), 100 s (c); la parada (sin velocidad) siempre al final.
    assert names(sort_active_rows([a, b, c], "eta", True)) == ["A serie", "c otra", "b peli"]
    assert names(sort_active_rows([a, b, c], "eta", False)) == ["c otra", "A serie", "b peli"]
    assert names(sort_active_rows([a, b, c], None, True)) == ["b peli", "A serie", "c otra"]


def test_pulsar_cabecera_ordena_y_se_recuerda(qtbot):
    from gui_qt.downloads.tab import A_SIZE
    tab = _tab(qtbot)
    tab._apply_active([_dl("peque", 1000, 10, 1, 1), _dl("grande", 9000, 10, 1, 1),
                       _dl("media", 5000, 10, 1, 1)])
    tab.active_view.horizontalHeader().sectionClicked.emit(A_SIZE)
    assert [d["name"] for d in tab.amodel.rows] == ["grande", "media", "peque"]
    tab.active_view.horizontalHeader().sectionClicked.emit(A_SIZE)
    assert [d["name"] for d in tab.amodel.rows] == ["peque", "media", "grande"]
    assert tab.config["table_sort"]["descargas_activas"] == {"key": "size", "asc": True}
    # El siguiente sondeo de aMule respeta el orden elegido.
    tab._apply_active([_dl("grande", 9000, 10, 1, 1), _dl("peque", 1000, 10, 1, 1)])
    assert [d["name"] for d in tab.amodel.rows] == ["peque", "grande"]
