"""La sesión (Archivos) se autoguardada, no solo al cerrar limpio.

Real (12/10): Snoopy se subió a las 22:58 y el PC se apagó después;
session.json era de las 17:20 y la fila "subido" se perdió al reiniciar
(el Historial sí la tenía, ese se escribe al subir). Con el apagado
diario de las 12:00, pasarían estas pérdidas todos los días.
"""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.app_files_core import FilesCoreMixin  # noqa: E402
from core.file_entry import FileEntry  # noqa: E402


class _Host(FilesCoreMixin):
    _SESSION_AUTOSAVE_SECONDS = 10.0

    def __init__(self, tmp_path):
        self.files = []
        self._selected_entry = None
        self._multi_selected = set()
        self._orphan_events_logged = set()
        self._session_file = tmp_path / "session.json"
        self._session_load_failed = False
        self.after_calls = []
        self.notified = []
        self.history = []

    def _session_path(self):
        return self._session_file

    def after(self, ms, fn, *args):
        self.after_calls.append((ms, fn))
        # No se ejecuta: el flush de remate lo dispara el test a mano.

    def _after_from_worker(self, fn, critical=False, timeout_s=5.0):
        fn()
        return True

    def _refresh_table(self):
        pass

    def _update_row(self, entry):
        pass

    def _ftp_row_live(self, entry, progress, speed):
        pass

    def _queue_upload_notification(self, fname):
        self.notified.append(fname)

    def _save_history_entry(self, *args, **kwargs):
        self.history.append(args)

    def _remove_uploaded_episode_from_missing_list(self, media_info):
        pass

    def _remove_uploaded_movie_from_movies_list(self, media_info):
        pass

    def _refresh_ftp_space(self):
        pass

    def _read_session(self):
        with open(self._session_file, encoding="utf-8") as f:
            return json.load(f)


def _mkfile(tmp_path, name="Serie 1x01.mkv"):
    p = tmp_path / name
    p.write_bytes(b"x" * 16)
    return p


def test_uploaded_guarda_la_fila_aunque_luego_se_borre_el_local(tmp_path):
    # El caso Snoopy: uploaded + "Eliminar original" + apagón.
    host = _Host(tmp_path)
    p = _mkfile(tmp_path)
    host._on_auto_file_event(str(p), "start")
    entry = host.files[0]
    entry.status = "renombrado"
    host._on_auto_file_event(str(p), "uploaded", new_name=p.name,
                             remote_full="/datos/" + p.name, size=16)
    assert host.after_calls == []  # forzado: sin espera
    p.unlink()  # post-proceso "Eliminar original"
    rows = host._read_session()
    assert any(r.get("status") == "subido" for r in rows)


def test_ticks_de_progreso_no_tocan_el_disco(tmp_path):
    host = _Host(tmp_path)
    p = _mkfile(tmp_path)
    entry = FileEntry(str(p))
    entry.status = "renombrado"
    host.files.append(entry)
    host._on_auto_file_event(str(p), "uploading", new_name=p.name,
                             progress=0.0, speed=1.0)
    for prog in (0.2, 0.7, 1.0):
        host._on_auto_file_event(str(p), "uploading", new_name=p.name,
                                 progress=prog, speed=1.0)
    assert not host._session_file.exists()
    assert host.after_calls == []


def test_transiciones_intermedias_debouncean_con_remate(tmp_path):
    host = _Host(tmp_path)
    p = _mkfile(tmp_path)
    host._on_auto_file_event(str(p), "start")
    assert host._session_file.exists()
    mtime = host._session_file.stat().st_mtime
    host._on_auto_file_event(str(p), "renamed", new_name="Otro.mkv")
    # Dentro de la ventana: no reescribe, programa el remate.
    assert host._session_file.stat().st_mtime == mtime
    assert len(host.after_calls) == 1
    ms, flush = host.after_calls[0]
    assert ms > 0
    flush()
    rows = host._read_session()
    assert any(r.get("status") == "renombrado" for r in rows)
