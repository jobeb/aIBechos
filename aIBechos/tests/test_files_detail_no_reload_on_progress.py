"""La ficha lateral de Archivos no se recarga en cada tramo de subida.

Real (2.0.3): con un archivo subiendo seleccionado, la ficha (carátula,
reparto, ruta FTP) se recargaba sin parar: cada evento "uploading" del
AutoWatcher hacía _update_row, y update_row vuelve a pintar la ficha del
archivo seleccionado."""

import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from core.api_client import MediaInfo  # noqa: E402
from core.app_files_core import FilesCoreMixin  # noqa: E402
from core.file_entry import FileEntry  # noqa: E402
from gui_qt.files.detail_panel import FilesDetailPanel  # noqa: E402


class _Host(FilesCoreMixin):
    def __init__(self, files):
        self.files = files
        self._selected_entry = files[0]
        self._orphan_events_logged = set()
        self.rows, self.live = [], []

    def _after_from_worker(self, fn, critical=False):
        fn()

    def _update_row(self, entry):
        self.rows.append(entry.status)

    def _ftp_row_live(self, entry, progress, speed):
        self.live.append(progress)


def test_ticks_de_subida_solo_repintan_la_fila_al_empezar(tmp_path):
    p = tmp_path / "Serie 1x01.mkv"
    p.write_bytes(b"x")
    entry = FileEntry(str(p))
    entry.status = "renombrado"
    host = _Host([entry])
    for prog in (0.0, 0.1, 0.5, 0.9):
        host._on_auto_file_event(str(p), "uploading", new_name=p.name, progress=prog, speed=1.0)
    assert host.rows == ["subiendo"]          # solo la transición
    assert host.live == [0.0, 0.1, 0.5, 0.9]  # la barra, en todos


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, t):
        self.text = t


def _panel():
    panel = types.SimpleNamespace(_shown_key=None, error=_Label(), loads=0, cleared=0)

    def _show_info(info, confidence, vote):
        panel.loads += 1
        panel._shown_key = None

    def clear():
        panel.cleared += 1
        panel._shown_key = None

    panel._show_info = _show_info
    panel.clear = clear
    panel.title = panel.episode = panel.poster = _Label()
    return panel


def _entry_with_info(tmp_path, title="Serie"):
    p = tmp_path / f"{title}.mkv"
    p.write_bytes(b"x")
    e = FileEntry(str(p))
    e.media_info = MediaInfo(tmdb_id=1, title=title, original_title=title, year="2020", media_type="tv", season=1, episode=1)
    return e


def test_misma_ficha_no_se_recarga(tmp_path):
    panel, e = _panel(), _entry_with_info(tmp_path)
    for status in ("subiendo", "subiendo", "subido"):
        e.status = status
        FilesDetailPanel.show_entry(panel, e)
    assert panel.loads == 1


def test_error_nuevo_se_muestra_sin_recargar(tmp_path):
    panel, e = _panel(), _entry_with_info(tmp_path)
    FilesDetailPanel.show_entry(panel, e)
    e.status, e.error_msg = "error", "Disco lleno en el servidor"
    FilesDetailPanel.show_entry(panel, e)
    assert panel.loads == 1
    assert "Disco lleno" in panel.error.text


def test_otra_ficha_o_info_cambiada_si_recarga(tmp_path):
    panel = _panel()
    a, b = _entry_with_info(tmp_path, "A"), _entry_with_info(tmp_path, "B")
    FilesDetailPanel.show_entry(panel, a)
    FilesDetailPanel.show_entry(panel, b)
    assert panel.loads == 2
    b.media_info = MediaInfo(tmdb_id=1, title="B", original_title="B", year="2020", media_type="tv", season=1, episode=2)  # reasignado
    FilesDetailPanel.show_entry(panel, b)
    assert panel.loads == 3
