"""Columnas que llenan la tabla sin dejar de ser arrastrables, y cuyo ancho
se recuerda entre sesiones.

QHeaderView.Stretch reparte el sitio sobrante, pero a cambio la columna
deja de poder redimensionarse a mano (no sale el cursor ↔ en su borde) y
se recalcula cada vez que se arrastra cualquier otra: ensanchar
"Detectado" estrechaba "Nombre original" (real, en casi todas las tablas).

fill_columns() deja TODAS las columnas en Interactive (arrastrar una solo
cambia esa). Las de relleno llenan la tabla la primera vez que se muestra
y después solo absorben lo que crece o mengua la tabla (ventana, splitter,
barra de scroll), nunca al arrastrar otra.

Con *key*, lo que el usuario arrastra se guarda en la config
("column_widths": {key: {"widths": [...], "viewport": ancho de la tabla,
"hidden": [columnas ocultas]}})
y se restaura la próxima vez que la tabla se muestra; si la tabla mide
distinto que al guardar, las de relleno absorben solo esa diferencia. Solo
cuenta lo arrastrado a mano: los anchos de partida que pone cada pestaña y
los ajustes de relleno no se guardan.

Botón derecho en la cabecera: "Restablecer anchos" (vuelve a los de
partida y olvida lo guardado) y mostrar/ocultar cada columna.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QHeaderView, QMenu

from core.applog import get_logger

_log = get_logger("aIBechos.qt", "app.log")

#: Por debajo de esto una columna de relleno no se encoge sola (el usuario
#: sí puede estrecharla a mano); si no cabe todo, sale la barra horizontal.
MIN_FILL_WIDTH = 120

#: Clave de la config donde se guardan los anchos.
CONFIG_KEY = "column_widths"

#: Espera tras el último arrastre antes de escribir config.json (un
#: arrastre emite decenas de cambios de tamaño).
SAVE_DELAY_MS = 800

_store = None
_pending = weakref.WeakSet()     # con un guardado aún en espera


def set_store(config) -> None:
    """La config (get/set/save) donde se recuerdan los anchos; la fija
    MainWindow antes de construir las pestañas. Sin ella no se guarda nada."""
    global _store
    _store = config


def saved_layout(key: str):
    """(anchos, ancho de la tabla al guardar, columnas ocultas) o None."""
    if _store is None or not key:
        return None
    try:
        data = _store.get(CONFIG_KEY) or {}
        entry = data.get(key) if isinstance(data, dict) else None
        widths = entry.get("widths") if isinstance(entry, dict) else None
        viewport = entry.get("viewport") if isinstance(entry, dict) else None
        hidden = entry.get("hidden", []) if isinstance(entry, dict) else []
    except Exception:
        return None
    if (isinstance(widths, list) and widths and all(isinstance(w, int) for w in widths)
            and isinstance(viewport, int)):
        if not (isinstance(hidden, list) and all(isinstance(c, int) for c in hidden)):
            hidden = []
        return widths, viewport, hidden
    return None


def flush_pending() -> None:
    """Guarda ya los arrastres que aún esperaban (al salir: la app acaba con
    os._exit y el temporizador no llegaría a saltar)."""
    for filler in list(_pending):
        filler._save_timer.stop()
        filler.save()


def _forget_layout(key: str) -> None:
    if _store is None or not key:
        return
    try:
        data = dict(_store.get(CONFIG_KEY) or {})
        if data.pop(key, None) is not None:
            _store.set(CONFIG_KEY, data)
            _store.save()
    except Exception:
        _log.exception("No se pudieron olvidar los anchos de columna (%s)", key)


def _save_layout(key: str, widths: list, viewport: int, hidden=None) -> None:
    if _store is None or not key:
        return
    try:
        data = dict(_store.get(CONFIG_KEY) or {})
        entry = {"widths": widths, "viewport": viewport}
        if hidden:
            entry["hidden"] = sorted(hidden)
        if data.get(key) == entry:
            return
        data[key] = entry
        _store.set(CONFIG_KEY, data)
        _store.save()
    except Exception:
        _log.exception("No se pudieron guardar los anchos de columna (%s)", key)


class _FillColumns(QObject):
    def __init__(self, view, cols, min_width, key):
        super().__init__(view)
        self._view = view
        self._cols = list(cols)
        self._min = min_width
        self._key = key
        self._ready = False       # hasta la primera vez que se muestra
        self._busy = False        # cambios nuestros, no del usuario
        # Último reparto "de verdad" (lo restaurado, lo arrastrado o el
        # primer llenado) y el ancho de tabla con el que se hizo: los anchos
        # de relleno siempre se calculan desde aquí, no desde los actuales,
        # para que estrechar (topando en el mínimo) y volver a ensanchar
        # deje la tabla como estaba. Real: al arrancar maximizada la tabla
        # pasa antes por el tamaño normal y lo guardado volvía descolocado.
        self._base = None
        self._defaults = None     # anchos de partida (para "Restablecer")
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save)
        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.sectionResized.connect(self._on_section_resized)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._header_menu)
        view.viewport().installEventFilter(self)

    def header(self) -> QHeaderView:
        v = self._view
        return v.horizontalHeader() if hasattr(v, "horizontalHeader") else v.header()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            if not self._ready:
                self._ready = True
                hdr = self.header()
                self._defaults = [hdr.sectionSize(c) for c in range(hdr.count())]
                if self.restore() is None:
                    self.fit()                    # primera vez: llenar
                    self._remember(filled=True)
            self.apply()
        return False

    def _on_section_resized(self, *_):
        if self._ready and not self._busy:
            self._remember()
            if self._key:
                _pending.add(self)
                self._save_timer.start()

    def _remember(self, filled=False):
        hdr = self.header()
        viewport = self._view.viewport().width()
        if filled:
            # Primer llenado con la tabla aún estrecha: si sobresale (relleno
            # topado en el mínimo), la tabla "llenaría" justo con ese largo;
            # al crecer, lo primero es recuperar eso, no inflar el relleno.
            viewport = max(viewport, hdr.length())
        old = self._base[0] if self._base else []
        # Una oculta mide 0: conservar el ancho que tenía, para volver a él.
        self._base = ([old[c] if hdr.isSectionHidden(c) and c < len(old) else hdr.sectionSize(c)
                       for c in range(hdr.count())], viewport)

    def restore(self):
        """Aplica lo guardado; devuelve el ancho de la tabla al guardarlo
        (None si no había nada o la tabla cambió de columnas)."""
        layout = saved_layout(self._key)
        hdr = self.header()
        if not layout or len(layout[0]) != hdr.count():
            return None
        widths, viewport, hidden = layout
        self._busy = True
        try:
            for c in hidden:
                if 0 <= c < hdr.count() and self._can_hide(c):
                    hdr.hideSection(c)
        finally:
            self._busy = False
        # Qt no deja bajar de minimumSectionSize (30 px con el estilo ya
        # aplicado), y los anchos de partida (★/🔒 a 28) se pusieron antes.
        narrowest = min((w for w in widths if w > 0), default=0)
        if 0 < narrowest < hdr.minimumSectionSize():
            hdr.setMinimumSectionSize(narrowest)
        self._busy = True
        try:
            for c, w in enumerate(widths):
                if w > 0 and not hdr.isSectionHidden(c):
                    hdr.resizeSection(c, w)
        finally:
            self._busy = False
        self._base = ([w if w > 0 else hdr.sectionSize(c) for c, w in enumerate(widths)], viewport)
        return viewport

    def save(self):
        _pending.discard(self)
        if self._base is None:
            return
        hdr = self.header()
        layout = saved_layout(self._key)
        saved = layout[0] if layout else []
        base, viewport = self._base
        hidden = [c for c in range(hdr.count()) if hdr.isSectionHidden(c)]
        widths = []
        for c in range(hdr.count()):
            # Una oculta mide 0: conservar lo que tenía guardado.
            if hdr.isSectionHidden(c):
                kept = base[c] if c < len(base) else 0
                widths.append(kept if kept > 0 else (saved[c] if c < len(saved) else 0))
            else:
                widths.append(base[c] if c < len(base) else hdr.sectionSize(c))
        _save_layout(self._key, widths, viewport, hidden)

    # ── Menú de la cabecera ──

    def _can_hide(self, col) -> bool:
        """Siempre queda al menos una columna de relleno visible (si no, la
        tabla no tendría qué ensanchar) y al menos una columna en total."""
        hdr = self.header()
        visible = [c for c in range(hdr.count()) if not hdr.isSectionHidden(c) and c != col]
        if not visible:
            return False
        return col not in self._cols or any(c in self._cols for c in visible)

    def _title(self, col) -> str:
        model = self._view.model()
        text = model.headerData(col, Qt.Horizontal, Qt.DisplayRole) if model is not None else None
        text = str(text or "").strip()
        return text or f"Columna {col + 1}"

    def _header_menu(self, pos):
        menu = self.build_menu()
        menu.exec(self.header().viewport().mapToGlobal(pos))

    def build_menu(self) -> QMenu:
        hdr = self.header()
        menu = QMenu(self._view)
        menu.addAction("Restablecer anchos de columna", self.reset)
        menu.addSeparator()
        for c in range(hdr.count()):
            act = menu.addAction(self._title(c))
            act.setCheckable(True)
            shown = not hdr.isSectionHidden(c)
            act.setChecked(shown)
            act.setEnabled(not shown or self._can_hide(c))
            act.toggled.connect(lambda on, c=c: self.set_visible(c, on))
        return menu

    def _changed_by_user(self):
        """Tras un cambio deliberado (ocultar/mostrar): llenar el sitio que
        queda o falta, tomarlo como nueva base y guardarlo."""
        self.fit()
        self._remember(filled=True)
        if self._key:
            _pending.add(self)
            self._save_timer.start()

    def set_visible(self, col, on: bool):
        hdr = self.header()
        if on == (not hdr.isSectionHidden(col)) or (not on and not self._can_hide(col)):
            return
        self._busy = True
        try:
            if on:
                hdr.showSection(col)
                width = self._base[0][col] if self._base and col < len(self._base[0]) else 0
                if width > 0:
                    hdr.resizeSection(col, width)
                elif hdr.sectionSize(col) <= 0:
                    hdr.resizeSection(col, hdr.defaultSectionSize())
            else:
                hdr.hideSection(col)
        finally:
            self._busy = False
        self._changed_by_user()

    def reset(self):
        """Vuelve a los anchos de partida, con todas las columnas, y olvida
        lo guardado (así una versión nueva puede cambiarlos)."""
        hdr = self.header()
        self._busy = True
        try:
            for c in range(hdr.count()):
                hdr.showSection(c)
                if self._defaults and c < len(self._defaults) and self._defaults[c] > 0:
                    hdr.resizeSection(c, self._defaults[c])
        finally:
            self._busy = False
        self.fit()
        self._remember(filled=True)
        self._save_timer.stop()
        _pending.discard(self)
        _forget_layout(self._key)

    def fill_cols(self):
        hdr = self.header()
        return [c for c in self._cols if c < hdr.count() and not hdr.isSectionHidden(c)]

    def fit(self):
        """Reparte entre las de relleno lo que falte o sobre para llenar
        justo la tabla."""
        cols = self.fill_cols()
        if not cols:
            return
        hdr = self.header()
        spare = self._view.viewport().width() - hdr.length()
        self._spread(cols, {c: hdr.sectionSize(c) for c in cols}, spare)

    def apply(self):
        """Anchos de relleno = los de la base + lo que ha cambiado la tabla
        desde entonces (sin bajar del mínimo; las demás no se tocan)."""
        cols = self.fill_cols()
        if not cols or self._base is None:
            return
        widths, base_vp = self._base
        hdr = self.header()
        base = {c: widths[c] if c < len(widths) else hdr.sectionSize(c) for c in cols}
        self._spread(cols, base, self._view.viewport().width() - base_vp)

    def _spread(self, cols, base, delta):
        hdr = self.header()
        share, extra = divmod(delta, len(cols))
        self._busy = True
        try:
            for i, c in enumerate(cols):
                b = base[c]
                new = max(b + share + (1 if i < extra else 0), min(b, self._min))
                if new != hdr.sectionSize(c):
                    hdr.resizeSection(c, new)
        finally:
            self._busy = False


def fill_columns(view, *cols, key: str = "", min_width: int = MIN_FILL_WIDTH) -> _FillColumns:
    """Deja arrastrables todas las columnas de *view* (QTableView/QTreeView),
    hace que *cols* llenen el ancho disponible al cambiar de tamaño y, con
    *key*, recuerda entre sesiones los anchos que el usuario arrastra."""
    filler = _FillColumns(view, cols, min_width, key)
    view._fill_columns = filler     # vive lo que la vista
    return filler
