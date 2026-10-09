"""Cards de Recomendado estilo web: póster, título, año·nota, insignia de
estado, botón principal de ancho completo y fila de iconos con todos los
botones actuales (⚡🔍📋⬇🚫★🔒).

Como actions.py: píxeles, no widgets (una fila trae ~20 cards y hay ~19
filas por tipo; con widgets serían cientos de ventanas nativas). El delegate
pinta y detecta clics por rectángulos; los datos salen del modelo (rol
CardRole = dict de card de core/recommended_rows.py) y el estado vivo
(en servidor, descarga, ⚡, ★, 🔒) lo pregunta a la pestaña vía
get_state(item) en cada pintado, así un dataChanged basta para refrescar.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict

import requests
from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QToolTip

from core.appdirs import app_data_dir
from gui_qt import theme
from gui_qt.actions import Action
from gui_qt.bridge import run_in_thread, ui

#: Rol con el dict de card (core/recommended_rows.py::normalize_item).
CardRole = Qt.UserRole + 4

CARD_W = 170
PAD = 4
POSTER_W, POSTER_H = CARD_W - 2 * PAD, 234
TITLE_H, META_H = 36, 18
PRIMARY_H = 28
ICON_W, ICON_H, ICON_GAP = 24, 22, 3
CARD_H = PAD + POSTER_H + 4 + TITLE_H + META_H + 4 + PRIMARY_H + 4 + ICON_H + PAD

# Insignia sobre el póster según estado (web: .pbadge + tintes st-*).
BADGES = {
    "owned": ("En el servidor", "#2ecc71", "#06180f"),
    "downloading": ("Descargando", "#f39c12", "#1d1300"),
    "requested": ("Solicitado", "#3498db", "#ffffff"),
    "failed": ("Fallida", "#e74c3c", "#1f0503"),
}

# Botón principal según estado de descarga (mismos colores que la tabla).
DL_PRIMARY = {
    "busy": ("⏳ Buscando…", theme.ICON_DL_BUSY),
    "ok": ("✓ Lanzada", theme.ICON_DL_OK),
    "already": ("✓ En aMule", theme.ICON_DL_ALREADY),
    "fail": ("↻ Reintentar", theme.ICON_DL_FAIL),
}


class PosterCache:
    """Pósteres en memoria + disco (app data/posters/), con descarga en
    hilo y deduplicación de peticiones en vuelo. get() nunca bloquea:
    devuelve el QPixmap o None (y avisa vía on_ready al llegar)."""

    _MEM_MAX = 300
    _DISK_MAX = 1000

    def __init__(self):
        self._mem: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._flying: set = set()
        try:
            self._dir = app_data_dir() / "posters"
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._dir = None

    @staticmethod
    def _disk_path(url: str, directory) -> str | None:
        if directory is None:
            return None
        return os.path.join(str(directory), hashlib.sha1(url.encode("utf-8")).hexdigest())

    def get(self, url: str | None) -> QPixmap | None:
        if not url:
            return None
        with self._lock:
            pm = self._mem.get(url)
            if pm is not None:
                self._mem.move_to_end(url)
                return pm
        path = self._disk_path(url, self._dir)
        if path is not None and os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                pm = QPixmap()
                if pm.loadFromData(raw):
                    with self._lock:
                        self._mem[url] = pm
                        while len(self._mem) > self._MEM_MAX:
                            self._mem.popitem(last=False)
                    return pm
            except OSError:
                pass
        return None

    def load_async(self, url: str | None, on_ready) -> None:
        """Descarga *url* en un hilo y llama on_ready() en la GUI al
        terminar (haya póster o no). Sin url o ya en vuelo: nada."""
        if not url or self.get(url) is not None:
            if url and self.get(url) is not None:
                ui(on_ready)
            return
        with self._lock:
            if url in self._flying:
                return
            self._flying.add(url)

        def worker():
            try:
                resp = requests.get(url, timeout=10)
                raw = resp.content if resp.ok else None
            except Exception:
                raw = None
            if raw:
                pm = QPixmap()
                if pm.loadFromData(raw):
                    with self._lock:
                        self._mem[url] = pm
                        while len(self._mem) > self._MEM_MAX:
                            self._mem.popitem(last=False)
                    path = self._disk_path(url, self._dir)
                    if path is not None:
                        try:
                            with open(path, "wb") as f:
                                f.write(raw)
                            self._prune_disk()
                        except OSError:
                            pass
            with self._lock:
                self._flying.discard(url)
            ui(on_ready)
        run_in_thread(worker)

    def _prune_disk(self) -> None:
        try:
            files = sorted((os.path.join(str(self._dir), n) for n in os.listdir(str(self._dir))),
                           key=os.path.getmtime)
        except OSError:
            return
        for old in files[:max(0, len(files) - self._DISK_MAX)]:
            try:
                os.remove(old)
            except OSError:
                pass


def card_buttons(item: dict, state: dict) -> tuple:
    """(primary, icons): botón principal de ancho completo + iconos, con los
    mismos textos/colores/tooltips que la tabla actual. *state* trae
    in_server, dl (None/busy/ok/already/fail), auto_on, fav y locked."""
    is_tv = item.get("media_type") == "tv"
    in_server = bool(state.get("in_server"))
    dl = state.get("dl")
    if in_server:
        primary = Action("download", "✓ En servidor", theme.ICON_DL_OK, "Ya está en el servidor",
                         enabled=False)
    elif dl in DL_PRIMARY:
        text, color = DL_PRIMARY[dl]
        primary = Action("download", text, color, "Descargar de nuevo en aMule")
    elif is_tv:
        primary = Action("download", "⬇ Piloto (1x01)", theme.ICON_DL_IDLE,
                         "Descargar el PILOTO (1x01) de esta serie en aMule (en segundo plano). "
                         "Para completar la serie entera usa ⚡.")
    else:
        primary = Action("download", "⬇ Descargar", theme.ICON_DL_IDLE,
                         "Buscar en aMule y descargar el mejor candidato para esta película "
                         "(en segundo plano).")
    icons = []
    if is_tv:
        on = bool(state.get("auto_on"))
        icons.append(Action("auto", "⚡", theme.ACCENT if on else theme.ICON_NEUTRAL,
                            "Quitar autocompletado de la serie" if on else "Autocompletar esta serie"))
    icons += [
        Action("search", "🔍", theme.ICON_AMULE, "Buscar en aMule (abre la pestaña Descargas)"),
        Action("copy", "📋", theme.ICON_COPY, "Copiar nombre de la obra al portapapeles"),
        Action("dismiss", "🚫", theme.ICON_IGNORE, "Quitar recomendación"),
        Action("fav", "★", theme.ACCENT if state.get("fav") else theme.ICON_NEUTRAL,
               state.get("fav_tip") or ("Quitar de favoritos" if state.get("fav")
                                        else "Marcar como favorita")),
        Action("lock", "🔒" if state.get("locked") else "🔓",
               theme.ACCENT if state.get("locked") else theme.ICON_NEUTRAL,
               state.get("lock_tip") or ("Quitar protección" if state.get("locked") else "Proteger")),
    ]
    return primary, icons


def card_state_key(state: dict) -> str:
    """Qué insignia lleva el póster: owned > downloading > requested >
    failed (misma prioridad que la web). Fase 1: requested siempre False
    (llegará leyendo la cola compartida de solicitudes)."""
    if state.get("in_server"):
        return "owned"
    if state.get("dl") == "busy":
        return "downloading"
    if state.get("requested"):
        return "requested"
    if state.get("dl") == "fail":
        return "failed"
    return ""


class _Layout:
    """Rectángulos de una card (pintado y hit-testing usan lo mismo)."""

    def __init__(self, rect: QRect, n_icons: int):
        x, y, w = rect.left(), rect.top(), rect.width()
        self.poster = QRect(x + PAD, y + PAD, POSTER_W, POSTER_H)
        y += PAD + POSTER_H + 4
        self.title = QRect(x + PAD, y, w - 2 * PAD, TITLE_H)
        y += TITLE_H
        self.meta = QRect(x + PAD, y, w - 2 * PAD, META_H)
        y += META_H + 4
        self.primary = QRect(x + PAD, y, w - 2 * PAD, PRIMARY_H)
        y += PRIMARY_H + 4
        total_icons = n_icons * ICON_W + max(0, n_icons - 1) * ICON_GAP
        ix = x + (w - total_icons) // 2
        self.icons = [QRect(ix + i * (ICON_W + ICON_GAP), y, ICON_W, ICON_H) for i in range(n_icons)]
        inner = self.poster
        self.badge = QRect(inner.left() + 4, inner.top() + 4, 0, 18)
        self.rank = QRect(inner.left() + 4, inner.bottom() - 40, 60, 36)


class CardDelegate(QStyledItemDelegate):
    """Pinta cards y emite cardAction(item, id) / cardSelected(item)."""

    cardAction = Signal(object, str)   # (dict de card, id de botón)
    cardSelected = Signal(object)      # (dict de card: clic fuera de botones)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.posters = PosterCache()
        self.get_state = lambda _item: {}
        self._views = []

    def attach(self, view) -> None:
        if view not in self._views:
            self._views.append(view)

    def _touch_views(self) -> None:
        for v in list(self._views):
            try:
                v.viewport().update()
            except RuntimeError:
                self._views.remove(v)

    def _item(self, index) -> dict | None:
        item = index.data(CardRole)
        return item if isinstance(item, dict) else None

    # ── Pintado ──

    def paint(self, painter, option, index):
        item = self._item(index)
        if item is None:   # esqueleto mientras carga la fila
            self._paint_skeleton(painter, option)
            return
        state = self.get_state(item)
        primary, icons = card_buttons(item, state)
        lay = _Layout(option.rect, len(icons))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_poster(painter, option, index, item, lay, state)
        self._paint_texts(painter, item, lay)
        self._paint_primary(painter, primary, lay)
        self._paint_icons(painter, icons, lay)
        painter.restore()

    def _paint_skeleton(self, painter, option):
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_ALT))
        r = QRect(option.rect.left() + PAD, option.rect.top() + PAD, POSTER_W, POSTER_H)
        painter.drawRoundedRect(r, 6, 6)
        for y, h in ((r.bottom() + 8, 12), (r.bottom() + 24, 10)):
            painter.drawRoundedRect(QRect(r.left(), y, POSTER_W - 30, h), 3, 3)
        painter.restore()

    def _paint_poster(self, painter, option, index, item, lay, state):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        key = card_state_key(state)
        border = {"owned": "#2f6b55", "downloading": "#6b5326",
                  "requested": "#2d5577", "failed": "#8a3a34"}.get(key)
        painter.setPen(QColor(border) if border else QColor(theme.BORDER))
        painter.setBrush(QColor(theme.BG_ALT))
        painter.drawRoundedRect(lay.poster, 6, 6)
        pm = self.posters.get(item.get("poster_url"))
        if pm is None and item.get("poster_url"):
            self.posters.load_async(item["poster_url"], self._touch_views)
            painter.setPen(QColor(theme.PENDING_COLOR))
            painter.drawText(lay.poster, Qt.AlignCenter, "…")
        elif pm is not None and not pm.isNull():
            scaled = pm.scaled(POSTER_W, POSTER_H, Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
            x = lay.poster.left() + (POSTER_W - scaled.width()) // 2
            y = lay.poster.top() + (POSTER_H - scaled.height()) // 2
            painter.setClipRect(lay.poster)
            painter.drawPixmap(x, y, scaled)
            painter.setClipping(False)
        else:
            painter.setPen(QColor(theme.PENDING_COLOR))
            painter.drawText(lay.poster.adjusted(6, 6, -6, -6),
                             Qt.AlignCenter | Qt.TextWordWrap, item.get("title") or "Sin póster")
        if key:
            text, bg, fg = BADGES[key]
            fm = QFontMetrics(painter.font())
            w = fm.horizontalAdvance(text) + 12
            lay.badge.setWidth(w)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(lay.badge, 8, 8)
            painter.setPen(QColor(fg))
            painter.drawText(lay.badge, Qt.AlignCenter, text)
        if item.get("rank"):
            painter.setPen(QColor("#000000"))
            font = painter.font()
            font.setPointSize(26)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(lay.rank, Qt.AlignLeft | Qt.AlignVCenter, str(item["rank"]))
        painter.restore()

    def _paint_texts(self, painter, item, lay):
        painter.save()
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(lay.title, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                         item.get("title") or "")
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(theme.PENDING_COLOR))
        meta = (item.get("year") or "") + (f" · ★ {item['vote']:.1f}" if item.get("vote") else "")
        painter.drawText(lay.meta, Qt.AlignLeft | Qt.AlignVCenter, meta)
        painter.restore()

    def _paint_primary(self, painter, primary, lay):
        self._paint_chip(painter, lay.primary, primary, bold=True)

    def _paint_icons(self, painter, icons, lay):
        for rect, act in zip(lay.icons, icons):
            self._paint_chip(painter, rect, act, bold=False)

    @staticmethod
    def _paint_chip(painter, rect, act, bold):
        color = QColor(act.color)
        if not act.enabled:
            color.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 5, 5)
        font = painter.font()
        font.setBold(bold)
        painter.setFont(font)
        # Botón principal: texto claro (sus fondos son oscuros); iconos:
        # claro también, igual que los chips de la tabla (actions.py).
        painter.setPen(QColor("#f2f2f2") if act.enabled else QColor("#8a8a8a"))
        painter.drawText(rect, Qt.AlignCenter, act.glyph if len(act.glyph) <= 2 else act.glyph[:18])

    def sizeHint(self, option, index):
        return QSize(CARD_W, CARD_H)

    # ── Clics y tooltips ──

    def _hit(self, option, index, pos):
        item = self._item(index)
        if item is None:
            return None, None
        state = self.get_state(item)
        primary, icons = card_buttons(item, state)
        lay = _Layout(option.rect, len(icons))
        if lay.primary.contains(pos):
            return item, primary
        for rect, act in zip(lay.icons, icons):
            if rect.contains(pos):
                return item, act
        return item, None

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            item, act = self._hit(option, index, event.position().toPoint())
            if item is None:
                return False
            if act is not None:
                if act.enabled:
                    self.cardAction.emit(item, act.id)
            else:
                self.cardSelected.emit(item)
            return True
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            item, act = self._hit(option, index, event.position().toPoint())
            if act is not None:
                return True   # el botón no selecciona la card
        return super().editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.ToolTip:
            item, act = self._hit(option, index, event.pos())
            if act is not None:
                QToolTip.showText(event.globalPos(), act.tooltip, view)
                return True
            if item is not None and item.get("overview"):
                QToolTip.showText(event.globalPos(), item["overview"], view)
                return True
            QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)
