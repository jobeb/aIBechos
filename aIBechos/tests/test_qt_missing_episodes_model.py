"""Modelo Qt de "Episodios que faltan" (gui_qt/missing_episodes/model.py).

Comprueba con QAbstractItemModelTester (el validador oficial de Qt) que el
árbol perezoso cumple el contrato de QAbstractItemModel, y que una serie con
1.000 episodios pendientes se despliega rápido -- el caso que colgaba la
interfaz Tk.
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QAbstractItemModelTester  # noqa: E402

from gui_qt.actions import Action, ActionsRole  # noqa: E402
from gui_qt.missing_episodes.model import (  # noqa: E402
    COL_ACTIONS, COL_NAME, COL_SUMMARY, MissingEpisodesModel)

TEMPLATE = "{serie} {temporada}x{episodio:02d} {titulo}{ext}"


class _Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Ctx:
    def __init__(self):
        self.config = _Cfg(tv_template=TEMPLATE)

    def is_favorite(self, media_type, tmdb_id):
        return tmdb_id == 1

    def is_reserved(self, media_type, tmdb_id):
        return False

    def favorite_tooltip(self, media_type, tmdb_id):
        return ""

    def reservation_tooltip(self, media_type, tmdb_id):
        return ""


def _row(tmdb_id, name, missing, **extra):
    r = {"tmdb_id": tmdb_id, "name": name, "missing": missing, "source": "jellyfin",
         "ignored": False, "episode_titles": {}, "split_seasons": set(), "unknown_seasons": set(),
         "server_season_counts": {}, "first_air_date": "2001-01-01", "season_air_dates": {},
         "episode_air_dates": {}, "ignored_seasons": set(), "ignored_episodes": {}}
    r.update(extra)
    return r


def _model(rows):
    m = MissingEpisodesModel(_Ctx())
    m.actions_provider = lambda node: [Action("x", "x", "#000", "tip")]
    m.set_rows(rows, {})
    return m


def test_model_passes_qt_model_tester(qapp):
    rows = [_row(1, "Uno", {1: [1, 2, 3], 2: [5]}),
            _row(2, "Dos", {}, server_season_counts={1: 10}),
            _row(3, "Tres", {1: [4]}, unknown_seasons={7})]
    m = _model(rows)
    QAbstractItemModelTester(m, QAbstractItemModelTester.FailureReportingMode.Fatal)
    # Recorrer el árbol entero fuerza la construcción perezosa bajo el tester.
    stack = [m.index(i, 0) for i in range(m.rowCount())]
    seen = 0
    while stack:
        idx = stack.pop()
        seen += 1
        for j in range(m.rowCount(idx)):
            stack.append(m.index(j, 0, idx))
    assert seen > len(rows)
    m.set_rows(rows[:1], {})   # reset bajo el tester


def test_tree_structure_and_lazy_children(qapp):
    m = _model([_row(1, "Uno", {1: [1, 2], 2: [3]}, split_seasons={1})])
    series = m.index(0, 0)
    assert m.node(series).children is None          # aún sin construir
    kinds = [m.node(m.index(i, 0, series)).kind for i in range(m.rowCount(series))]
    assert kinds == ["info", "season", "season"]
    season1 = m.index(1, 0, series)
    assert m.data(season1).startswith("Temporada 1 (2 episodios)")
    assert m.rowCount(season1) == 2
    ep = m.index(0, 0, season1)
    assert m.data(ep) == "Uno 1x01"
    assert m.data(m.index(0, COL_ACTIONS, season1), ActionsRole)[0].id == "x"
    assert m.data(m.index(0, COL_SUMMARY)).startswith("3 episodios")
    assert m.data(m.index(0, COL_NAME), Qt.FontRole).bold()


def test_thousand_episode_season_expands_fast(qapp):
    m = _model([_row(1, "One Piece", {1: list(range(1, 1001))})])
    series = m.index(0, 0)
    t0 = time.perf_counter()
    season = m.index(0, 0, series)
    n = m.rowCount(season)
    for i in range(0, n, 50):
        m.data(m.index(i, COL_NAME, season))
    elapsed = time.perf_counter() - t0
    assert n == 1000
    assert elapsed < 1.0, f"desplegar 1000 episodios tardó {elapsed:.2f}s"


def test_find_nodes_for_state_refresh(qapp):
    m = _model([_row(1, "Uno", {2: [4, 5]})])
    series = m.index(0, 0)
    season = m.index(0, 0, series)
    m.rowCount(season)
    assert m.find_season_node(1, 2).season == 2
    assert m.find_episode_node((1, 2, 5)).line[1] == 5
    assert m.find_episode_node((1, 2, 9)) is None
    assert m.series_index(1).isValid() and not m.series_index(99).isValid()
