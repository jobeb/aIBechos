"""
Pestaña "Historial" en Qt: subidas y borrados (de este equipo o, con "Ver todo
el servidor", de todos), y las solicitudes de la web. Buscar, reintentar una
subida fallida, exportar el historial o el log, limpiar.

Datos y sincronización: core/app_history_core.py y app_files_core.py (vía host).
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import zipfile
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTableView, QVBoxLayout,
                               QWidget)

from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from gui_qt import theme
from gui_qt.actions import Action, ActionsDelegate, ActionsRole
from gui_qt.dialogs import confirm

H_DATE, H_FILE, H_KIND, H_WHO, H_DEST, H_SIZE, H_STATE, H_ACT = range(8)
HEADERS = ["Fecha", "Archivo", "Tipo", "Cliente", "Destino FTP / motivo", "Tamaño", "Estado", ""]
REQ_COLORS = {"done": SUCCESS_COLOR, "failed": ERROR_COLOR, "downloading": WARNING_COLOR,
              "claimed": WARNING_COLOR}


def _size(sz) -> str:
    sz = sz or 0
    if sz >= 1024 * 1024:
        return f"{sz / (1024 * 1024):.1f} MB"
    if sz >= 1024:
        return f"{sz // 1024} KB"
    return f"{sz} B"


class HistoryModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
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
        e = self.rows[index.row()]
        col = index.column()
        kind = e.get("kind", "subida")
        st = e.get("status", "ok")
        is_req, is_del = kind == "solicitud", kind == "borrado"
        error_msg = e.get("error_msg", "")
        showing_error = st == "error" and bool(error_msg) and not is_del and not is_req
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            if col == H_DATE:
                try:
                    return datetime.datetime.fromtimestamp(e.get("ts", 0)).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    return "—"
            if col == H_FILE:
                return e.get("name", "") if (is_req or is_del) else e.get("filename", "")
            if col == H_KIND:
                return "Solicitud" if is_req else "Borrado" if is_del else "Subida"
            if col == H_WHO:
                return e.get("person", "")
            if col == H_DEST:
                if is_req:
                    return e.get("detail", "")
                if is_del:
                    return e.get("reason", "")
                return error_msg if showing_error else e.get("remote", "")
            if col == H_SIZE:
                return "—" if is_req else _size(e.get("size", 0))
            if col == H_STATE:
                return e.get("status_es", "") if is_req else st.capitalize()
        elif role == Qt.ForegroundRole:
            if col == H_STATE:
                c = REQ_COLORS.get(st, PENDING_COLOR) if is_req else \
                    {"ok": SUCCESS_COLOR, "error": ERROR_COLOR}.get(st, PENDING_COLOR)
                return QBrush(QColor(c))
            if col == H_DEST and (showing_error or (is_req and st == "failed")):
                return QBrush(QColor(ERROR_COLOR))
        elif role == ActionsRole and col == H_ACT:
            can_retry = st == "error" and not is_del and not is_req
            return [Action("retry", "🔄", theme.ICON_DL_IDLE,
                           "Volver a intentar esta subida con el mismo archivo local." if can_retry else
                           "Solo se puede reintentar una subida que falló (un borrado no).",
                           enabled=can_retry)]
        return None


class HistoryTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self._all: list = []
        self._build_ui()
        host.history_view = self
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        self.title = QLabel("Historial de subidas")
        self.title.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(self.title)
        self.show_all = QCheckBox("Ver todo el servidor")
        self.show_all.setToolTip("Subidas y borrados de todos los equipos de este servidor")
        self.show_all.toggled.connect(self._on_show_all)
        self.requests = QCheckBox("Solicitudes web")
        self.requests.setEnabled(False)
        self.requests.toggled.connect(self._on_requests)
        head.addWidget(self.show_all)
        head.addWidget(self.requests)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar archivo, destino, cliente...")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        head.addWidget(self.search)
        for text, fn in (("🗑 Limpiar historial", self._clear), ("📥 Descargar log completo", self._export_log),
                         ("📥 Descargar historial", self._export_history)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            head.addWidget(b)
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
        for c, w in ((H_DATE, 125), (H_KIND, 75), (H_WHO, 110), (H_SIZE, 75), (H_STATE, 90), (H_ACT, 40)):
            hdr.resizeSection(c, w)
        root.addWidget(self.view, 1)
        self.empty = QLabel("")
        self.empty.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(self.empty)

    # ── Datos ──

    def refresh(self):
        host = self.host
        if self.show_all.isChecked() and self.requests.isChecked():
            self._all = list(host._web_requests_rows)
            self.title.setText(f"Solicitudes de la web  ({len(self._all)})")
        elif self.show_all.isChecked():
            self._all = list(reversed(host._shared_activity_history))
            self.title.setText(f"Historial de subidas  ({len(self._all)} registros, todo el servidor)")
        else:
            history = host._load_history()
            self._all = list(reversed(history))
            self.title.setText(f"Historial de subidas  ({len(history)} registros)")
        host._history_all = self._all
        self._apply_search()

    def _apply_search(self):
        q = self.search.text().strip().lower()
        rows = [e for e in self._all if self.host._history_entry_matches(e, q)] if q else self._all
        self.model.set_rows(rows)
        if rows:
            self.empty.setText("")
        elif q:
            self.empty.setText("Ningún registro coincide con la búsqueda.")
        elif self.show_all.isChecked() and self.requests.isChecked():
            self.empty.setText(self.host._web_requests_error or "No hay solicitudes de la web.")
        else:
            self.empty.setText("Sin subidas registradas todavía.")

    def on_web_requests(self):
        if self.host._history_visible and self.requests.isChecked():
            self.refresh()

    def on_activity_synced(self):
        if self.host._history_visible and self.show_all.isChecked():
            self.refresh()

    def on_shown(self):
        self.host._history_visible = True
        self.host._sync_activity_history_from_ftp()
        if self.requests.isChecked():
            self.host._sync_web_requests_history()
        self.refresh()

    def on_hidden(self):
        self.host._history_visible = False

    def _on_show_all(self, on: bool):
        self.requests.setEnabled(on)
        if not on and self.requests.isChecked():
            self.requests.setChecked(False)
        self.refresh()

    def _on_requests(self, on: bool):
        if on:
            self.host._last_web_requests_sync_ts = 0.0   # al encender, siempre fresco
            self.host._sync_web_requests_history()
        self.refresh()

    # ── Acciones ──

    def _on_action(self, index: QModelIndex, action_id: str):
        if action_id != "retry" or not (0 <= index.row() < len(self.model.rows)):
            return
        e = self.model.rows[index.row()]
        local_path = e.get("local_path", "")
        if not local_path or not os.path.exists(local_path):
            QMessageBox.warning(self, "No se puede reintentar",
                                "El archivo original ya no está en esa ubicación -- súbelo de nuevo desde Archivos.")
            return
        remote = e.get("remote", "")
        if not remote or "/" not in remote:
            QMessageBox.warning(self, "No se puede reintentar", "Falta la ruta de destino de este registro.")
            return
        remote_dir, remote_filename = remote.rsplit("/", 1)
        self.ctx.set_status(f"Reintentando: {Path(local_path).name}")
        threading.Thread(target=self.host._retry_history_upload_worker,
                         args=(local_path, remote_dir + "/", remote_filename), daemon=True).start()

    def _clear(self):
        if not confirm(self, "Limpiar historial", "¿Borrar el historial de subidas de este equipo?",
                       "Solo se borra el registro local; los archivos subidos no se tocan.",
                       confirm_text="Limpiar", danger=True):
            return
        try:
            self.host._history_path().write_text("[]", encoding="utf-8")
        except Exception:
            pass
        self.refresh()
        self.ctx.set_status("Historial borrado", WARNING_COLOR)

    def _export_history(self):
        dest, _f = QFileDialog.getSaveFileName(self, "Descargar historial", "historial_subidas.json",
                                               "JSON (*.json);;Todos los archivos (*.*)")
        if not dest:
            return
        try:
            Path(dest).write_text(json.dumps(self.host._load_history(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            self.ctx.set_status(f"Historial guardado en {dest}", SUCCESS_COLOR)
        except Exception as e:
            self.ctx.set_status(f"No se pudo guardar el historial: {e}", ERROR_COLOR)

    def _export_log(self):
        dest, _f = QFileDialog.getSaveFileName(self, "Descargar log completo", "logs_aibechos.zip",
                                               "Archivo ZIP (*.zip);;Todos los archivos (*.*)")
        if not dest:
            return
        from core.appdirs import app_data_dir
        from core.log_export import collect_log_files
        found = 0
        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in collect_log_files(app_data_dir()):
                    zf.write(p, arcname=p.name)
                    found += 1
            self.ctx.set_status(f"{found} archivo(s) de log guardados en {dest}" if found else
                                "No se encontró ningún archivo de log todavía",
                                SUCCESS_COLOR if found else WARNING_COLOR)
        except Exception as e:
            self.ctx.set_status(f"No se pudo guardar el log: {e}", ERROR_COLOR)
