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
