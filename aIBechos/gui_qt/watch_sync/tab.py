"""
Pestaña "🔄 Sincronizar visionado" en Qt: marca como visto en Plex/Jellyfin lo
que ya está visto en la otra plataforma, para las parejas de usuarios
emparejadas en Ajustes. Primero muestra una vista previa; nada se escribe
hasta "Confirmar y sincronizar". Debajo, el historial.

Lógica: core/app_watch_sync_core.py (vía host); la sincronización programada
la lanza el propio host en segundo plano (_start_watch_sync_scheduler).
"""

from __future__ import annotations

import datetime
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableView, QVBoxLayout, QWidget)

from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from gui_qt.bridge import run_in_thread, ui
from gui_qt.dialogs import confirm


class _Table(QAbstractTableModel):
    def __init__(self, headers, row_fn, color_fn=None, parent=None):
        super().__init__(parent)
        self.headers, self.row_fn, self.color_fn = headers, row_fn, color_fn
        self.rows: list = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        r = self.rows[index.row()]
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return self.row_fn(r)[index.column()]
        if role == Qt.ForegroundRole and self.color_fn is not None:
            c = self.color_fn(r, index.column())
            return QBrush(QColor(c)) if c else None
        return None


def _view(model, stretch_col):
    v = QTableView()
    v.setModel(model)
    v.setSelectionBehavior(QAbstractItemView.SelectRows)
    v.setAlternatingRowColors(True)
    v.setShowGrid(False)
    v.verticalHeader().hide()
    v.verticalHeader().setDefaultSectionSize(24)
    v.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    return v


class WatchSyncTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self._actions: list = []
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        t = QLabel("Sincronizar visionado")
        t.setStyleSheet("font-size: 15pt; font-weight: bold;")
        t.setAlignment(Qt.AlignCenter)
        root.addWidget(t)
        d = QLabel("Marca como visto en una plataforma lo que ya está visto en la otra, para cada pareja de "
                   "usuarios emparejada en Configuración.")
        d.setWordWrap(True)
        d.setAlignment(Qt.AlignCenter)
        d.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(d)
        self.warn = QLabel("Activa Plex y Jellyfin en Configuración → Servidor → \"Servidores de medios\" "
                           "para poder usar esta utilidad.")
        self.warn.setAlignment(Qt.AlignCenter)
        self.warn.setStyleSheet(f"color: {WARNING_COLOR};")
        root.addWidget(self.warn)
        self.last_run = QLabel("")
        self.last_run.setAlignment(Qt.AlignCenter)
        self.last_run.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(self.last_run)
        self.run_btn = QPushButton("🔄 Sincronizar visionado ahora")
        self.run_btn.setProperty("accent", True)
        self.run_btn.clicked.connect(self.run)
        root.addWidget(self.run_btn, 0, Qt.AlignHCenter)
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        # Vista previa
        self.preview_box = QWidget()
        pv = QVBoxLayout(self.preview_box)
        pv.setContentsMargins(0, 0, 0, 0)
        q = QLabel("¿Aplicar estos cambios?")
        q.setStyleSheet("font-weight: bold;")
        pv.addWidget(q)
        self.summary = QLabel("")
        self.failed = QLabel("")
        self.failed.setStyleSheet(f"color: {WARNING_COLOR};")
        pv.addWidget(self.summary)
        pv.addWidget(self.failed)
        self.pmodel = _Table(["Título", "T/E", "Se marcará en"], lambda a: (
            a.item.name, f"{a.item.season}x{a.item.episode:02d}" if a.item.media_type == "episode" else "",
            a.target.capitalize()))
        pview = _view(self.pmodel, 0)
        pview.horizontalHeader().resizeSection(1, 60)
        pview.horizontalHeader().resizeSection(2, 120)
        pv.addWidget(pview, 1)
        bf = QHBoxLayout()
        bf.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.cancel_preview)
        ok = QPushButton("Confirmar y sincronizar")
        ok.setProperty("accent", True)
        ok.clicked.connect(self.confirm_preview)
        bf.addWidget(cancel)
        bf.addWidget(ok)
        bf.addStretch(1)
        pv.addLayout(bf)
        self.preview_box.hide()
        root.addWidget(self.preview_box, 1)

        # Historial
        hh = QHBoxLayout()
        self.hist_title = QLabel("Historial de sincronizaciones")
        self.hist_title.setStyleSheet("font-size: 11pt; font-weight: bold;")
        hh.addWidget(self.hist_title)
        hh.addStretch(1)
        clear = QPushButton("🗑 Limpiar historial")
        clear.clicked.connect(self.clear_history)
        hh.addWidget(clear)
        root.addLayout(hh)
        target_lbl = {"plex": "Plex", "jellyfin": "Jellyfin"}

        def hist_row(e):
            try:
                ts = datetime.datetime.fromtimestamp(e.get("ts", 0)).strftime("%d/%m/%Y %H:%M")
            except Exception:
                ts = "—"
            s, ep = e.get("season"), e.get("episode")
            return (ts, e.get("person", "?"), e.get("name", ""),
                    f"{s}x{ep:02d}" if s is not None and ep is not None else "—",
                    target_lbl.get(e.get("target"), "?"), "✓ Ok" if e.get("status", "ok") == "ok" else "✕ Error")

        self.hmodel = _Table(["Fecha", "Persona", "Título", "T/E", "Marcado en", "Estado"], hist_row,
                             lambda e, c: (SUCCESS_COLOR if e.get("status", "ok") == "ok" else ERROR_COLOR)
                             if c == 5 else None)
        hview = _view(self.hmodel, 2)
        for c, w in ((0, 125), (1, 110), (3, 60), (4, 100), (5, 80)):
            hview.horizontalHeader().resizeSection(c, w)
        root.addWidget(hview, 1)
        host.watch_sync_view = self
        self._update_enabled()
        self.refresh_history()

    def _update_enabled(self):
        cfg = self.host.config_data
        ok = bool(cfg.get("plex_enabled") and cfg.get("jellyfin_enabled"))
        self.warn.setVisible(not ok)
        self.run_btn.setEnabled(ok)
        last = cfg.get("watch_sync_last_run_ts", 0)
        self.last_run.setText("Última sincronización: " +
                              ("nunca" if not last else self.host._fmt_cleanup_scan_age(time.time() - last)))

    def on_shown(self):
        self._update_enabled()
        self.refresh_history()

    def _set_status(self, text, color=PENDING_COLOR):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    # ── Ejecutar ──

    def run(self):
        mappings = self.host.config_data.get("watch_sync_user_mappings", [])
        if not mappings:
            self._set_status("Empareja al menos un usuario primero, en Configuración → Cliente", ERROR_COLOR)
            return
        self.run_btn.setEnabled(False)
        self._set_status("Leyendo estado de visionado...", WARNING_COLOR)
        host = self.host

        def worker():
            actions, failed = host._watch_sync_collect_actions(
                mappings, status_cb=lambda t: ui(lambda tt=t: self._set_status(tt, WARNING_COLOR)))
            ui(lambda: self._show_preview(actions, failed))
        run_in_thread(worker)

    def _show_preview(self, actions, failed):
        self.run_btn.setEnabled(True)
        if not actions and not failed:
            self.preview_box.hide()
            self._set_status("Nada que sincronizar, ya está todo al día", SUCCESS_COLOR)
            return
        from core.watch_sync import summarize_actions
        s = summarize_actions(actions)
        self.summary.setText(f"Plex: {s['plex']['movies']} película(s), {s['plex']['episodes']} episodio(s)\n"
                             f"Jellyfin: {s['jellyfin']['movies']} película(s), {s['jellyfin']['episodes']} episodio(s)")
        self.failed.setText("No verificado (se omite): " + ", ".join(m.get("plex_user_name", "?") for m in failed)
                            if failed else "")
        self._actions = sorted(actions, key=lambda a: (a.item.name, a.item.season or 0, a.item.episode or 0))
        self.pmodel.set_rows(self._actions)
        self.preview_box.show()
        self._set_status(f"{len(self._actions)} cambio(s) pendientes de confirmar")

    def cancel_preview(self):
        self.preview_box.hide()
        self._actions = []
        self._set_status("Sincronización cancelada: no se ha cambiado nada")

    def confirm_preview(self):
        actions = list(self._actions)
        self.preview_box.hide()
        self._set_status("Sincronizando...", WARNING_COLOR)
        host = self.host

        def worker():
            ok, fail = host._watch_sync_apply(
                actions, status_cb=lambda t: ui(lambda tt=t: self._set_status(tt, WARNING_COLOR)))
            ui(lambda: (self._set_status(f"Sincronización completada: {ok} aplicados, {fail} fallidos",
                                         SUCCESS_COLOR if fail == 0 else WARNING_COLOR),
                        self._update_enabled(), self.refresh_history()))
        run_in_thread(worker)

    def on_scheduled_done(self):
        self._update_enabled()
        self.refresh_history()

    # ── Historial ──

    def refresh_history(self):
        entries = list(reversed(self.host._load_watch_sync_history()))
        self.hmodel.set_rows(entries)
        self.hist_title.setText(f"Historial de sincronizaciones  ({len(entries)})")

    def clear_history(self):
        if not confirm(self, "Limpiar historial", "¿Borrar el historial de sincronizaciones?",
                       confirm_text="Limpiar", danger=True):
            return
        try:
            self.host._watch_sync_history_path().write_text("[]", encoding="utf-8")
        except Exception:
            pass
        self.refresh_history()
