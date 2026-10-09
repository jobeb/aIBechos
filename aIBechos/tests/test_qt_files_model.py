"""Modelo Qt de la pestaña Archivos (gui_qt/files/model.py): contrato de
QAbstractItemModel (QAbstractItemModelTester), textos de cada columna,
actualización de una fila suelta y rendimiento con miles de archivos (en la
versión Tk hacía falta paginar)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QAbstractItemModelTester  # noqa: E402

from core.file_entry import FileEntry  # noqa: E402
from gui_qt.actions import ActionsRole  # noqa: E402
from gui_qt.files.model import (COL_ACTIONS, COL_BAR, COL_DET, COL_NAME, COL_NN, COL_STAT,  # noqa: E402
                                FilesModel, ProgressRole)


class _Ctx:
    def favorite_tooltip(self, *a):
        return ""

    def reservation_tooltip(self, *a):
        return ""


class _Host:
    def __init__(self, files):
        self.files = files
        self.ctx = _Ctx()

    def _preview_remote_path(self, e):
        return f"/series/{e.detected.get('title', '')}/{e.name}"

    def _file_size_text(self, e):
        return "1.0 KB"

    def _fav_symbol(self, e):
        return "☆"

    def _lock_symbol(self, e):
        return "🔓"

    def _entry_is_favorite(self, e):
        return False

    def _entry_is_reserved(self, e):
        return False


def _entries(tmp_path, names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"x")
        out.append(FileEntry(str(p)))
    return out


def test_files_model_contract_and_texts(qapp, tmp_path):
    files = _entries(tmp_path, ["Bleach 1x05 [HDTV].mkv", "Dune 01.cbz"])
    host = _Host(files)
    m = FilesModel(host)
    QAbstractItemModelTester(m, QAbstractItemModelTester.FailureReportingMode.Fatal)
    m.reset()
    assert m.rowCount() == 2
    assert m.data(m.index(0, COL_NAME)) == "Bleach 1x05 [HDTV].mkv"
    assert m.data(m.index(0, COL_DET)) == "Bleach S01E05"
    assert m.data(m.index(0, COL_STAT)) == "Pendiente"
    assert m.data(m.index(0, COL_BAR), ProgressRole) is None          # sin subida: sin barra
    ids = [a.id for a in m.data(m.index(0, COL_ACTIONS), ActionsRole)]
    assert ids == ["upload", "play", "remove"]

    files[0].status = "subiendo"
    files[0].ftp_progress = 0.5
    files[0].new_name = "Bleach 1x05.mkv"
    seen = []
    m.dataChanged.connect(lambda a, b, roles: seen.append((a.row(), b.row())))
    assert m.update_entry(files[0])
    assert seen == [(0, 0)]                                           # solo esa fila
    assert m.data(m.index(0, COL_NN)) == "Bleach 1x05.mkv"
    assert m.data(m.index(0, COL_BAR), ProgressRole) == 0.5
    assert [a.id for a in m.data(m.index(0, COL_ACTIONS), ActionsRole)][0] == "skip"
    assert m.data(m.index(0, COL_STAT), Qt.ForegroundRole) is not None


def test_thousands_of_files_reset_fast(qapp, tmp_path):
    p = tmp_path / "Serie 1x01.mkv"
    p.write_bytes(b"x")
    base = FileEntry(str(p))
    files = [base] * 0
    for i in range(5000):
        e = FileEntry.__new__(FileEntry)
        e.__dict__.update(base.__dict__)
        e.name = f"Serie 1x{i:04d}.mkv"
        files.append(e)
    m = FilesModel(_Host(files))
    t0 = time.perf_counter()
    m.reset()
    for r in range(0, 5000, 100):
        m.data(m.index(r, COL_NAME))
    assert m.rowCount() == 5000
    assert time.perf_counter() - t0 < 1.0
