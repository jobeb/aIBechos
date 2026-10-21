"""Estado de la ventana que se recuerda entre sesiones: el reparto de los
paneles divididos (QSplitter) y la pestaña abierta.

Usa la misma config que los anchos de columna (gui_qt/columns.py, fijada
por MainWindow con columns.set_store) y, como ellos, solo guarda lo que
hace el usuario: los tamaños de partida de cada pestaña no cuentan.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject, QTimer

from core.applog import get_logger
from gui_qt import columns

_log = get_logger("aIBechos.qt", "app.log")

SPLITTERS_KEY = "splitter_sizes"
TABS_KEY = "last_tabs"
SAVE_DELAY_MS = 800

_pending = weakref.WeakSet()


def _get(config_key: str, key: str):
    store = columns._store
    if store is None or not key:
        return None
    try:
        data = store.get(config_key) or {}
        return data.get(key) if isinstance(data, dict) else None
    except Exception:
        return None


def _put(config_key: str, key: str, value) -> None:
    store = columns._store
    if store is None or not key:
        return
    try:
        data = dict(store.get(config_key) or {})
        if data.get(key) == value:
            return
        data[key] = value
        store.set(config_key, data)
        store.save()
    except Exception:
        _log.exception("No se pudo guardar el estado de la ventana (%s/%s)", config_key, key)


def flush_pending() -> None:
    """Guarda ya lo que esperaba (al salir, ver columns.flush_pending)."""
    for saver in list(_pending):
        saver.timer.stop()
        saver.save()


class _SplitterSaver(QObject):
    def __init__(self, splitter, key):
        super().__init__(splitter)
        self.splitter = splitter
        self.key = key
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(SAVE_DELAY_MS)
        self.timer.timeout.connect(self.save)
        splitter.splitterMoved.connect(self._moved)

    def _moved(self, *_):
        _pending.add(self)
        self.timer.start()

    def save(self):
        _pending.discard(self)
        sizes = self.splitter.sizes()
        saved = _get(SPLITTERS_KEY, self.key)
        if isinstance(saved, list) and len(saved) == len(sizes):
            # Un panel oculto mide 0: conservar lo que tenía.
            sizes = [s if (s > 0 or not self.splitter.widget(i).isHidden()) else saved[i]
                     for i, s in enumerate(sizes)]
        _put(SPLITTERS_KEY, self.key, sizes)


def remember_splitter(splitter, key: str) -> None:
    """Restaura el reparto que dejó el usuario (si lo hay; llamar DESPUÉS
    del setSizes de partida) y guarda los cambios que haga a mano. Antes de
    mostrarse, Qt reparte la diferencia de tamaño según los stretch factor:
    el panel lateral conserva su ancho y la tabla absorbe el resto."""
    saved = _get(SPLITTERS_KEY, key)
    if (isinstance(saved, list) and len(saved) == splitter.count()
            and all(isinstance(s, int) and s >= 0 for s in saved) and any(saved)):
        splitter.setSizes(saved)
    splitter._ui_state_saver = _SplitterSaver(splitter, key)


def remember_tab(tabs, key: str, keys: list, track: bool = True) -> None:
    """Abre la pestaña de *tabs* que estaba abierta la última vez y (con
    *track*) recuerda la que se elija. *keys*[i] es la clave estable de la
    pestaña i (el título o el orden pueden cambiar entre versiones)."""
    saved = _get(TABS_KEY, key)
    if saved in keys and keys.index(saved) < tabs.count():
        tabs.setCurrentIndex(keys.index(saved))
    if track:
        tabs.currentChanged.connect(
            lambda i: save_tab(key, keys[i]) if 0 <= i < len(keys) else None)


def save_tab(key: str, tab_key: str) -> None:
    _put(TABS_KEY, key, tab_key)
