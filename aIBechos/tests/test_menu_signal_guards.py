"""Guardas de lambdas en señales Qt (gui_qt/): QAction.triggered y
QPushButton.clicked emiten `checked` como primer argumento posicional --
una lambda `lambda s=serie: ...` lo recibe en `s` (caso real: "Template
aMule por serie" en Archivos abría AmuleTemplateDialog con s=False y
reventaba en silencio sin abrir nada). La convención es primer parámetro
desechable (`_` / `_checked`)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "gui_qt"


def _py_files():
    return sorted(ROOT.rglob("*.py"))


def test_lambdas_en_triggered_desechan_checked():
    bad = []
    for p in _py_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"\.triggered\.connect\(\s*lambda\s+([A-Za-z_]+)\s*=", line)
            if m and m.group(1) not in ("_", "_checked", "checked"):
                bad.append(f"{p.name}:{i}: {line.strip()}")
    assert not bad, "lambda en triggered que tragaría `checked`:\n" + "\n".join(bad)


def test_lambdas_en_clicked_desechan_checked():
    bad = []
    for p in _py_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"\.clicked\.connect\(\s*lambda\s+([A-Za-z_]+)\s*=", line)
            if m and m.group(1) not in ("_", "_checked", "checked"):
                bad.append(f"{p.name}:{i}: {line.strip()}")
    assert not bad, "lambda en clicked que tragaría `checked`:\n" + "\n".join(bad)


def test_template_amule_archivos_descarta_checked():
    src = (ROOT / "files" / "tab.py").read_text(encoding="utf-8")
    i = src.find("Template aMule por serie")
    assert i >= 0
    chunk = src[i:i + 400]
    assert re.search(r"lambda\s+_checked\s*=\s*False\s*,\s*s\s*=\s*series", chunk), \
        "el menú Template aMule de Archivos debe descartar `checked`"
