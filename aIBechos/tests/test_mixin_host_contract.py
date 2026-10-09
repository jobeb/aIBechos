"""Contrato de las mixins de core/ con su anfitrión, QtAppCore (gui_qt/).

La lógica de la app (core/app_*.py, core/missing_ep_scan.py) vive en mixins
sin interfaz. Cada `self.algo` que leen tiene que existir en el anfitrión:
como método/propiedad/constante de clase, o asignado al construirse. Si no,
revienta con AttributeError en tiempo de ejecución (pasó con
_last_missing_movies_sync_ts) -- este test lo detecta antes."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MIXIN_FILES = ["core/app_files_core.py", "core/app_shared_sync.py", "core/missing_ep_scan.py",
               "core/app_auto_complete.py", "core/app_download_requests.py",
               "core/app_downloads_core.py", "core/app_movies_core.py",
               "core/app_history_core.py", "core/app_cleanup_core.py",
               "core/app_watch_sync_core.py",
               "core/app_settings_core.py"]

# self.x NO seguido de una asignación (= pero no ==).
_READ_RE = re.compile(r"self\.(\w+)\b(?!\s*(?::[^=\n]*)?=(?!=))")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _used_names():
    text = "".join(_read(f) for f in MIXIN_FILES)
    # Solo lo que la lógica LEE (o llama): un atributo que únicamente
    # escribe no tiene por qué existir antes.
    names = set(_READ_RE.findall(text))
    # Leídos siempre con getattr(self, "x", por_defecto): no hace falta que existan.
    optional = set(re.findall(r'getattr\(self,\s*"(\w+)"', text))
    return names, optional


def _assigned_in(src: str) -> set:
    out = set(re.findall(r"self\.(\w+)\s*(?::[^=\n]*)?=(?!=)", src))
    for lhs in re.findall(r"^\s*(self\.\w+(?:\s*,\s*self\.\w+)+)\s*=(?!=)", src, re.M):
        out |= set(re.findall(r"self\.(\w+)", lhs))
    return out


def _missing(cls, host_src: str) -> list:
    names, optional = _used_names()
    assigned = _assigned_in(host_src)
    return sorted(n for n in names if n not in optional and not hasattr(cls, n) and n not in assigned)


def test_qt_host_aporta_todo_lo_que_usan_las_mixins():
    pytest.importorskip("PySide6")
    from gui_qt.core_host import QtAppCore
    missing = _missing(QtAppCore, _read("gui_qt/core_host.py"))
    assert not missing, f"QtAppCore no aporta: {missing}"
