"""Ningún módulo debe crear el logger raíz "aIBechos" con handler: todos los
loggers de la app ("aIBechos.gui", "aIBechos.qt"...) son hijos suyos y, con
un handler también en el padre, cada línea se escribiría dos veces en
app.log (pasó al mover lógica de gui/app.py a core/)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_nadie_usa_el_logger_padre():
    offenders = []
    for path in list((ROOT / "core").glob("*.py")) + list((ROOT / "gui_qt").rglob("*.py")) + [ROOT / "main.py"]:
        text = path.read_text(encoding="utf-8")
        if re.search(r'get_logger\(\s*"aIBechos"\s*,', text):
            offenders.append(path.name)
    assert not offenders, f"get_logger('aIBechos', ...) duplica las líneas del log: {offenders}"
