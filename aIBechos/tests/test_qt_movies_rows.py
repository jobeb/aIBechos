"""Pestaña Recomendado (gui_qt/movies/tab.py): secciones por categoría,
carga de filas, filtro de texto y ficha. TMDB fingido, sin red."""

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from gui_qt.bridge import install  # noqa: E402
from gui_qt.movies.tab import MoviesTab  # noqa: E402


class _Ctx(QObject):
    favorites_changed = Signal()
    reservations_changed = Signal()

    def is_favorite(self, *a):
        return False

    def is_reserved(self, *a):
        return False

    def favorite_tooltip(self, *a):
        return ""

    def reservation_tooltip(self, *a):
        return ""

    def set_status(self, *a):
        pass

    def toggle_favorite(self, *a):
        pass

    def toggle_reservation(self, *a):
        pass

    def best_known_size_bytes(self, *a):
        return 0


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


class _TMDB:
    def __init__(self):
        self.calls = []

    def get_genres(self, kind):
        return [{"id": 28, "name": "Accion"}, {"id": 35, "name": "Comedia"}]

    def list_endpoint(self, path, kind, page=1, params=None):
        self.calls.append((path, page))
        base = (page - 1) * 2
        items = []
        for i in (1, 2):
            n = base + i
            items.append({"id": 1000 + n, "media_type": kind,
                          "title": f"Dune Parte {n}", "release_date": "2024-03-01",
                          "poster_path": "", "vote_average": 8.0, "overview": "Desierto.",
                          "genre_ids": [878], "original_language": "en", "origin_country": []})
        return items, 1

    def get_top_cast(self, *a):
        return []

    def get_movie_certification(self, *a):
        return ""

    def get_watch_providers(self, *a):
        return {"results": {"ES": {"flatrate": [1]}}}

    def movie_watch_providers_raw(self, *a):
        return {"ES": {"flatrate": [1]}}


class _Host:
    def __init__(self):
        self.ctx = _Ctx()
        self.config_data = _Cfg({"missing_movies_watch_only": False,
                                 "missing_movies_hide_in_server": False,
                                 "missing_movies_hide_asian": False,
                                 "missing_movies_type_filter": "all",
                                 "missing_movies_year_filter": "Todos",
                                 "missing_movies_genre_filter": "Todos"})
        self.tmdb = _TMDB()
        self._amule_ec_lock = threading.Lock()
        self._movies_results = []
        self._movies_selected_tmdb_id = None
        self._movies_visible = False
        self.window = None
        self.dismissed = []

    def _rows_from_movies_cache(self):
        return []

    def _is_missing_ep_auto_enabled(self, tid):
        return False

    def _sync_missing_movies_from_ftp(self):
        pass

    def _dismiss_missing_movie(self, r):
        self.dismissed.append(r)


def _tab(qtbot):
    install()
    tab = MoviesTab(_Host())
    qtbot.addWidget(tab)
    tab.show()
    return tab


def test_secciones_y_carga(qtbot):
    tab = _tab(qtbot)
    assert len(tab._sections) == 38  # 19 pelis + 19 series (watch_only off: entra Próximamente)
    assert tab._sections[0]["rowdef"]["id"] == "trend"
    assert all(s["state"] == "idle" for s in tab._sections)
    sec = tab._sections[0]
    tab._load_section(sec)
    qtbot.wait(2000)
    assert sec["state"] == "ready", sec["status_lbl"].text()
    assert len(sec["model"].items) == 2
    assert "2 título" in sec["status_lbl"].text()
    assert "recomendada" in tab.status_lbl.text()


def test_filtro_texto_y_ficha(qtbot):
    tab = _tab(qtbot)
    sec = tab._sections[0]
    tab._load_section(sec)
    qtbot.wait(2000)
    assert sec["state"] == "ready"
    tab.search.setText("parte 1")
    tab._apply_text()
    assert len(sec["model"].items) == 1
    tab.search.clear()
    tab._apply_text()
    assert len(sec["model"].items) == 2
    item = sec["model"].items[0]
    tab._on_card_selected(item)
    qtbot.wait(500)
    assert tab.d_title.text() == "Dune Parte 1"
    assert "Desierto." in tab.d_overview.text()


def test_accion_descartar(qtbot):
    tab = _tab(qtbot)
    sec = tab._sections[0]
    tab._load_section(sec)
    qtbot.wait(2000)
    item = sec["model"].items[0]
    tab._on_card_action(dict(item), "dismiss")
    assert tab.host.dismissed and tab.host.dismissed[0]["tmdb_id"] == item["tmdb_id"]
