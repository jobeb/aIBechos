"""
Pestaña "Protegidos" en Qt: lo reservado (protegido del borrado) por ti o, con
"Ver todo el servidor", por cualquiera; tu cuota y "Liberar" para soltar una
reserva propia. Las reservas son las del contexto (sincronizadas por FTP).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel,
                               QSplitter, QTableView, QVBoxLayout, QWidget)

from core.fmt import fmt_size
from core.status_colors import PENDING_COLOR
from gui_qt import theme
from gui_qt.actions import Action, ActionsDelegate, ActionsRole
from gui_qt.ficha import TmdbFicha

P_ICON, P_NAME, P_SIZE, P_OWNER, P_ACT = range(5)
HEADERS = ["", "Reservado", "Tamaño", "Reservado por", ""]


class ProtectedModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []     # [(clave, entrada)]
        self.user = ""

    def set_rows(self, rows, user):
        self.beginResetModel()
        self.rows = list(rows)
        self.user = user
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        _key, e = self.rows[index.row()]
        col = index.column()
        owner = e.get("reserved_by", "")
        mine = owner == self.user
        if role == Qt.DisplayRole:
            if col == P_ICON:
                return "📺" if e.get("media_type") == "tv" else "🎬"
            if col == P_NAME:
                return e.get("name", "")
            if col == P_SIZE:
                return fmt_size(e.get("size_bytes", 0) or 0)
            if col == P_OWNER:
                return owner
        elif role == Qt.ForegroundRole and col in (P_SIZE, P_OWNER):
            return QBrush(QColor(theme.ACCENT if (col == P_OWNER and mine) else PENDING_COLOR))
        elif role == ActionsRole and col == P_ACT:
            return [Action("release", "🔓", theme.ICON_RESTORE if mine else theme.ICON_NEUTRAL,
                           "Soltar esta reserva: deja de estar protegida y su tamaño vuelve a tu cuota."
                           if mine else "La reservó otra persona, así que solo ella puede soltarla.",
                           enabled=mine)]
        return None


class ProtectedTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        t = QLabel("Protegidos")
        t.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(t)
        hint = QLabel("Todo lo que hayas reservado desde Archivos o Liberar espacio.")
        hint.setStyleSheet(f"color: {PENDING_COLOR};")
        head.addWidget(hint)
        head.addStretch(1)
        self.show_all = QCheckBox("Ver todo el servidor")
        self.show_all.toggled.connect(lambda _c: self.render())
        head.addWidget(self.show_all)
        root.addLayout(head)
        self.quota_lbl = QLabel("")
        root.addWidget(self.quota_lbl)

        self.model = ProtectedModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(28)
        self.view.setMouseTracking(True)
        self.delegate = ActionsDelegate(self.view)
        self.delegate.actionTriggered.connect(self._on_action)
        self.view.setItemDelegateForColumn(P_ACT, self.delegate)
        hdr = self.view.horizontalHeader()
        hdr.setSectionResizeMode(P_NAME, QHeaderView.Stretch)
        for c, w in ((P_ICON, 30), (P_SIZE, 90), (P_OWNER, 130), (P_ACT, 44)):
            hdr.resizeSection(c, w)
        self.view.selectionModel().currentRowChanged.connect(lambda cur, _p: self._show(cur.row()))
        self.ficha = TmdbFicha(host.tmdb, "Pulsa una reserva\npara ver su ficha")
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.view)
        split.addWidget(self.ficha)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 270])
        root.addWidget(split, 1)

        host.protected_view = self
        self.ctx.reservations_changed.connect(self.render)
        self.render()

    def render(self):
        host = self.host
        user = host.config_data.get("app_user_name", "").strip()
        if not user:
            self.quota_lbl.setText("Configura \"Tu nombre\" en Ajustes → Conexión FTP para poder reservar espacio.")
            self.quota_lbl.setStyleSheet(f"color: {PENDING_COLOR};")
        else:
            used_gb, quota_gb, color, aviso = host._quota_status(user)
            self.quota_lbl.setText(f"Tu cuota ({user}): {used_gb:.1f} de {quota_gb:.0f}GB reservados{aviso}")
            self.quota_lbl.setStyleSheet(f"color: {color};")
        res = host._reservations
        if self.show_all.isChecked():
            shown = list(res.items())
        elif user:
            shown = [(k, e) for k, e in res.items() if e.get("reserved_by") == user]
        else:
            shown = []
        shown.sort(key=lambda kv: kv[1].get("name", "").lower())
        self.model.set_rows(shown, user)

    def on_shown(self):
        self.host._protected_visible = True
        self.ctx.sync_reservations()
        self.render()

    def on_hidden(self):
        self.host._protected_visible = False

    def _show(self, row: int):
        if not (0 <= row < len(self.model.rows)):
            return
        _k, e = self.model.rows[row]
        self.ficha.show(e.get("media_type", "movie"), e.get("tmdb_id"), e.get("name", ""))
        res_at = self.host._fmt_mark_ts(e.get("reserved_at", 0))
        self.ficha.set_extra(" · ".join(x for x in (
            f"Reservado por {e.get('reserved_by', '—')}", f"Reservado el: {res_at}" if res_at else "",
            fmt_size(e.get("size_bytes", 0) or 0)) if x))

    def _on_action(self, index: QModelIndex, action_id: str):
        if action_id == "release" and 0 <= index.row() < len(self.model.rows):
            _k, e = self.model.rows[index.row()]
            self.host._release_protected_row(_k, e)
