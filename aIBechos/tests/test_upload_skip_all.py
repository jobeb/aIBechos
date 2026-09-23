"""Guarda de regresión: "Omitir todos" en subidas manuales por lote.

Bug real: al subir a mano varios archivos que ya existen en el servidor,
cada worker en paralelo abría SU diálogo de "Archivo ya existe" — el
diálogo solo ofrecía "Sobrescribir todos" para el sí, pero para el no
había que contestar "Omitir" un archivo tras otro. App no se puede
instanciar sin tkinter, así que la comprobación es estática sobre
gui/app.py (mismo patrón que test_gui_status_colors.py).
"""

import ast
from pathlib import Path

APP_SOURCE = Path(__file__).resolve().parent.parent / "gui" / "app.py"


def _segment(tree: ast.Module, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            seg = ast.get_source_segment(APP_SOURCE.read_text(encoding="utf-8"), node)
            assert seg is not None, f"No se pudo extraer el código de {name}"
            return seg
    raise AssertionError(f"No existe {name} en gui/app.py")


def test_dialogo_ofrece_omitir_todos():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_OverwriteDialog")
    assert "Omitir todos" in seg, "El diálogo no ofrece el botón 'Omitir todos'"
    assert '"skip_all"' in seg or "'skip_all'" in seg, \
        "El diálogo no devuelve el resultado 'skip_all'"


def test_skip_all_se_resetea_por_tanda():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_begin_ftp_upload")
    assert "_upload_skip_all" in seg and "= False" in seg, \
        "_begin_ftp_upload no resetea _upload_skip_all (se colaría a la siguiente tanda)"


def test_skip_all_omite_sin_preguntar():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_upload_entry_with")
    assert "self._upload_skip_all = True" in seg, \
        "_upload_entry_with no activa _upload_skip_all al contestar 'Omitir todos'"
    assert "already_exists and self._upload_skip_all" in seg, \
        "_upload_entry_with no tiene atajo para omitir sin preguntar con el flag activo"


def test_sin_typo_sobreescribir():
    assert "Sobreescribir" not in APP_SOURCE.read_text(encoding="utf-8"), \
        "Typo 'Sobreescribir' en gui/app.py (es 'Sobrescribir')"
