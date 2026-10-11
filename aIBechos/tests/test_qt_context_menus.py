"""Menús clic-derecho (gui_qt/): todas las tablas/listas con filas deben
ofrecerlos (muchas no tenían ninguno). Estático + comprobación en las
pestañas con harness Qt existente."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "gui_qt"

# Módulo -> debe mencionar CustomContextMenu (vista) al menos una vez.
MENUS = [
    "files/tab.py",
    "missing_episodes/tab.py",
    "downloads/tab.py",
    "movies/tab.py",
    "movies/see_all.py",
    "history/tab.py",
    "info/requests.py",
    "protected/tab.py",
    "cleanup/tab.py",
    "watch_sync/tab.py",
]


def test_todas_las_tablas_tienen_menu():
    missing = [f for f in MENUS
               if "CustomContextMenu" not in (ROOT / f).read_text(encoding="utf-8")]
    assert not missing, f"sin menú clic-derecho: {missing}"


def test_handlers_conectados():
    sin_handler = [f for f in MENUS
                   if "customContextMenuRequested" not in (ROOT / f).read_text(encoding="utf-8")]
    assert not sin_handler, f"policy sin handler: {sin_handler}"
