"""Subida manual con "Sustituir" en el diálogo de "Posible contenido
duplicado": tras subir bien el nuevo se borra el viejo (real: "Avatar Los
Siete Refugios 1x01 En el horizonte.mkv" se subió junto al .mp4 que ya
había, porque el diálogo solo permitía "Subir de todas formas")."""

import os
from types import SimpleNamespace

import pytest

from core.app_files_core import FilesCoreMixin


class _Host(FilesCoreMixin):
    def __init__(self, delete_result=(True, "")):
        self.deleted = []
        self.statuses = []
        self._delete_result = delete_result

    def _delete_remote_file(self, path):
        self.deleted.append(path)
        return self._delete_result

    def after(self, _ms, fn):
        fn()

    def _set_status(self, text, color=None):
        self.statuses.append(text)


DIR = "/datos2/seriespeques/Avatar Los Siete Refugios/Temporada 01"
NEW = f"{DIR}/Avatar Los Siete Refugios 1x01 En el horizonte.mkv"
OLD = f"{DIR}/Avatar Los Siete Refugios 1x01 En el horizonte.mp4"


def test_sustituir_borra_el_viejo_tras_subir():
    host, entry = _Host(), SimpleNamespace(_replace_duplicate=OLD)
    host._finish_duplicate_replacement(entry, NEW)
    assert host.deleted == [OLD]
    assert not hasattr(entry, "_replace_duplicate")


def test_sin_sustituir_no_borra_nada():
    host = _Host()
    host._finish_duplicate_replacement(SimpleNamespace(), NEW)
    assert host.deleted == []


def test_nunca_borra_lo_recien_subido():
    host = _Host()
    host._finish_duplicate_replacement(SimpleNamespace(_replace_duplicate=NEW), NEW)
    assert host.deleted == []


def test_borrado_fallido_avisa():
    host = _Host(delete_result=(False, "550 denegado"))
    host._finish_duplicate_replacement(SimpleNamespace(_replace_duplicate=OLD), NEW)
    assert host.deleted == [OLD]
    assert any("no se pudo borrar" in s for s in host.statuses)


def test_dialogo_ofrece_sustituir(qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QPushButton
    from gui_qt.files.dialogs import OverwriteDialog
    dlg = OverwriteDialog(None, "x.mp4", replace_label="Sustituir",
                          replace_all_label="Sustituir todos", show_all_button=False)
    qtbot.addWidget(dlg)
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert "Sustituir" in texts and "Sustituir todos" not in texts   # sin tanda, sin "todos"
    next(b for b in dlg.findChildren(QPushButton) if b.text() == "Sustituir").click()
    assert dlg.result == "replace"
