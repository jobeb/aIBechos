"""Una sola pregunta por tanda para todos los conflictos de subida.

Bug real (2026-11-10): al subir 5 capítulos de The Twilight Zone, dos ya
estaban en el servidor como .avi (duplicado) y otros tenían restos o
parciales: salieron tres diálogos distintos de "¿sustituir?", cada uno con
su propio "(todos)" que no alcanzaba a los demás tipos.
Ahora _prescan_batch_conflicts revisa toda la tanda antes de subir y
pregunta una vez; _upload_entry_with aplica esa respuesta sin más diálogos.
"""

import os
import threading
from types import SimpleNamespace

import pytest

import core.duplicate_detect as dd
from core.app_files_upload import FilesUploadMixin

DIR = "/datos2/series/The Twilight Zone/Temporada 01"


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class _Slots:
    def acquire(self, cancel_event=None):
        return False   # corta justo antes de transferir: "cancelado" = se iba a subir


class _Conn:
    def __init__(self, sizes=None, listing=()):
        self.sizes = sizes or {}
        self.listing = list(listing)
        self.list_calls = 0

    def get_remote_size(self, path):
        return self.sizes.get(path)

    def list_files_checked(self, path):
        self.list_calls += 1
        return list(self.listing)


class _Host(FilesUploadMixin):
    def __init__(self, entries, answer="replace", stale=None):
        self.config_data = _Cfg(rename_remote=True)
        self._upload_queue = list(entries)
        self._upload_batch_size = len(entries)
        self._upload_cancel = threading.Event()
        self._upload_confirm_lock = threading.Lock()
        self._upload_overwrite_all = False
        self._upload_skip_all = False
        self._upload_duplicate_ignore_all = False
        self._upload_duplicate_replace_all = False
        self._upload_stale_choice = None
        self._upload_slots = _Slots()
        self.answer = answer
        self.stale = stale or {}
        self.asked = []
        self.deleted = []
        self.skipped = []

    def after(self, _ms, fn):
        fn()

    def _set_status(self, *a):
        pass

    def _ftp_row_set(self, *a):
        pass

    def _update_row(self, *a):
        pass

    def _auto_is_processing(self, path):
        return False

    def _upload_target(self, entry, ftp_conn):
        return "/datos2/series", DIR, entry.new_name, f"{DIR}/{entry.new_name}"

    def _stale_remote_from_history(self, local_path, filename, new_remote):
        return self.stale.get(filename, "")

    def _ask_gui(self, make, what, cancel_event=None):
        self.asked.append(what)
        if what == "batch-conflicts":
            make()
            return self.answer
        return "skip"   # cualquier diálogo por archivo: no debería salir

    def _make_batch_conflict_dialog(self, items, total, show_keep=True):
        self.dialog_items = items
        self.dialog_show_keep = show_keep
        return SimpleNamespace(result=self.answer)

    def _delete_remote_file(self, path):
        self.deleted.append(path)
        return True, ""

    def _get_free_space_with_jellyfin_fallback(self, ftp_conn, root):
        return None

    def _save_skip_history(self, entry, remote_file, size, reason, status="omitido"):
        self.skipped.append((entry.name, reason))


def _entry(tmp_path, ep):
    name = f"The Twilight Zone 1x0{ep}.mkv"
    p = tmp_path / name
    p.write_bytes(b"x" * 100)
    return SimpleNamespace(name=name, new_name=name, path=str(p), status="en_cola",
                           media_info=SimpleNamespace(media_type="tv", season=1, episode=ep),
                           remote_dir_override="", ftp_progress=0, ftp_status="",
                           _upload_dropped=False, _stale_checked=False,
                           _duplicate_checked=False, _last_upload_remote_size=0,
                           _conflict_policy=None, _conflict_dup=None)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """1x04 completo en el servidor, 1x06 con un .avi duplicado, 1x07 con
    resto de una subida anterior, 1x08 parcial (se reanuda: no es conflicto)
    y 1x09 sin nada."""
    entries = [_entry(tmp_path, n) for n in (4, 6, 7, 8, 9)]
    by = {e.name[-8:-4]: e for e in entries}
    sizes = {f"{DIR}/{by['1x04'].new_name}": 100, f"{DIR}/{by['1x08'].new_name}": 40}
    conn = _Conn(sizes, listing=["The Twilight Zone 1x06.avi"])
    monkeypatch.setattr(dd, "find_duplicate",
                        lambda files, info, cur: "The Twilight Zone 1x06.avi"
                        if info.episode == 6 and "The Twilight Zone 1x06.avi" in files else None)
    stale = {by["1x07"].name: f"{DIR}/Twilight Zone viejo 1x07.mkv"}
    return entries, by, conn, stale


