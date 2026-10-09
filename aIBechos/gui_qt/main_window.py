"""
Ventana principal de la interfaz Qt: pestañas, cabecera (⚡ Auto, espacio
libre en el FTP), barra de estado y bandeja.

La lógica sin interfaz vive en un único QtAppCore (gui_qt/core_host.py),
compartido por las pestañas -- igual que en Tk todo colgaba de la App.

Las pestañas que todavía no están migradas muestran un aviso con un botón para
abrir la versión clásica (Tk) -- las dos no pueden estar abiertas a la vez
(core/single_instance.py), así que el botón cierra esta y lanza la otra.
"""

from __future__ import annotations

import os
import re
import sys
import time

from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from core.applog import get_logger
from core.status_colors import SUCCESS_COLOR, WARNING_COLOR
from core.version import __version__
from gui_qt.bridge import run_in_thread, ui
from gui_qt.context import AppContext
from gui_qt.core_host import QtAppCore

_log = get_logger("aIBechos.qt", "app.log")

# (clave, título) en el mismo orden que la versión Tk.
TABS = [
    ("files", "📁 Archivos"),
    ("movies", "🎬 Recomendado"),
    ("missing", "🔍 Episodios"),
    ("downloads", "📥 Descargas"),
    ("cleanup", "🗑 Liberar espacio"),
    ("info", "ℹ Info"),
    ("config", "⚙ Configuración"),
]
# Clave de pestaña Qt -> clave de vista de la versión Tk (App._current_view_key),
# que es la que consulta la lógica compartida. "info" informa la subpestaña
# activa (InfoTab.current_key): la lógica que distinguía history/protected/
# watch_sync/stats sigue viendo la misma clave que antes.
VIEW_KEYS = {"missing": "missing_ep"}
MIGRATED = {"files", "movies", "missing", "downloads", "cleanup", "info", "config"}
UPLOAD_NOTIFICATION_DEBOUNCE_MS = 8000   # mismo valor que la versión Tk
FTP_SPACE_PERIODIC_MS = 5 * 60 * 1000


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, start_minimized: bool = False):
        super().__init__()
        self.ctx = ctx
        self._quitting = False
        self.host = QtAppCore(ctx)
        self.host.window = self
        self.setWindowTitle(f"aIBechos v{__version__}")
        icon_file = resource_path("LogoaIBechos.ico" if sys.platform == "win32" else "IconoSinFondo.png")
        self._icon = QIcon(icon_file) if os.path.exists(icon_file) else QIcon()
        self.setWindowIcon(self._icon)
        self.resize(1360, 860)
        self._apply_window_state()
        self.setMinimumSize(1000, 680)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 0)
        outer.addLayout(self._build_header())

        self.tabs = QTabWidget()
        self._tab_widgets = {}
        for key, title in TABS:
            if key == "files":
                from gui_qt.files.tab import FilesTab
                w = FilesTab(self.host)
            elif key == "movies":
                from gui_qt.movies.tab import MoviesTab
                w = MoviesTab(self.host)
            elif key == "cleanup":
                from gui_qt.cleanup.tab import CleanupTab
                w = CleanupTab(self.host)
            elif key == "info":
                from gui_qt.info.tab import InfoTab
                w = InfoTab(self.host)
            elif key == "config":
                from gui_qt.settings.tab import SettingsTab
                w = SettingsTab(self.host)
            elif key == "downloads":
                from gui_qt.downloads.tab import DownloadsTab
                w = DownloadsTab(self.host)
            elif key == "missing":
                from gui_qt.missing_episodes.tab import MissingEpisodesTab
                w = MissingEpisodesTab(ctx, host=self.host)
                self.host.missing_view = w
            else:
                raise ValueError(f"Pestaña desconocida: {key}")
            self._tab_widgets[key] = w
            self.tabs.addTab(w, title)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.status_lbl = QLabel("")
        self.statusBar().addWidget(self.status_lbl, 1)
        ctx.status.connect(self._set_status)
        ctx.favorites_changed.connect(self._tab_widgets["files"].refresh_marks)
        ctx.reservations_changed.connect(self._tab_widgets["files"].refresh_marks)

        self._pending_notifications = []
        self._notify_timer = QTimer(self)
        self._notify_timer.setSingleShot(True)
        self._notify_timer.setInterval(UPLOAD_NOTIFICATION_DEBOUNCE_MS)
        self._notify_timer.timeout.connect(self._flush_upload_notifications)
        self._ftp_space_gen = 0
        self._ftp_space_timer = QTimer(self)
        self._ftp_space_timer.setInterval(FTP_SPACE_PERIODIC_MS)
        self._ftp_space_timer.timeout.connect(self.refresh_ftp_space)

        self._setup_tray()
        self._startup()
        if start_minimized and self.tray is not None:
            self.hide()
        elif self.host.config_data.get("window_maximized", False):
            self.showMaximized()
        else:
            self.show()

    # ── Tamaño y posición de la ventana (se recuerdan entre sesiones) ──

    def _apply_window_state(self):
        """"window_geometry" es "WxH+X+Y" (mismo formato que guardaba la
        versión Tk). Si cae fuera de todas las pantallas (un monitor que ya
        no está), se queda el tamaño por defecto."""
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$",
                     str(self.host.config_data.get("window_geometry", "") or ""))
        if not m:
            return
        w, h, x, y = (int(v) for v in m.groups())
        if QGuiApplication.screenAt(QPoint(x + 40, y + 20)) is None:
            return
        self.setGeometry(QRect(x, y, max(w, 800), max(h, 500)))

    def _save_window_state(self):
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        cfg = self.host.config_data
        cfg.set("window_geometry", f"{g.width()}x{g.height()}+{g.x()}+{g.y()}")
        cfg.set("window_maximized", self.isMaximized())

    # ── Cabecera ──

    def _build_header(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 4)
        self.ftp_space_lbl = QLabel("")
        self.ftp_space_lbl.setToolTip("Espacio libre en cada disco del servidor (se actualiza cada 5 min y tras subir)")
        row.addWidget(self.ftp_space_lbl)
        row.addStretch(1)
        self.auto_btn = QPushButton("⚡ Auto")
        self.auto_btn.setToolTip("Modo automático: vigila la carpeta configurada, identifica, renombra y "
                                 "sube lo que vaya apareciendo.")
        self.auto_btn.clicked.connect(self.toggle_auto)
        row.addWidget(self.auto_btn)
        return row

    def _update_auto_btn(self):
        running = self.host.watcher_running()
        self.auto_btn.setText("⏹ Detener" if running else "⚡ Auto")
        self.auto_btn.setProperty("danger", running)
        self.auto_btn.style().unpolish(self.auto_btn)
        self.auto_btn.style().polish(self.auto_btn)

    def toggle_auto(self):
        if self.host.watcher_running():
            self.host.stop_watcher()
        else:
            self.host.start_watcher()
        self._update_auto_btn()

    # ── Arranque (lo equivalente de App.__init__ + sus after(...)) ──

    def _startup(self):
        host = self.host
        try:
            host._load_session()
        except Exception:
            _log.exception("Qt: no se pudo cargar la sesión")
        try:
            host._cleanup_stale_uploading_marks()
        except Exception:
            _log.exception("Qt: limpieza de marcas de subida a medias")
        host.load_genres_async()
        self._tab_widgets["files"].refresh_table()
        QTimer.singleShot(3000, self.refresh_ftp_space)
        self._ftp_space_timer.start()
        QTimer.singleShot(2300, self.ctx.sync_missing_episodes)
        QTimer.singleShot(2400, self.host._sync_missing_movies_from_ftp)
        QTimer.singleShot(400, self._restore_auto_watcher)
        # Procesos de fondo, mismos retardos que la versión Tk: autocompletado
        # de series (pasada cada 30 min) y solicitudes de la web.
        QTimer.singleShot(600, host._start_missing_ep_auto_worker)
        QTimer.singleShot(8000, host._start_download_requests_worker)
        # Sincronización de visionado programada (hilo de fondo, como en Tk)
        host._start_watch_sync_scheduler()
        # Traslados únicos tras el cambio de nombre, configuración compartida
        # del servidor y aviso de versión nueva: mismos retardos que en Tk.
        QTimer.singleShot(300, host._migrate_shared_data_folder)
        QTimer.singleShot(400, host._migrate_autostart_identity)
        QTimer.singleShot(2200, host._sync_server_config_from_ftp)
        QTimer.singleShot(3500, host._check_for_updates_at_startup)
        # Escaneo de episodios que faltan en segundo plano si toca (cada hora
        # se comprueba; de verdad se repite cada 12 h).
        self._bg_scan_timer = QTimer(self)
        self._bg_scan_timer.setInterval(3_600_000)
        self._bg_scan_timer.timeout.connect(self._maybe_background_missing_scan)
        self._bg_scan_timer.start()
        QTimer.singleShot(60_000, self._maybe_background_missing_scan)
        # Vigilancia del vigilante: si el hilo muriera, el botón no debe mentir.
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(8000)
        self._watch_timer.timeout.connect(self._update_auto_btn)
        self._watch_timer.start()

    def _maybe_background_missing_scan(self):
        """Equivalente de App._maybe_background_missing_scan: si hace más de
        12 h del último escaneo (y no hay subida ni escaneo en marcha), lanza
        uno con la pestaña Episodios, que ya sabe mostrar el progreso."""
        host = self.host
        cfg = host.config_data
        if not cfg.get("jellyfin_enabled") and not cfg.get("plex_enabled"):
            return
        tab = self._tab_widgets["missing"]
        if host._upload_running or getattr(tab, "_scanning", False):
            return
        from core.missing_episodes_cache import load_cache
        last_ts = (load_cache().get("_meta") or {}).get("last_scan_ts", 0)
        if time.time() - last_ts < 12 * 3600:
            return
        _log.info("Qt: escaneo de episodios que faltan en segundo plano")
        tab.start_scan()

    def on_settings_saved(self):
        """Tras "Guardar configuración": lo que otras pestañas leen al
        construirse (Plex/Jellyfin activos, espacio libre...)."""
        info = self._tab_widgets.get("info")
        ws = (info.pages.get("watch_sync") if info is not None and hasattr(info, "pages")
              else None)
        if ws is not None and hasattr(ws, "on_shown"):
            ws.on_shown()
        self.refresh_ftp_space()

    def _restore_auto_watcher(self):
        cfg = self.host.config_data
        if cfg.get("auto_watcher_running", False) and cfg.get("watch_folder", "").strip():
            self.host.start_watcher()
        self._update_auto_btn()

    # ── Espacio libre ──

    def refresh_ftp_space(self):
        host = self.host
        if not host.config_data.get("ftp_host", ""):
            return
        disks = host._disks_from_categories()
        if not disks:
            self.ftp_space_lbl.setText("")
            return
        self._ftp_space_gen += 1
        gen = self._ftp_space_gen

        def worker():
            ftp = self.ctx.connect_ftp()
            if ftp is None:
                return
            parts, any_low, free_by_disk = [], False, {}
            try:
                for disk_name, root in disks.items():
                    try:
                        free = host._get_free_space_with_jellyfin_fallback(ftp, root)
                    except Exception:
                        continue
                    if free is None:
                        continue
                    free_by_disk[disk_name] = free
                    parts.append(f"{disk_name.capitalize()}-{host._fmt_free_space(free)}")
                    any_low = any_low or free <= 1024 ** 3
            finally:
                ftp.disconnect()
            if not parts:
                return
            text = "Espacio disponible: " + " · ".join(parts)
            color = WARNING_COLOR if any_low else SUCCESS_COLOR

            def apply():
                if gen == self._ftp_space_gen:
                    self.ftp_space_lbl.setText(text)
                    self.ftp_space_lbl.setStyleSheet(f"color: {color};")
                    host._shared_free_space_by_disk = free_by_disk
                    if host._stats_visible:
                        host._stats_view.refresh_from_cache()
            ui(apply)
        run_in_thread(worker)

    # ── Barra de estado ──

    def _set_status(self, text: str, color: str):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {color};")

    def _on_tab_changed(self, i: int):
        prev = getattr(self, "_current_tab", None)
        if prev is not None and hasattr(prev, "confirm_leave") and self.tabs.widget(i) is not prev:
            if not prev.confirm_leave():
                # Seguir en Configuración: volver a marcarla sin reentrar aquí.
                self.tabs.blockSignals(True)
                self.tabs.setCurrentWidget(prev)
                self.tabs.blockSignals(False)
                return
        if prev is not None and hasattr(prev, "on_hidden"):
            prev.on_hidden()
        w = self.tabs.widget(i)
        self._current_tab = w
        if hasattr(w, "on_shown"):
            w.on_shown()

    def current_view_key(self) -> str:
        w = self.tabs.currentWidget()
        for key, tab in self._tab_widgets.items():
            if tab is w:
                if key == "info" and hasattr(tab, "current_key"):
                    return tab.current_key()
                return VIEW_KEYS.get(key, key)
        return "files"

    def amule_search(self, query: str, expected_year=None, is_movie=None):
        """Abre Descargas y busca *query* en aMule (🔍 de otras pestañas)."""
        tab = self._tab_widgets["downloads"]
        self.tabs.setCurrentWidget(tab)
        tab.search(query, expected_year=expected_year, is_movie=is_movie)

    # ── Notificaciones ──

    def queue_upload_notification(self, name: str):
        """Agrupa las subidas completadas: una sola notificación cuando la
        racha se queda quieta 8 s (ver App._queue_upload_notification)."""
        self._pending_notifications.append(name)
        self._notify_timer.start()

    def _flush_upload_notifications(self):
        pending, self._pending_notifications = self._pending_notifications, []
        if not pending:
            return
        if len(pending) == 1:
            self.notify("aIBechos — Subida completada", pending[0])
            return
        preview = ", ".join(pending[:3])
        if len(pending) > 3:
            preview += f" y {len(pending) - 3} más"
        self.notify(f"aIBechos — {len(pending)} subidas completadas", preview)

    def notify(self, title: str, message: str):
        if self.tray is not None and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, message, self._icon, 5000)
            return
        # Sin bandeja (p.ej. escritorios Linux sin área de notificación):
        # aviso nativo del sistema, como hacía la versión anterior.
        import subprocess
        try:
            if sys.platform == "darwin":
                esc = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.Popen(["osascript", "-e",
                                  f'display notification "{esc(message)}" with title "{esc(title)}"'])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["notify-send", title, message])
        except OSError:
            pass

    # ── Bandeja ──

    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self._icon, self)
        tray.setToolTip("aIBechos")
        menu = QMenu()
        show = QAction("Mostrar", menu)
        show.triggered.connect(self._show_from_tray)
        quit_ = QAction("Salir", menu)
        quit_.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addSeparator()
        menu.addAction(quit_)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self._show_from_tray()
                               if reason == QSystemTrayIcon.Trigger else None)
        tray.show()
        self.tray = tray

    def _show_from_tray(self):
        if self.host.config_data.get("window_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    # ── Cierre ──

    def _confirm_quit_with_uploads(self) -> bool:
        if not self.host._upload_running:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Subida en curso")
        box.setIcon(QMessageBox.Warning)
        box.setText("Hay una subida FTP en curso.")
        box.setInformativeText("Si cierras ahora se cancelará; se reanudará la próxima vez que subas el archivo.")
        ok = box.addButton("Cerrar y cancelar la subida", QMessageBox.DestructiveRole)
        box.addButton("Seguir subiendo", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is ok

    def quit_app(self, confirmed: bool = False):
        if self._quitting:
            return
        if not confirmed and not self._confirm_quit_with_uploads():
            return
        self._quitting = True
        try:
            self._save_window_state()   # host.shutdown() guarda la config
        except Exception:
            _log.exception("Qt: no se pudo guardar el estado de la ventana")
        try:
            self.host.shutdown()
        except Exception:
            _log.exception("Qt: fallo al cerrar")
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        # "Minimizar a la bandeja al cerrar" (Configuración → Cliente →
        # General → Modo Automático): el aspa esconde la ventana y la app
        # sigue en segundo plano (las subidas/vigilancia continúan). Sin
        # bandeja disponible se sale normalmente. "Salir" del menú de la
        # bandeja llama a quit_app() directamente, sin pasar por aquí.
        if (self.host.config_data.get("close_to_tray", False)
                and not self._quitting and self.tray is not None):
            event.ignore()
            self.hide()
            self.notify("aIBechos", "La app sigue ejecutándose en la bandeja del sistema.")
            return
        self.quit_app()
        if self._quitting:
            event.accept()
        else:
            event.ignore()
