"""
"La App sin interfaz": hereda toda la lógica de la app (las mixins
core/app_*.py y core/missing_ep_scan.py) y aporta lo que esa lógica espera de
su anfitrión:

- el estado (lista de archivos, cola de subida, cachés de carpetas FTP,
  clientes de TMDB/libros/cómics...);
- after(ms, fn) y _after_from_worker(), sobre gui_qt.bridge (Qt no es seguro
  entre hilos);
- los "ganchos" de interfaz (_update_row, _refresh_table, _ftp_row_set,
  diálogos...), que reenvía a la vista registrada (la pestaña Archivos) o a
  otras pestañas. Sin vista registrada, los ganchos no hacen nada: así la
  lógica (p.ej. AutoWatcher) funciona aunque la pestaña no se haya creado.

Una sola instancia por proceso, creada por la ventana principal.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QTimer

from core import missing_ep_rows as mer
from core.anilist_client import AniListClient
from core.app_auto_complete import AutoCompleteMixin
from core.app_download_requests import DownloadRequestsMixin
from core.app_downloads_core import DownloadsCoreMixin
from core.app_movies_core import MoviesCoreMixin
from core.app_history_core import HistoryCoreMixin
from core.app_cleanup_core import CleanupCoreMixin
from core.app_watch_sync_core import WatchSyncCoreMixin
from core.app_settings_core import SettingsCoreMixin
from core.app_files_core import FilesCoreMixin
from core.app_shared_sync import SharedSyncMixin
from core.applog import get_logger
from core.book_client import GoogleBooksClient
from core.comicvine_client import ComicVineClient
from core.kitsu_client import KitsuClient
from core.missing_ep_scan import MissingEpScanMixin
from core.mangadex_client import MangaDexClient
from core.openlibrary_client import OpenLibraryClient
from core.upload_slots import UploadSlotManager
from gui_qt.bridge import ui

_log = get_logger("aIBechos.qt", "app.log")


class _NullView:
    """Vista vacía: cualquier gancho de interfaz es un no-op."""

    def __getattr__(self, name):
        return lambda *a, **k: None


class QtAppCore(FilesCoreMixin, SharedSyncMixin, MissingEpScanMixin, AutoCompleteMixin,
                DownloadRequestsMixin, DownloadsCoreMixin, MoviesCoreMixin, HistoryCoreMixin,
                CleanupCoreMixin, WatchSyncCoreMixin, SettingsCoreMixin):
    def __init__(self, ctx):
        self.ctx = ctx
        self.config_data = ctx.config
        self.tmdb = ctx.tmdb
        cfg = self.config_data
        self.book_client = GoogleBooksClient(cfg.get("google_books_api_key", ""))
        self.comicvine = ComicVineClient(cfg.get("comicvine_api_key", ""))
        self.openlibrary_client = OpenLibraryClient()
        self.mangadex_client = MangaDexClient()
        self.anilist_client = AniListClient()
        self.kitsu_client = KitsuClient()
        # Conexión de control compartida con AutoWatcher (como self.ftp en Tk).
        self.ftp = self._new_ftp_client()
        self._ftp_cmd_lock = threading.Lock()

        # Lista de archivos y selección
        self.files = []
        self._session_load_failed = False
        self._selected_entry = None
        self._multi_selected = set()
        self._files_sort_key, self._files_sort_asc = None, True
        saved = (cfg.get("table_sort", {}) or {}).get("archivos")
        if isinstance(saved, dict) and "key" in saved:
            self._files_sort_key, self._files_sort_asc = saved.get("key"), bool(saved.get("asc", True))
        self._results_kind = "tmdb"
        self._search_provider_override = "tmdb"

        # Cola de subida (mismos campos que App.__init__)
        self._upload_queue = []
        self._upload_cancel = threading.Event()
        self._upload_skip = threading.Event()
        self._upload_running = False
        self._upload_current_idx = -1
        self._upload_current_remote = ""
        self._upload_overwrite_all = False
        self._upload_skip_all = False
        self._upload_duplicate_ignore_all = False
        self._upload_stale_choice = None
        self._rename_overwrite_all = False
        self._upload_slot_of = {}
        self._upload_skip_events = []
        self._upload_confirm_lock = threading.Lock()
        self._upload_slots = UploadSlotManager(cfg)

        self._series_folder_cache = {}
        self._ftp_dir_cache = ctx._ftp_dir_cache   # la misma caché que el resto de la interfaz Qt
        self._series_folder_lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._history_dirty = False
        self._stats_push_lock = threading.Lock()
        self._stats_visible = False
        self._stats_view = None
        self._missing_ep_scanning = False
        self._cleanup_raw_items = []
        self._genres_cache = {"tv": [], "movie": []}
        self._watcher = None

        # Autocompletado (⚡) y solicitudes de la web
        from core.auto_series import load_local_cache as _load_auto_series
        self._amule_ec_lock = ctx.ec_lock
        self._auto_series_shared = _load_auto_series()
        self._last_auto_series_sync_ts = 0.0
        self._missing_ep_auto_busy = set()
        self._missing_ep_auto_worker = None
        self._download_requests_carry = {}
        self._download_requests_worker = None
        self._fallback_missing_rows = None

        # Recomendado
        self._movies_results = []
        self._movies_selected_tmdb_id = None
        self._movies_visible = False
        self._last_missing_movies_sync_ts = 0.0

        # Historial, estadísticas compartidas, Liberar espacio y Protegidos
        self._last_activity_sync_ts = 0.0
        self._last_category_stats_sync_ts = 0.0
        self._last_category_upload_stats_sync_ts = 0.0
        self._last_cleanup_candidates_sync_ts = 0.0
        self._last_deletion_stats_sync_ts = 0.0
        self._last_reservations_sync_ts = 0.0
        self._last_slim_stats_sync_ts = 0.0
        self._last_upload_stats_sync_ts = 0.0
        self._last_web_requests_sync_ts = 0.0
        from core.deletion_stats import load_local_cache as _l5
        self._shared_deletion_stats = _l5()
        self._category_stats_bootstrap_done = False
        self._shared_activity_history = []
        self._web_requests_rows = []
        self._web_requests_error = ""
        self._history_all = []
        self._history_visible = False
        self._cleanup_visible = False
        self._protected_visible = False
        self._cleanup_filtered_items = []
        self._cleanup_flat_rows = []
        self._cleanup_expanded = set()
        self._cleanup_expanded_seasons = set()
        self._cleanup_slim_loading = set()
        self._cleanup_rescanning = set()
        self._cleanup_deleting_paths = set()
        self._cleanup_trees = {}
        self._cleanup_trees_ts = 0.0
        self._cleanup_ep_cache = {}
        self._cleanup_ep_cache_ttl = 600
        self._cleanup_last_scan_ts = None
        self._cleanup_scanned_by = ""
        self._cleanup_selected_item = None
        self._shared_free_space_by_disk = {}

        from core.upload_stats import load_local_cache as _l1
        from core.slim_stats import load_local_cache as _l2
        from core.category_upload_stats import load_local_cache as _l3
        from core.category_stats import load_local_cache as _l4
        self._shared_upload_stats = _l1()
        self._shared_slim_stats = _l2()
        self._shared_category_upload_stats = _l3()
        self._shared_category_stats = _l4()

        self.view = _NullView()            # pestaña Archivos (FilesTab), ver set_view
        self.missing_view = _NullView()    # pestaña Episodios que faltan
        self.movies_view = _NullView()     # pestaña Recomendado
        self.history_view = _NullView()    # pestaña Historial
        self.cleanup_view = _NullView()    # pestaña Liberar espacio
        self.protected_view = _NullView()  # pestaña Protegidos
        self.watch_sync_view = _NullView()  # pestaña Sincronizar visionado
        self.window = None                 # ventana principal (diálogos, notificaciones)
        self.settings_view = _NullView()
        # Listas de proveedores/filtros/pesos al scoring de aMule (como Tk al arrancar).
        self._apply_provider_lists(self.config_data)

    # ── Estado de "Episodios que faltan" (vive en la pestaña Qt) ──

    @property
    def _missing_ep_results(self) -> list:
        rows = getattr(self.missing_view, "_results", None)
        if isinstance(rows, list):
            return rows
        if self._fallback_missing_rows is None:
            from core.missing_episodes_cache import load_cache
            self._fallback_missing_rows = mer.rows_from_cache(load_cache(), complete=False)
        return self._fallback_missing_rows

    @property
    def _spanish_dub_cache(self) -> dict:
        cache = getattr(self.missing_view, "_dub_cache", None)
        if isinstance(cache, dict):
            return cache
        from core.spanish_dub_cache import load_cache
        return load_cache() or {}

    @property
    def _missing_ep_complete_rows(self):
        return getattr(self.missing_view, "_complete_rows", None)

    @_missing_ep_complete_rows.setter
    def _missing_ep_complete_rows(self, value):
        if not isinstance(self.missing_view, _NullView):
            self.missing_view._complete_rows = value

    # Reservas: una sola copia, la del contexto (la que pintan las pestañas)
    @property
    def _reservations(self) -> dict:
        return self.ctx.reservations

    @_reservations.setter
    def _reservations(self, value: dict):
        changed = value != self.ctx.reservations
        self.ctx.reservations = value
        if changed:
            ui(self.ctx.reservations_changed.emit)

    def _hide_no_dub_enabled(self) -> bool:
        return bool(self.config_data.get("missing_ep_hide_no_dub", False))

    def _dub_cutoff_for_series(self, tmdb_id, ai_verdict=None):
        return mer.dub_cutoff_for_series(self._spanish_dub_cache, tmdb_id, ai_verdict)

    def _load_complete_series_from_cache(self) -> list:
        from core.missing_episodes_cache import load_cache
        return mer.rows_from_cache(load_cache(), complete=True)

    def _refresh_missing_ep_auto_button(self, tmdb_id, active=None):
        self.missing_view.refresh_auto(tmdb_id)
        self.view.refresh_marks()

    def _forget_auto_countdown(self, tmdb_id):
        pass   # la cuenta atrás de Qt se calcula al pintar, no hay etiqueta que borrar

    def _render_missing_episodes_table(self, reset_page: bool = True):
        self.missing_view.render()

    # Escaneo (MissingEpScanMixin), usado por "Forzar búsqueda"/autocompletado
    def _push_missing_episodes_to_ftp(self):
        self.ctx.push_missing_episodes()

    def _scan_notify(self, text, color):
        self.ctx.set_status(text, color)

    def _refresh_server_audio(self, source, server_id, tmdb_id):
        svc = getattr(self.missing_view, "scan", None)
        if svc is not None:
            svc._refresh_server_audio(source, server_id, tmdb_id)

    # ── Hilos ──

    def after(self, ms, fn, *args):
        """Equivalente a Tk.after: *fn* se ejecuta en el hilo de la
        interfaz, pasados *ms* milisegundos. Seguro desde cualquier hilo."""
        call = (lambda: fn(*args)) if args else fn
        if ms <= 0:
            ui(call)
        else:
            ui(lambda: QTimer.singleShot(int(ms), call))

    def _after_from_worker(self, fn, critical: bool = False, timeout_s: float = 5.0) -> bool:
        """En Tk reintenta si el bucle principal aún no corre; en Qt la cola
        de eventos acepta llamadas desde el arranque."""
        ui(fn)
        return True

    # ── Conexiones y servicios compartidos con el contexto ──

    def _new_ftp_client(self):
        return self.ctx.new_ftp_client()

    def _set_status(self, text, color=None):
        self.ctx.set_status(text or "", color or "#95a5a6")

    def _is_favorite(self, media_type, tmdb_id):
        return self.ctx.is_favorite(media_type, tmdb_id)

    def _is_reserved(self, media_type, tmdb_id):
        return self.ctx.is_reserved(media_type, tmdb_id)

    def _toggle_favorite(self, media_type, tmdb_id, name, on_done=None):
        self.ctx.toggle_favorite(media_type, tmdb_id, name)
        if on_done:
            on_done()

    def _toggle_reservation(self, media_type, tmdb_id, name, size_bytes, on_done=None):
        self.ctx.toggle_reservation(self.window, media_type, tmdb_id, name, size_bytes)
        if on_done:
            on_done()

    def _genre_names_for(self, media_type: str, genre_ids: list) -> list:
        if not genre_ids or media_type == "libro":
            return []
        cache = (self._genres_cache or {}).get("movie" if media_type != "tv" else "tv") or []
        by_id = {g.get("id"): g.get("name", "") for g in cache}
        return [by_id.get(gid, "") for gid in genre_ids if by_id.get(gid)]

    def load_genres_async(self):
        def worker():
            try:
                self._genres_cache = {"tv": self.tmdb.get_genres("tv"),
                                      "movie": self.tmdb.get_genres("movie")}
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    # ── Ganchos de interfaz: pestaña Archivos ──

    def _refresh_table(self):
        self.view.refresh_table()

    def _update_row(self, entry):
        self.view.update_row(entry)

    def _update_detail(self, entry):
        self.view.update_detail(entry)

    def _clear_detail(self):
        self._selected_entry = None
        self._multi_selected.clear()
        self.view.clear_detail()

    def _reset_search_panel(self, entry=None):
        self.view.reset_search_panel(entry)

    def _update_assign_button_label(self):
        self.view.update_assign_button_label()

    def _update_status_bar(self):
        self.view.update_status_bar()

    def _update_status_bar_debounced(self):
        self.view.update_status_bar()

    def _apply_persisted_files_sort(self):
        if self._files_sort_key:
            self._sort_files_by_current_key()
        self._refresh_table()

    def _prompt_same_series_if_needed(self, book_comic_entries, video_entries):
        if len(book_comic_entries) < 2 or isinstance(self.view, _NullView):
            self.after(50, self._search_new_entries, book_comic_entries + video_entries)
            return
        self.view.prompt_same_series(book_comic_entries, video_entries)

    def _ftp_row_set(self, entry, status_text, progress, speed):
        """Mismo contrato que en Tk: fija estado de subida/progreso/velocidad
        de la fila (seguro desde un hilo)."""
        entry.ftp_status = status_text
        entry.ftp_progress = progress
        entry.ftp_speed = speed
        self.after(0, lambda e=entry: self.view.update_row(e))

    def _ftp_row_live(self, entry, progress, speed, *args, **kwargs):
        entry.ftp_progress = progress
        entry.ftp_speed = speed
        self.view.update_progress(entry)

    def _ftp_row_uploading(self, entry, *args, **kwargs):
        self.after(0, lambda e=entry: self.view.update_row(e))

    def _ftp_row_done(self, entry, *args, **kwargs):
        self.after(0, lambda e=entry: self.view.update_row(e))

    def _refresh_ftp_columns(self):
        self.view.refresh_table()

    def _restore_ftp_row_buttons(self):
        self.view.refresh_table()

    def _refresh_ftp_space(self):
        if self.window is not None:
            self.window.refresh_ftp_space()

    # ── Diálogos (en el hilo de la interfaz; devuelven objeto con .result) ──

    def _make_overwrite_dialog(self, *args, **kwargs):
        from gui_qt.files.dialogs import OverwriteDialog
        return OverwriteDialog.ask(self.window, *args, **kwargs)

    def _make_stale_upload_dialog(self, *args, **kwargs):
        from gui_qt.files.dialogs import StaleUploadDialog
        return StaleUploadDialog.ask(self.window, *args, **kwargs)

    def _make_series_match_dialog(self, *args, **kwargs):
        from gui_qt.files.dialogs import SeriesMatchDialog
        return SeriesMatchDialog.ask(self.window, *args, **kwargs)

    def _warn_dialog(self, title: str, message: str):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self.window, title, message)

    # ── Ganchos de core/app_settings_core.py ──

    def _error_dialog(self, title: str, message: str):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(self.window, title, message)

    def _ask_yes_no(self, title: str, message: str) -> bool:
        from PySide6.QtWidgets import QMessageBox
        return QMessageBox.question(self.window, title, message) == QMessageBox.Yes

    def _ask_save_path(self, title: str, initialfile: str):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self.window, title, initialfile, "JSON (*.json)")
        return path

    def _ask_open_path(self, title: str):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self.window, title, "", "JSON (*.json)")
        return path

    def _make_rename_reservations_dialog(self, old_name: str, new_name: str):
        from gui_qt.settings.dialogs import RenameReservationsDialog
        return RenameReservationsDialog.ask(self.window, old_name, new_name)

    def _on_server_config_applied(self):
        self.settings_view.reload_from_config()

    def _on_config_imported(self):
        self.settings_view.reload_from_config()

    def _invalidate_missing_ep_detail_frames(self):
        """Tras guardar Ajustes (enlaces personalizados, etc.): la vista Qt
        no cachea fichas construidas, basta con repintar."""
        if self.missing_view is not None:
            try:
                self.missing_view.render()
            except Exception:
                pass

    def _show_update_dialog(self, tag: str, html_url: str):
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self.window)
        box.setWindowTitle("Actualización disponible")
        box.setText(f"Hay una nueva actualización {tag}.")
        box.setInformativeText("¿Quieres descargarla?")
        open_btn = box.addButton("Ir a la release", QMessageBox.AcceptRole)
        skip_btn = box.addButton("Saltar esta versión", QMessageBox.DestructiveRole)
        box.addButton("Ahora no", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            import webbrowser
            webbrowser.open(html_url)
        elif box.clickedButton() is skip_btn:
            self.config_data.set("skipped_update_version", tag)
            self.config_data.save()

    def _make_confirm_dialog(self, title, heading, body="", confirm_text="Aceptar",
                             cancel_text="Cancelar", confirm_color=None, **_kw):
        from gui_qt.dialogs import confirm
        ok = confirm(self.window, title, heading, body, confirm_text=confirm_text,
                     cancel_text=cancel_text, danger=bool(confirm_color))
        return type("_Result", (), {"result": ok})()

    def _make_remove_entry_dialog(self, *args, **kwargs):
        from gui_qt.files.dialogs import RemoveEntryDialog
        return RemoveEntryDialog.ask(self.window, *args, **kwargs)

    def _make_confirm_removal_dialog(self, *args, **kwargs):
        from gui_qt.files.dialogs import ConfirmRemovalDialog
        return ConfirmRemovalDialog.ask(self.window, *args, **kwargs)

    # ── Ganchos: Historial, Liberar espacio, Protegidos ──

    def _current_view_key(self) -> str:
        return self.window.current_view_key() if self.window is not None else "files"

    def _open_custom_link(self, template, variables, background=False):
        self.ctx.open_custom_link(template, variables, background)

    def _search_missing_ep_on_amule(self, filename, series_name="", expected_year=None, is_movie=False):
        if self.window is not None:
            self.window.amule_search(filename, expected_year=expected_year, is_movie=is_movie)

    def _refresh_history_view(self):
        self.history_view.refresh()

    def _apply_web_requests_history(self, rows, error: str):
        if rows is not None:
            self._web_requests_rows = rows
        self._web_requests_error = error
        self.history_view.on_web_requests()

    def _apply_cleanup_filters(self, preserve_page: bool = False):
        self.cleanup_view.apply_filters()

    def _render_cleanup_page(self):
        self.cleanup_view.render()

    def _cleanup_slim_ratio(self) -> float:
        fn = getattr(self.cleanup_view, "slim_ratio", None)
        return fn() if callable(fn) else 2.0

    def _update_cleanup_delete_progress(self, current, total, name):
        self.cleanup_view.set_status(name)

    def _finish_delete_cleanup_item(self, item, ok: bool, msg: str):
        """Tras borrar una candidata (ver App._finish_delete_cleanup_item)."""
        from core.fmt import fmt_size
        self._cleanup_deleting_paths.discard(item.ftp_path)
        if ok:
            self._cleanup_raw_items = [it for it in self._cleanup_raw_items if it is not item]
            self._cleanup_filtered_items = [it for it in self._cleanup_filtered_items if it is not item]
            self._cleanup_flat_rows = [d for d in (self._cleanup_flat_rows or []) if d.get("item") is not item]
            self._set_status(f"Eliminado: {item.name} ({fmt_size(item.size_bytes)} liberados)", "#2ecc71")
            self._refresh_ftp_space()
            from core.cleanup_candidates_cache import save_cache
            try:
                save_cache(self._cleanup_raw_items, self._cleanup_last_scan_ts or time.time(),
                           self._cleanup_scanned_by)
            except Exception:
                _log.warning("Liberar espacio (Qt): no se pudo actualizar el caché tras borrar", exc_info=True)
            self._push_cleanup_deletion_to_ftp(item.ftp_path)
            if item.media_type == "tv" and item.tmdb_id is not None:
                self._remove_series_from_missing_episodes(item.tmdb_id)
        else:
            self._set_status(f"No se pudo eliminar {item.name}: {msg}", "#e74c3c")
        self.cleanup_view.after_delete()

    def _apply_synced_cleanup_candidates(self, payload: dict, force: bool = False, rerender: bool = True):
        """Lista de "Liberar espacio" compartida (ver la versión Tk): solo si
        es más reciente que la que hay, o tras un borrado propio (force)."""
        from core.cleanup_candidates_cache import save_cache
        if not force and (payload.get("last_scan_ts") or 0) <= (self._cleanup_last_scan_ts or 0):
            return
        self._cleanup_raw_items = payload["items"]
        self._cleanup_last_scan_ts = payload.get("last_scan_ts")
        self._cleanup_scanned_by = payload.get("scanned_by", "")
        try:
            save_cache(self._cleanup_raw_items, self._cleanup_last_scan_ts, self._cleanup_scanned_by)
        except Exception:
            _log.warning("Liberar espacio (Qt): no se pudo guardar el mirror local", exc_info=True)
        if self._cleanup_visible and rerender:
            self.cleanup_view.on_new_candidates()

    def _on_watch_sync_ran(self):
        self.watch_sync_view.on_scheduled_done()

    def _refresh_watch_sync_history_view(self):
        self.watch_sync_view.refresh_history()

    def _render_protected_table(self):
        self.protected_view.render()

    def _make_confirm_delete_dialog(self, *args, **kwargs):
        from gui_qt.files.dialogs import ConfirmDeleteDialog
        return ConfirmDeleteDialog.ask(self.window, *args, **kwargs)

    # ── Ganchos hacia otras pestañas ──

    def _remove_uploaded_episode_from_missing_list(self, media_info):
        self.missing_view.remove_uploaded_episode(media_info)

    # Recomendado (core/app_movies_core.py)
    def _render_movies_table(self, reset_page: bool = True):
        self.movies_view.render()

    def _update_movies_status_text(self):
        self.movies_view.update_status()

    def _refresh_movies_genre_filter_options(self):
        self.movies_view.refresh_genres()

    def _cancel_missing_episodes_scan(self):
        self.missing_view.cancel_scan()

    def _refresh_cleanup_item_after_replace(self, *args, **kwargs):
        pass   # "Liberar espacio" aún no está migrada

    def _apply_synced_activity_history(self, activity_list: list):
        self._shared_activity_history = activity_list
        self.history_view.on_activity_synced()

    def _queue_upload_notification(self, name: str, *args, **kwargs):
        if self.window is not None:
            self.after(0, lambda: self.window.queue_upload_notification(name))

    def _send_notification(self, title: str, message: str):
        if self.window is not None:
            self.after(0, lambda: self.window.notify(title, message))

    # ── Vigilante de carpeta (AutoWatcher) ──

    def watcher_running(self) -> bool:
        return bool(self._watcher and self._watcher.running)

    def start_watcher(self) -> bool:
        from core.auto_watcher import AutoWatcher
        folder = self.config_data.get("watch_folder", "").strip()
        if not folder:
            self._set_status("Configura la carpeta vigilada en ⚙ Configuración", "#f39c12")
            return False
        if self.watcher_running():
            return True
        self._watcher = AutoWatcher(
            folder, self.config_data, self.tmdb, self.ftp,
            self._on_auto_event, self._on_auto_file_event,
            upload_slots=self._upload_slots, ftp_lock=self._ftp_cmd_lock,
            ftp_factory=self._new_ftp_client,
            comicvine_client=self.comicvine, book_client=self.book_client,
            openlibrary_client=self.openlibrary_client)
        self._watcher.start()
        self._set_status(f"Vigilando: {folder}", "#2ecc71")
        self.config_data.set("auto_watcher_running", True)
        self.config_data.save()
        return True

    def stop_watcher(self, persist: bool = True) -> None:
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None
        if persist:
            self._set_status("Modo automático detenido")
            self.config_data.set("auto_watcher_running", False)
            self.config_data.save()

    # ── Cierre ──

    def shutdown(self) -> None:
        """Lo mismo que App._force_quit menos la ventana: parar el vigilante
        (sin cambiar su ajuste persistido), cancelar subidas, cerrar la
        conexión y guardar la sesión."""
        self.stop_watcher(persist=False)
        stop = getattr(self, "_watch_sync_scheduler_stop", None)
        if stop is not None:
            stop.set()
        self._upload_cancel.set()
        try:
            with self._ftp_cmd_lock:
                self.ftp.disconnect()
        except Exception:
            pass
        try:
            self.config_data.save()
        except Exception:
            _log.warning("Qt: no se pudo guardar la configuración al cerrar", exc_info=True)
        try:
            self._save_session()
        except Exception:
            _log.warning("Qt: no se pudo guardar la sesión al cerrar", exc_info=True)
        time.sleep(0)
