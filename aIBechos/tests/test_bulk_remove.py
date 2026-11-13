"""Botón "Eliminar" de Archivos (uno o varios seleccionados): un solo
diálogo cuyas opciones dependen de dónde está cada archivo, y todas
quitan las filas de la lista. Antes solo había la ✕ por fila y Supr con
varios seleccionados no hacía nada; "Limpiar" nunca borra del disco."""

import threading
from types import SimpleNamespace

from core import remote_presence as rp
from core.app_files_core import FilesCoreMixin

R1 = "/datos2/series/Serie/Temporada 1/Serie 1x01.mkv"
R2 = "/datos2/series/Serie/Temporada 1/Serie 1x02.mkv"


# ── Reglas puras ──

def test_opciones_segun_donde_estan():
    assert rp.bulk_removal_options([(True, "")]) == [rp.QUITAR_LISTA, rp.BORRAR_LOCAL]
    assert rp.bulk_removal_options([(False, R1)]) == [rp.QUITAR_LISTA, rp.BORRAR_SERVIDOR]
    assert rp.bulk_removal_options([(True, R1)]) == [
        rp.QUITAR_LISTA, rp.BORRAR_LOCAL, rp.BORRAR_SERVIDOR, rp.BORRAR_AMBOS]
    assert rp.bulk_removal_options([(False, "")]) == [rp.QUITAR_LISTA]
    # Ruta local guardada por error en "remote": no cuenta como servidor.
    assert rp.bulk_removal_options([(False, "C:\\x\\a.mkv")]) == [rp.QUITAR_LISTA]


def test_mezcla_ofrece_lo_de_todos():
    items = [(True, ""), (False, R1)]
    assert rp.bulk_removal_options(items) == [
        rp.QUITAR_LISTA, rp.BORRAR_LOCAL, rp.BORRAR_SERVIDOR, rp.BORRAR_AMBOS]
    assert rp.presence_counts(items + [(True, R2), (False, "")]) == {
        "total": 4, "local": 2, "remote": 2, "both": 1, "none": 1}


def test_cada_opcion_borra_solo_donde_puede():
    assert rp.removal_targets(rp.QUITAR_LISTA, "a", True, R1) == ("", "")
    assert rp.removal_targets(rp.BORRAR_LOCAL, "a", True, R1) == ("a", "")
    assert rp.removal_targets(rp.BORRAR_SERVIDOR, "a", True, R1) == ("", R1)
    assert rp.removal_targets(rp.BORRAR_AMBOS, "a", True, R1) == ("a", R1)
    assert rp.removal_targets(rp.BORRAR_AMBOS, "a", False, R1) == ("", R1)
    assert rp.removal_targets(rp.BORRAR_AMBOS, "a", True, "") == ("a", "")


# ── Flujo ──

class _Host(FilesCoreMixin):
    def __init__(self, entries, history, choice, confirm=True, remote_fail=()):
        self.files = list(entries)
        self._multi_selected = set(entries[1:])
        self._selected_entry = entries[0]
        self._upload_queue = []
        self._watcher = None
        self._history = history
        self.choice, self.confirm = choice, confirm
        self.remote_fail = set(remote_fail)
        self.dialog_args = self.confirm_args = None
        self.deleted_remote = []
        self.status = []
        self.discarded = []

    def after(self, _ms, fn):
        fn()

    def _load_history(self):
        return self._history

    def _set_status(self, text, color=None):
        self.status.append(text)

    def _make_remove_entries_dialog(self, names, counts, opciones):
        self.dialog_args = (names, counts, opciones)
        return SimpleNamespace(result=self.choice)

    def _make_confirm_bulk_removal_dialog(self, locales, remotos):
        self.confirm_args = (locales, remotos)
        return SimpleNamespace(result=self.confirm)

    def _delete_remote_files(self, paths):
        self.deleted_remote += [p for p in paths if p not in self.remote_fail]
        return {p: (p not in self.remote_fail, "550 denegado") for p in paths}

    def _ftp_lookup_uploaded_file(self, entry, nombre):
        return ""

    def _discard_from_auto_watcher(self, entry):
        self.discarded.append(entry)

    def _drop_from_upload(self, entries):
        pass

    def _refresh_table(self):
        pass

    def _autosave_session(self, force=False):
        pass

    def _clear_detail(self):
        pass

    def _update_assign_button_label(self):
        pass


class _Entry(SimpleNamespace):
    __hash__ = object.__hash__   # como FileEntry: identidad, para los sets
    __eq__ = object.__eq__


def _entry(path, name):
    return _Entry(path=str(path), name=name, new_name=name, status="subido")


def _run(host):
    """_remove_entries lanza un hilo para localizar; aquí se espera a él y
    al del borrado."""
    before = set(threading.enumerate())
    host._remove_entries(host._current_selection())
    for _ in range(3):
        for t in set(threading.enumerate()) - before:
            t.join(5)


