"""Pestaña "ℹ Info": Protegidos, Historial, Solicitudes web, Sincronizar
visionado y Estadísticas como subpestañas (cada una reutilizada tal cual).

Contenedor perezoso como el de Configuración: cada hija se construye la
primera vez que se muestra. Reenvía on_shown/on_hidden a la activa y expone
current_key() para que current_view_key() siga informando la vista real.
Las hijas registran sus host.*_view como siempre, así la lógica no cambia.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

# Imports ESTÁTICOS a propósito (nada de importlib con strings): PyInstaller
# solo empaqueta lo que ve en el análisis estático; con importación
# dinámica la app funcionaba desde fuente pero el instalado moría con
# "No module named 'gui_qt.history'" al abrir Info (real, v2.0.0).
from gui_qt.history.tab import HistoryTab
from gui_qt.info.requests import WebRequestsTab
from gui_qt.protected.tab import ProtectedTab
from gui_qt.stats.tab import StatsTab
from gui_qt.watch_sync.tab import WatchSyncTab

# (título, clave de vista, clase): mismo orden pedido.
SPECS = (
    ("📋 Historial", "history", HistoryTab),
    ("🌐 Solicitudes web", "requests", WebRequestsTab),
    ("🔒 Protegidos", "protected", ProtectedTab),
    ("🔄 Sincronizar visionado", "watch_sync", WatchSyncTab),
    ("📊 Estadísticas", "stats", StatsTab),
)


class InfoTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.pages: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        for title, key, _cls in SPECS:
            holder = QWidget()
            QVBoxLayout(holder).setContentsMargins(0, 0, 0, 0)
            holder.setProperty("page_key", key)
            self.tabs.addTab(holder, title)
        self.tabs.currentChanged.connect(lambda _i: self.ensure_current())
        root.addWidget(self.tabs, 1)
        host.info_view = self
        self.ensure_current()

    def ensure_current(self):
        i = self.tabs.currentIndex()
        if i < 0:
            return
        _title, key, cls = SPECS[i]
        if key in self.pages:
            page = self.pages[key]
        else:
            page = cls(self.host, self.tabs.widget(i))
            self.tabs.widget(i).layout().addWidget(page)
            self.pages[key] = page
        if hasattr(page, "on_shown"):
            page.on_shown()

    def current_key(self) -> str:
        i = self.tabs.currentIndex()
        return SPECS[i][1] if 0 <= i < len(SPECS) else "history"

    def on_shown(self):
        self.ensure_current()

    def on_hidden(self):
        for page in self.pages.values():
            if hasattr(page, "on_hidden"):
                page.on_hidden()

    # ── Reenvíos que la lógica pide a cada vista ──

    def on_web_requests(self):
        page = self.pages.get("requests")
        if page is not None and hasattr(page, "on_web_requests"):
            page.on_web_requests()

    def on_activity_synced(self):
        page = self.pages.get("history")
        if page is not None and hasattr(page, "on_activity_synced"):
            page.on_activity_synced()

    def refresh_from_cache(self):
        page = self.pages.get("stats")
        if page is not None and hasattr(page, "refresh_from_cache"):
            page.refresh_from_cache()
