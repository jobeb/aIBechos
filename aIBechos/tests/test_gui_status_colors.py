"""Guarda de regresión: los colores que se pasan a _set_status() existen.

Bug real (visto en app.log): gui/app.py::_delete_local_file llamaba a
_set_status(..., OK_COLOR), y OK_COLOR no estaba definido en el módulo. El
NameError se lanzaba DESPUÉS de que os.remove ya hubiera borrado el archivo
pero ANTES de llegar a _drop_entry_row(), así que "quitar de la lista"
borraba el archivo del disco sin quitar la fila de la lista -- la fila se
quedaba señalando a un archivo que ya no existía.

App no se puede instanciar sin tkinter, así que la comprobación es estática:
cualquier argumento color de _set_status() que sea un identificador en
mayúsculas debe existir como constante a nivel de módulo.
"""

import ast
from pathlib import Path

APP_SOURCE = Path(__file__).resolve().parent.parent / "gui" / "app.py"


def _module_names(tree) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.asname:
                    names.add(a.asname)
                elif a.name != "*":
                    names.add(a.name)
    return names


def _set_status_color_args(tree) -> list:
    args = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_set_status"):
            continue
        if len(node.args) < 2:
            continue
        color = node.args[1]
        if isinstance(color, ast.Name) and color.id.isupper() and color.id.isidentifier():
            args.append(color.id)
    return args


def test_los_colores_de_set_status_existen():
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    defined = _module_names(tree)
    for name in sorted(set(_set_status_color_args(tree))):
        assert name in defined, f"Color indefinido pasado a _set_status(): {name}"