def _setup(tmp_path):
    a = tmp_path / "Serie 1x01.mkv"
    a.write_bytes(b"x")
    b = tmp_path / "Serie 1x02.mkv"   # ya no está en disco, sí en el servidor
    c = tmp_path / "Peli.mkv"
    c.write_bytes(b"x")               # solo en disco
    ea, eb, ec = _entry(a, a.name), _entry(b, b.name), _entry(c, c.name)
    history = [{"status": "ok", "local_path": str(a), "remote": R1},
               {"status": "ok", "local_path": str(b), "remote": R2}]
    return (a, b, c), (ea, eb, ec), history


def test_borrar_ambos_borra_donde_esta_y_quita_todas(tmp_path):
    (a, b, c), entries, history = _setup(tmp_path)
    host = _Host(list(entries), history, rp.BORRAR_AMBOS)
    _run(host)
    names, counts, opciones = host.dialog_args
    assert counts == {"total": 3, "local": 2, "remote": 2, "both": 1, "none": 0}
    assert rp.BORRAR_AMBOS in opciones
    assert host.confirm_args == ([str(a), str(c)], [R1, R2])
    assert sorted(host.deleted_remote) == [R1, R2]
    assert not a.exists() and not c.exists()
    assert host.files == []
    assert host._selected_entry is None and host._multi_selected == set()


def test_quitar_solo_de_la_lista_no_borra_ni_confirma(tmp_path):
    (a, _, c), entries, history = _setup(tmp_path)
    host = _Host(list(entries), history, rp.QUITAR_LISTA)
    _run(host)
    assert host.confirm_args is None
    assert a.exists() and c.exists() and host.deleted_remote == []
    assert host.files == []
    # El modo automático no los vuelve a meter en la lista.
    assert len(host.discarded) == 3


def test_borrar_del_servidor_deja_el_disco(tmp_path):
    (a, _, c), entries, history = _setup(tmp_path)
    host = _Host(list(entries), history, rp.BORRAR_SERVIDOR)
    _run(host)
    assert host.confirm_args == ([], [R1, R2])
    assert a.exists() and c.exists()
    assert host.files == []


def test_cancelar_no_toca_nada(tmp_path):
    (a, _, _), entries, history = _setup(tmp_path)
    host = _Host(list(entries), history, None)
    _run(host)
    assert host.files == list(entries) and a.exists() and host.confirm_args is None
    host = _Host(list(entries), history, rp.BORRAR_LOCAL, confirm=False)
    _run(host)
    assert host.files == list(entries) and a.exists()


def test_si_falla_el_servidor_no_borra_el_local_y_la_fila_se_queda(tmp_path):
    (a, _, c), (ea, eb, ec), history = _setup(tmp_path)
    host = _Host([ea, eb, ec], history, rp.BORRAR_AMBOS, remote_fail={R1})
    _run(host)
    assert a.exists()           # su copia del servidor sigue: no se borra el local
    assert not c.exists()
    assert host.files == [ea]   # para poder reintentar
    assert "no se pudieron" in host.status[-1]


def test_un_solo_archivo_tambien(tmp_path):
    (_, _, c), (_, _, ec), history = _setup(tmp_path)
    host = _Host([ec], history, rp.BORRAR_LOCAL)
    _run(host)
    assert host.dialog_args[2] == [rp.QUITAR_LISTA, rp.BORRAR_LOCAL]
    assert not c.exists() and host.files == []


# ── Diálogos Qt ──

def _buttons(dlg):
    from PySide6.QtWidgets import QPushButton
    return [b.text() for b in dlg.findChildren(QPushButton)]


def test_dialogo_un_archivo_solo_en_disco(qtbot):
    from gui_qt.files.dialogs import RemoveEntriesDialog
    items = [(True, "")]
    dlg = RemoveEntriesDialog(None, ["Peli.mkv"], rp.presence_counts(items),
                              rp.bulk_removal_options(items))
    qtbot.addWidget(dlg)
    assert _buttons(dlg) == ["Quitar solo de la lista", "Borrar del disco (1)", "Cancelar"]


def test_dialogo_varios_mezclados_y_cerrar_es_cancelar(qtbot):
    from gui_qt.files.dialogs import RemoveEntriesDialog
    items = [(True, R1), (False, R2), (True, "")]
    dlg = RemoveEntriesDialog(None, ["a", "b", "c"], rp.presence_counts(items),
                              rp.bulk_removal_options(items))
    qtbot.addWidget(dlg)
    assert _buttons(dlg) == ["Quitar solo de la lista", "Borrar del disco (2)",
                             "Borrar del servidor (2)", "Borrar del disco y del servidor",
                             "Cancelar"]
    dlg.reject()
    assert dlg.result is None


def test_confirmacion_lista_las_rutas(qtbot):
    from PySide6.QtWidgets import QLabel
    from gui_qt.files.dialogs import ConfirmBulkRemovalDialog
    local = r"C:\d\peli.mkv"
    dlg = ConfirmBulkRemovalDialog(None, [local], [R1, R2])
    qtbot.addWidget(dlg)
    texto = "\n".join(l.text() for l in dlg.findChildren(QLabel))
    assert "1 archivo de tu disco y 2 archivos del servidor" in texto
    assert local in texto and R1 in texto and R2 in texto
    assert dlg.result is False
