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
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMenu, QPushButton, QTableView, QVBoxLayout, QWidget)

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
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        hdr = self.view.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(H_FILE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(H_DEST, QHeaderView.Stretch)
        hdr.resizeSection(H_ACT, 116)
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

    def _context_menu(self, pos):
        index = self.view.indexAt(pos)
        if not index.isValid() or not (0 <= index.row() < len(self.model.rows)):
            return
        self.view.selectRow(index.row())
        row, e = index.row(), self.model.rows[index.row()]
        st = e.get("status", "")
        menu = QMenu(self.view)

        def add(text, aid, enabled=True):
            a = QAction(text, menu)
            a.setEnabled(enabled)
            a.triggered.connect(lambda _c=False: self._on_action(self.model.index(row, 0), aid))
            menu.addAction(a)

        add("🔍 Buscar a mano en Descargas", "search", bool(e.get("name", "")))
        add("⬇ Descargar ahora mismo", "download", st in ("pending", "failed"))
        add("✕ Cancelar solicitud", "cancel", st in _ACTIVE)
        menu.addSeparator()
        copy = QAction("📋 Copiar título", menu)
        copy.setEnabled(bool(e.get("name", "")))
        copy.triggered.connect(lambda _c=False, t=e.get("name", ""): self._copy_title(t))
        menu.addAction(copy)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _copy_title(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self.ctx.set_status(f"Copiado: {text[:80]}", SUCCESS_COLOR)
    def _on_action(self, index: QModelIndex, action_id: str):
        if not (0 <= index.row() < len(self.model.rows)):
            return
        e = self.model.rows[index.row()]
        if action_id == "search":
            self._manual_search(e)
        elif action_id == "download":
            self._download_now(e)
        elif action_id == "cancel":
            self._cancel(e)

    def _manual_search(self, e: dict):
        """Abre Descargas buscando a mano lo pedido (misma query que usaría
        el lanzamiento automático; TMDB canónico en un hilo)."""
        if not e.get("name"):
            return
        self.ctx.set_status(f"Preparando búsqueda manual: {e.get('name', '')}...")
        host = self.host

        def worker():
            try:
                query, year, is_movie = host._web_request_search_query(dict(e))
            except Exception:
                query, year, is_movie = e.get("name", ""), None, e.get("media_type") == "movie"
            if not query:
                self.ctx.set_status("Sin título que buscar", PENDING_COLOR)
                return

            def apply():
                win = host.window
                if win is not None:
                    win.amule_search(query, expected_year=year, is_movie=is_movie)
            host.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _download_now(self, e: dict):
        """Ejecuta la descarga automática ya (reclama y lanza sin esperar
        al ciclo del worker)."""
        if e.get("status") not in ("pending", "failed"):
            return
        req_id = e.get("id", "")
        self.ctx.set_status(f"Lanzando descarga: {e.get('name', '')}...")

        def worker():
            msg = self.host._download_now_request(req_id)
            self.host.after(0, lambda: self._after_download_now(msg))
        threading.Thread(target=worker, daemon=True).start()

    def _after_download_now(self, msg: str):
        self.ctx.set_status(msg)
        self._refresh_now()

    def _cancel(self, e: dict):
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