def _run(host, entries, conn):
    host._prescan_batch_conflicts(conn)
    return {e.name[-8:-4]: host._upload_entry_with(e, conn, lambda: 0, threading.Event())
            for e in entries}


def test_una_sola_pregunta_y_sustituir_todo(batch):
    entries, by, conn, stale = batch
    host = _Host(entries, answer="replace", stale=stale)
    res = _run(host, entries, conn)
    assert host.asked == ["batch-conflicts"], "tiene que preguntar UNA vez, sin diálogos por archivo"
    assert [n for n, _ in host.dialog_items] == [by[k].new_name for k in ("1x04", "1x06", "1x07")]
    assert all(r == (False, "cancelado") for r in res.values()), res   # todos llegan a subirse
    assert by["1x06"]._replace_duplicate == f"{DIR}/The Twilight Zone 1x06.avi"
    assert host.deleted == [stale[by["1x07"].name]]
    assert conn.list_calls == 1, "el listado de la carpeta se reutiliza para toda la tanda"


def test_omitir_estos_solo_omite_los_conflictivos(batch):
    entries, by, conn, stale = batch
    host = _Host(entries, answer="skip", stale=stale)
    res = _run(host, entries, conn)
    assert host.asked == ["batch-conflicts"]
    assert {k for k, r in res.items() if r == (True, "omitido")} == {"1x04", "1x06", "1x07"}
    assert res["1x08"] == res["1x09"] == (False, "cancelado")
    assert host.deleted == []
    assert all(by[k].status != "en_cola" for k in ("1x04", "1x06", "1x07")), \
        "lo omitido vuelve a un estado accionable, no se queda 'en cola'"


def test_subir_sin_borrar_nada(batch):
    entries, by, conn, stale = batch
    host = _Host(entries, answer="keep", stale=stale)
    res = _run(host, entries, conn)
    assert host.asked == ["batch-conflicts"]
    assert res["1x04"] == (True, "omitido")          # mismo nombre completo: no se pisa
    assert res["1x06"] == res["1x07"] == (False, "cancelado")
    assert not getattr(by["1x06"], "_replace_duplicate", None)
    assert host.deleted == []
    assert host.dialog_show_keep


def test_cerrar_con_la_x_no_sustituye(batch):
    entries, by, conn, stale = batch
    host = _Host(entries, answer=None, stale=stale)
    res = _run(host, entries, conn)
    assert res["1x04"] == res["1x06"] == res["1x07"] == (True, "omitido")
    assert host.deleted == []


def test_uno_a_uno_vuelve_al_flujo_de_siempre(batch):
    entries, by, conn, stale = batch
    host = _Host(entries, answer="one_by_one", stale=stale)
    _run(host, entries, conn)
    assert host.asked[0] == "batch-conflicts"
    assert set(host.asked[1:]) == {"overwrite", "duplicate", "stale-upload"}


def test_sin_conflictos_no_pregunta(tmp_path):
    entries = [_entry(tmp_path, n) for n in (1, 2)]
    host = _Host(entries)
    host._prescan_batch_conflicts(_Conn())
    assert host.asked == []
    assert all(e._duplicate_checked for e in entries), "comprobado sin duplicados: no se relista"


def test_un_solo_archivo_no_revisa_la_tanda(tmp_path):
    e = _entry(tmp_path, 4)
    host = _Host([e])
    conn = _Conn({f"{DIR}/{e.new_name}": 100})
    host._prescan_batch_conflicts(conn)
    assert host.asked == [] and e._conflict_policy is None


def test_solo_completos_no_ofrece_subir_sin_borrar(tmp_path):
    entries = [_entry(tmp_path, n) for n in (1, 2)]
    host = _Host(entries)
    host._prescan_batch_conflicts(_Conn({f"{DIR}/{e.new_name}": 100 for e in entries}))
    assert host.dialog_show_keep is False


def test_dialogo_qt(qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QPushButton
    from gui_qt.files.dialogs import BatchConflictDialog
    dlg = BatchConflictDialog(None, [("a.mkv", ["ya está completo en el servidor"])], 5,
                              show_keep=False)
    qtbot.addWidget(dlg)
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert "Subir sin borrar nada" not in texts
    assert {"Sustituir todo", "Omitir estos", "Preguntar uno a uno"} <= set(texts)
    assert dlg.result == "skip"   # la X no sustituye nada
    next(b for b in dlg.findChildren(QPushButton) if b.text() == "Sustituir todo").click()
    assert dlg.result == "replace"
