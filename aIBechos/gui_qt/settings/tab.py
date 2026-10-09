"""
Pestaña "⚙ Configuración" en Qt: Cliente (cada equipo la suya) y Servidor
(compartida, con Publicar/Descartar). Las sub-pestañas se construyen al
abrirse por primera vez; lo nunca abierto no entra en el guardado y conserva
lo que hubiera (igual que _collect_settings en Tk). Nada se aplica hasta
"💾 Guardar configuración"; al salir con cambios se pregunta.

Lógica de guardar/aplicar/publicar: core/app_settings_core.py (vía host).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from core.applog import get_logger
from core.version import __version__
from gui_qt import theme
from gui_qt.settings import client_pages as cp
from gui_qt.settings import server_pages as sp
from gui_qt.settings.dialogs import UnsavedSettingsDialog

_log = get_logger("aIBechos.gui", "app.log")

CLIENT_PAGES = (("General", "general", cp.GeneralPage),
                ("Conexión FTP", "ftp", cp.FtpPage),
                ("Sincronizar visionado", "watch_sync", cp.WatchSyncConfigPage),
                ("Copia de seguridad", "backup", cp.BackupPage))
SERVER_PAGES = (("TMDB / IA", "tmdb", sp.TmdbPage),
                ("Plantillas", "templates", sp.TemplatesPage),
                ("Categorías", "categories", sp.CategoriesPage),
                ("Servidores de medios", "media", sp.MediaServersPage),
                ("Reservas", "reservas", sp.ReservationsPage),
                ("Preferencias descargas", "download_prefs", sp.DownloadPrefsPage))


def _normalized(key: str, value):
    """Valor comparable para detectar cambios: los enlaces guardados por
    versiones anteriores (o los de fábrica) no traen "background", que la
    página siempre añade -- sin esto, abrir Plantillas ya contaba como
    cambio sin guardar."""
    if key.startswith("custom_links_") and isinstance(value, list):
        return [{"name": (l or {}).get("name", ""), "url_template": (l or {}).get("url_template", ""),
                 "background": bool((l or {}).get("background", False))} for l in value]
    return value


class _LazyTabs(QTabWidget):
    """Sub-pestañas que construyen su página la primera vez que se ven."""

    def __init__(self, owner: "SettingsTab", specs):
        super().__init__()
        self.owner = owner
        self.specs = specs
        for title, key, _cls in specs:
            holder = QWidget()
            QVBoxLayout(holder).setContentsMargins(0, 0, 0, 0)
            holder.setProperty("page_key", key)
            self.addTab(holder, title)
        self.currentChanged.connect(lambda _i: self.ensure_current())

    def ensure_current(self):
        i = self.currentIndex()
        if i >= 0:
            self.owner.ensure_page(self.specs[i][1], self.widget(i), self.specs[i][2])


class SettingsTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.pages: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 4)
        top = QHBoxLayout()
        top.addStretch(1)
        self.save_btn = QPushButton("💾 Guardar configuración")
        self.save_btn.setProperty("accent", True)
        self.save_btn.setToolTip("Guardar los cambios de Ajustes. Hasta que pulses aquí no se aplica nada de "
                                 "lo que hayas tocado.")
        self.save_btn.clicked.connect(self.save)
        top.addWidget(self.save_btn)
        root.addLayout(top)

        self.outer = QTabWidget()
        self.client_tabs = _LazyTabs(self, CLIENT_PAGES)
        self.outer.addTab(self.client_tabs, "🖥 Cliente")
        server = QWidget()
        sv = QVBoxLayout(server)
        sv.setContentsMargins(0, 6, 0, 0)
        btns = QHBoxLayout()
        btns.addStretch(1)
        publish = QPushButton("📤 Publicar como configuración del servidor")
        publish.setToolTip("OJO: sobrescribe la configuración compartida PARA TODOS los usuarios con la de "
                           "este equipo. Es manual a propósito, para que no ocurra sin querer al guardar Ajustes.")
        publish.clicked.connect(self.publish)
        discard = QPushButton("📥 Descartar cambios y recuperar del servidor")
        discard.setToolTip("Tirar los cambios locales de configuración de servidor y volver a la que hay "
                           "publicada. No afecta a tus ajustes de cliente (FTP, carpeta vigilada, tu nombre).")
        discard.clicked.connect(self.host._discard_local_server_config)
        btns.addWidget(publish)
        btns.addWidget(discard)
        btns.addStretch(1)
        sv.addLayout(btns)
        d = QLabel("Sube TMDB/IA, plantillas, categorías FTP, Plex/Jellyfin, enlaces y la cuota de reservas de "
                   "este equipo (con sus claves y tokens) para que los adopten los otros clientes de este mismo "
                   "servidor, o descarta lo que tengas aquí sin publicar y recupera lo último que se haya "
                   "publicado.")
        d.setWordWrap(True)
        d.setAlignment(Qt.AlignCenter)
        d.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
        sv.addWidget(d)
        self.server_tabs = _LazyTabs(self, SERVER_PAGES)
        sv.addWidget(self.server_tabs, 1)
        self.outer.addTab(server, "🌐 Servidor")
        self.outer.currentChanged.connect(lambda _i: self._current_lazy().ensure_current())
        root.addWidget(self.outer, 1)
        ver = QLabel(f"aIBechos v{__version__}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
        root.addWidget(ver)
        host.settings_view = self
        self.client_tabs.ensure_current()

    def _current_lazy(self) -> _LazyTabs:
        return self.client_tabs if self.outer.currentIndex() == 0 else self.server_tabs

    def ensure_page(self, key, holder, cls):
        if key in self.pages:
            return
        try:
            page = cls(self.host)
        except Exception:
            _log.exception("Configuración (Qt): no se pudo construir la sub-pestaña %r", key)
            return
        holder.layout().addWidget(page)
        self.pages[key] = page

    # ── Guardar / cambios sin guardar ──

    def collect(self) -> dict:
        data = {}
        for page in self.pages.values():
            data.update(page.collect())
        return data

    def dirty(self) -> bool:
        cfg = self.host.config_data
        return any(_normalized(k, cfg.get(k)) != _normalized(k, v) for k, v in self.collect().items())

    def save(self):
        data = self.collect()
        requested = data.get("app_user_name")
        data = self.host._commit_settings(data)
        ftp = self.pages.get("ftp")
        if ftp is not None and "app_user_name" in data and data["app_user_name"] != requested:
            ftp.user_name.setText(data["app_user_name"])
        window = self.host.window
        if window is not None:
            window.on_settings_saved()

    def confirm_leave(self) -> bool:
        """Al salir de la pestaña: True si se puede salir."""
        try:
            dirty = self.dirty()
        except Exception:
            # Ante la duda, preguntar: perder cambios en silencio es peor.
            _log.warning("Configuración (Qt): no se pudo comprobar si hay cambios", exc_info=True)
            dirty = True
        if not dirty:
            return True
        result = UnsavedSettingsDialog.ask(self).result
        if result == "save":
            self.save()
            return True
        if result == "discard":
            self.reload_from_config()
            return True
        return False

    def publish(self):
        """Publica lo GUARDADO (como en Tk); con cambios a medias, avisa
        primero para no publicar algo distinto de lo que se ve."""
        if self.dirty() and not self.host._ask_yes_no(
                "Cambios sin guardar",
                "Hay cambios sin guardar en Ajustes y se publicaría lo último guardado, no lo que "
                "ves ahora. ¿Publicar de todas formas?"):
            return
        self.host._publish_server_config()

    def reload_from_config(self):
        """Tira las páginas construidas (config cambió por debajo: importar,
        sincronizar o descartar) y vuelve a construir la visible."""
        for key, page in list(self.pages.items()):
            page.setParent(None)
            page.deleteLater()
        self.pages.clear()
        self._current_lazy().ensure_current()
