"""Columna "Peso" de Archivos: sigue mostrando el tamaño cuando el
archivo ya se movió o borró tras subirlo, aunque la fila no se hubiera
pintado nunca mientras existía."""

from core.app_files_core import FilesCoreMixin
from core.file_entry import FileEntry


class _Host:
    _file_size_text = FilesCoreMixin._file_size_text
    _remember_entry_size = staticmethod(FilesCoreMixin._remember_entry_size)


def test_peso_leido_del_disco_se_conserva_al_borrarse(tmp_path):
    p = tmp_path / "a.mkv"
    p.write_bytes(b"x" * 2048)
    e = FileEntry(str(p))
    h = _Host()
    shown = h._file_size_text(e)
    assert shown
    p.unlink()
    assert h._file_size_text(e) == shown


def test_bytes_sabidos_sin_texto_se_muestran(tmp_path):
    """Barra de estado / orden por peso apuntaban solo los bytes."""
    e = FileEntry(str(tmp_path / "no_existe.mkv"))
    e._last_known_size_bytes = 5 * 1024 * 1024
    assert _Host()._file_size_text(e) != ""


def test_peso_que_manda_el_autowatcher(tmp_path):
    e = FileEntry(str(tmp_path / "ya_borrado.mkv"))
    h = _Host()
    assert h._file_size_text(e) == ""
    h._remember_entry_size(e, 3 * 1024 * 1024)
    assert h._file_size_text(e) != ""
    h._remember_entry_size(e, 0)          # sin dato: no borra lo sabido
    h._remember_entry_size(e, None)       # ni el disco (no existe)
    assert e._last_known_size_bytes == 3 * 1024 * 1024
    r = FileEntry.from_dict(e.to_dict())  # y sobrevive al reinicio
    assert h._file_size_text(r) != ""


def test_fila_antigua_sin_peso_lo_toma_del_historial(qapp, tmp_path):
    from gui_qt.files.model import COL_SIZE, FilesModel

    e = FileEntry(str(tmp_path / "Serie 1x01.mkv"))   # ya no existe en disco
    e.status = "subido"
    hist = tmp_path / "upload_history.json"
    hist.write_text("[]", encoding="utf-8")

    class Host(_Host):
        files = [e]
        _multi_selected = set()
        _selected_entry = None

        def _history_path(self):
            return hist

        def _load_history(self):
            return [{"status": "ok", "filename": e.name, "local_path": e.path,
                     "size": 700 * 1024 * 1024}]

        def _preview_remote_path(self, entry):
            return ""

    m = FilesModel(Host())
    m.reset()
    assert m.data(m.index(0, COL_SIZE)) != ""
    assert e._last_known_size_bytes == 700 * 1024 * 1024
