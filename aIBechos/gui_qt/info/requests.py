"""Subpestaña "Solicitudes web" de Info: la cola compartida de solicitudes
(aIBechos_solicitudes.json) con su estado, quién la pidió y quién la tiene,
búsqueda y botón Cancelar por solicitud activa.

Antes vivía mezclada en Historial tras el checkbox "Solicitudes web": la
tabla reutiliza HistoryModel (gui_qt/history/tab.py), que ya pinta filas
kind=="solicitud". Datos: host._web_requests_rows vía
core/app_history_core.py::_sync_web_requests_history."""

from __future__ import annotations

import threading

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableView, QVBoxLayout, QWidget)

from core.status_colors import PENDING_COLOR, SUCCESS_COLOR
from gui_qt.actions import ActionsDelegate
from gui_qt.dialogs import confirm
from gui_qt.history.tab import H_ACT, H_DEST, H_FILE, H_STATE, HistoryModel

_ACTIVE = ("pending", "claimed", "downloading")


class WebRequestsTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self._all: list = []
        self._build_ui()
        host.requests_view = self
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        self.title = QLabel("Solicitudes de la web")
        self.title.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar título, cliente, estado...")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        head.addWidget(self.search)
        refresh = QPushButton("🔄 Actualizar")
        refresh.setToolTip("Releer la cola compartida de solicitudes del servidor.")
        refresh.clicked.connect(self._refresh_now)
        head.addWidget(refresh)
        root.addLayout(head)

        self.model = HistoryModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.setMouseTracking(True)
        self.delegate = ActionsDelegate(self.view)
        self.delegate.actionTriggered.connect(self._on_action)
        self.view.setItemDelegateForColumn(H_ACT, self.delegate)
        hdr = self.view.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(H_FILE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(H_DEST, QHeaderView.Stretch)
        root.addWidget(self.view, 1)
        self.empty = QLabel("")
        self.empty.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(self.empty)

    # ── Datos ──

    def refresh(self):
        self._all = list(self.host._web_requests_rows)
        self.title.setText(f"Solicitudes de la web  ({len(self._all)})")
        self._apply_search()

    def _apply_search(self):
        q = self.search.text().strip().lower()
        rows = [e for e in self._all if self.host._history_entry_matches(e, q)] if q else self._all
        self.model.set_rows(rows)
        if rows:
            self.empty.setText("")
        elif q:
            self.empty.setText("Ninguna solicitud coincide con la búsqueda.")
        else:
            self.empty.setText(self.host._web_requests_error or "No hay solicitudes de la web.")

    def _refresh_now(self):
        self.host._last_web_requests_sync_ts = 0.0   # siempre fresco
        self.host._sync_web_requests_history()

    def on_web_requests(self):
        if self.host._requests_visible:
            self.refresh()

    def on_shown(self):
        self.host._requests_visible = True
        self._refresh_now()
        self.refresh()

    def on_hidden(self):
        self.host._requests_visible = False

    # ── Acciones ──

    def _on_action(self, index: QModelIndex, action_id: str):
        if action_id != "cancel" or not (0 <= index.row() < len(self.model.rows)):
            return
        e = self.model.rows[index.row()]
        if e.get("status") not in _ACTIVE:
            return
        if not confirm(self, "Cancelar solicitud", f"¿Cancelar '{e.get('name', '')}'?",
                       "Se libera la solicitud y se quita su descarga de aMule si la tenía este equipo.",
                       confirm_text="Cancelar", danger=True):
            return
        req_id = e.get("id", "")
        self.ctx.set_status(f"Cancelando: {e.get('name', '')}...")

        def worker():
            ok = self.host._cancel_web_request(req_id)
            self.host.after(0, lambda: self._after_cancel(ok, e.get("name", "")))
        threading.Thread(target=worker, daemon=True).start()

    def _after_cancel(self, ok: bool, name: str):
        self.ctx.set_status(f"Solicitud cancelada: {name}" if ok else
                            "No se pudo cancelar (¿la cogió otro equipo?)",
                            SUCCESS_COLOR if ok else PENDING_COLOR)
        self._refresh_now()
