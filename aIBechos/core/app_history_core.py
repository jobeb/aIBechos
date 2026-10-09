"""
Historial (subidas y borrados), estadísticas compartidas (ranking de
subidas/borrados/adelgazamientos, por categoría) y reservas compartidas por
FTP, sin interfaz. Mixin que hereda QtAppCore
(gui_qt/core_host.py).
"""

import json
import threading
import time as _time
from pathlib import Path

from core import shared_data
from core.ftp_client import sizes_by_top_level_folder
from core.appdirs import app_data_dir as _appdata_dir
from core.applog import get_logger
from core.status_colors import ERROR_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class HistoryCoreMixin:
    # Constantes de clase (antes en App, gui/app.py)
    _ACTIVITY_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_activity_history_from_ftp
    _CATEGORY_STATS_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_category_stats_from_ftp
    _CATEGORY_UPLOAD_STATS_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_category_upload_stats_from_ftp
    _DELETION_STATS_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_deletion_stats_from_ftp
    _RESERVATIONS_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_reservations_from_ftp
    _SLIM_STATS_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_slim_stats_from_ftp
    _UPLOAD_STATS_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_upload_stats_from_ftp
    _WEB_REQUESTS_SYNC_MIN_INTERVAL = 15   # segundos, ver _sync_web_requests_history


    def _reservation_attribution(self, media_type: str, tmdb_id: int) -> str:
        """Sufijo de tooltip ("\nReservado por X el <fecha>. Solo X puede
        soltarla.") con lo que conste; "" si no consta nada."""
        from core.reservations import reserved_by, reserved_at
        owner = (reserved_by(self._reservations, media_type, tmdb_id) or "").strip()
        date = self._fmt_mark_ts(reserved_at(self._reservations, media_type, tmdb_id))
        if not owner and not date:
            return ""
        who = f"Reservado por {owner}" if owner else "Reservado"
        when = f" el {date}" if date else ""
        only = f" Solo {owner} puede soltarla." if owner else ""
        return f"\n{who}{when}.{only}"

    def _reservations_remote_path(self) -> str:
        """Ruta remota del JSON de reservas -- archivo propio dentro de la
        carpeta compartida (ver _shared_data_path), no un campo de ajustes
        aparte: reservas, favoritos y configuración de servidor comparten
        la misma conexión/carpeta FTP, así que basta con una única ruta
        (de carpeta) configurada por el usuario."""
        return self._shared_data_path(shared_data.filename("reservas"))

    def _deletion_stats_remote_path(self) -> str:
        """Ruta remota del ranking de borrados por usuario (ver
        core/deletion_stats.py, pestaña Estadísticas) -- archivo propio
        dentro de la misma carpeta compartida, mismo motivo que
        _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("estadisticas_borrados"))

    def _reservation_owner(self, media_type: str, tmdb_id: int):
        from core.reservations import reserved_by
        return reserved_by(self._reservations, media_type, tmdb_id)

    def _reservation_quota_bytes(self) -> int:
        """Cuota de reservas configurada, en bytes -- configuración de
        SERVIDOR (ver core/server_config.py), 100GB por defecto si nunca
        se ha sincronizado/publicado nada (config.py::DEFAULTS
        ["reservation_quota_gb"])."""
        return int(self.config_data.get("reservation_quota_gb", 100)) * 1024 ** 3

    def _push_reservations_to_ftp(self, transform, on_done=None):
        """Sube el cambio de *transform* al FTP en segundo plano --
        descarga el JSON remoto fresco, le aplica *transform* (no lo que
        hubiera en memoria) y lo vuelve a subir, para minimizar la carrera
        si otro cliente conectado al mismo servidor cambió algo distinto
        mientras tanto. *transform* recibe el dict remoto y devuelve el
        dict nuevo -- mismo callable tanto para un cambio de una sola
        clave (_toggle_reservation) como para uno que toca varias a la vez
        (_transfer_reservations/_unprotect_all_reservations). Si no hay
        ruta configurada, el cambio se queda en el mirror local nada más."""
        remote_path = self._reservations_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                try:
                    remote_data = _json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    remote_data = {}
                if not isinstance(remote_data, dict):
                    remote_data = {}
                merged = transform(remote_data)
                data = _json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                if up_ok:
                    self.after(0, lambda: self._apply_synced_reservations(merged, on_done))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _transfer_reservations(self, old_owner: str, new_owner: str):
        """"Traspasarlas al nombre nuevo" en _RenameReservationsDialog --
        misma entrada, mismo tamaño, solo cambia reserved_by."""
        from core.reservations import transfer_reservations, save_local_cache as _save_reservations_cache
        self._reservations = transfer_reservations(self._reservations, old_owner, new_owner)
        _save_reservations_cache(self._reservations)
        self._push_reservations_to_ftp(lambda data: transfer_reservations(data, old_owner, new_owner))

    def _unprotect_all_reservations(self, owner: str):
        """"Desproteger todas" en _RenameReservationsDialog -- la otra
        opción al cambiar de nombre, empezar de nuevo en vez de traspasar."""
        from core.reservations import remove_all_by_owner, save_local_cache as _save_reservations_cache
        self._reservations = remove_all_by_owner(self._reservations, owner)
        _save_reservations_cache(self._reservations)
        self._push_reservations_to_ftp(lambda data: remove_all_by_owner(data, owner))

    def _apply_synced_reservations(self, merged: dict, on_done=None):
        from core.reservations import save_local_cache as _save_reservations_cache
        changed = merged != self._reservations
        self._reservations = merged
        _save_reservations_cache(self._reservations)
        if on_done:
            on_done()
        elif not changed:
            pass   # ver _apply_synced_favorites: no redibujar si no cambió nada de verdad
        elif self._cleanup_visible:
            self._apply_cleanup_filters()
        elif self._protected_visible:
            self._render_protected_table()
        elif self._current_view_key() == "files":
            self._refresh_table()

    def _sync_reservations_from_ftp(self):
        """Refresca el mirror local desde el FTP en segundo plano -- mismo
        motivo y patrón que _sync_favorites_from_ftp, incluido el freno de
        _RESERVATIONS_SYNC_MIN_INTERVAL. Se llama al abrir Archivos (los
        candados de las filas ya subidas), Liberar espacio (protección +
        cuota) y Protegidos (gestión)."""
        now = _time.time()
        if now - self._last_reservations_sync_ts < self._RESERVATIONS_SYNC_MIN_INTERVAL:
            return
        self._last_reservations_sync_ts = now

        remote_path = self._reservations_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    return
                try:
                    remote_data = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_data, dict):
                    return
                self.after(0, lambda: self._apply_synced_reservations(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _sync_activity_history_from_ftp(self):
        """Refresca el mirror en memoria del historial de actividad
        compartido desde el FTP en segundo plano -- mismo patrón y mismo
        freno que _sync_reservations_from_ftp. Se llama al entrar en
        Historial; solo importa de verdad si "Ver todo el servidor" está
        activo, pero se sincroniza siempre que se visita la pestaña para
        que el interruptor no tenga que esperar a una conexión nueva al
        encenderlo."""
        now = _time.time()
        if now - self._last_activity_sync_ts < self._ACTIVITY_SYNC_MIN_INTERVAL:
            return
        self._last_activity_sync_ts = now

        remote_path = self._activity_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    return
                try:
                    remote_list = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_list, list):
                    return
                self.after(0, lambda: self._apply_synced_activity_history(remote_list))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _sync_upload_stats_from_ftp(self):
        """Refresca el mirror local del ranking de subidas desde el FTP en
        segundo plano -- mismo patrón y mismo freno que
        _sync_activity_history_from_ftp. Se llama al entrar en
        Estadísticas."""
        now = _time.time()
        if now - self._last_upload_stats_sync_ts < self._UPLOAD_STATS_SYNC_MIN_INTERVAL:
            return
        self._last_upload_stats_sync_ts = now

        remote_path = self._upload_stats_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    return
                try:
                    remote_data = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_data, dict):
                    return
                self.after(0, lambda: self._apply_synced_upload_stats(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _push_deletion_stat_to_ftp(self, person: str, size_bytes: int, ts: float):
        """Suma un borrado al ranking de "limpiadores" compartido (ver
        core/deletion_stats.py, pestaña Estadísticas) -- llamado desde
        _save_deletion_history_entry para todo borrado OK. Archivo APARTE
        del ranking de subidas (ver el docstring de core/deletion_stats.py
        para el motivo); mismo patrón lectura-modificación-escritura que
        _push_upload_stat_to_ftp."""
        remote_path = self._deletion_stats_remote_path()
        if not remote_path:
            _log.info(
                "Estadísticas: sin \"Carpeta compartida (datos)\" configurada -- "
                "no se puede sumar el borrado de %r al ranking de borradores", person)
            return

        def worker():
            from core.deletion_stats import add_deletion
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    _log.warning(
                        "Estadísticas: no se pudo conectar al FTP para sumar el borrado de %r (%s)",
                        person, _msg)
                    return
                with self._stats_push_lock:
                    from core.shared_data import read_shared_json
                    remote_data, _is_new = read_shared_json(own_ftp, remote_path)
                    if remote_data is None:
                        return
                    merged = add_deletion(remote_data, person, size_bytes, ts)
                    data = _json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                    if up_ok:
                        _log.info("Estadísticas: borrado de %r sumado al ranking de borradores (%d bytes)",
                                  person, size_bytes)
                        self.after(0, lambda: self._apply_synced_deletion_stats(merged))
                    else:
                        _log.warning(
                            "Estadísticas: no se pudo subir el ranking de borradores actualizado tras sumar a %r (%s)",
                            person, _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_deletion_stats(self, merged: dict):
        from core.deletion_stats import save_local_cache as _save_deletion_stats_cache
        self._shared_deletion_stats = merged
        _save_deletion_stats_cache(merged)
        if self._stats_visible:
            self._stats_view.refresh_from_cache()

    def _sync_deletion_stats_from_ftp(self):
        """Refresca el mirror local del ranking de borradores desde el FTP
        en segundo plano -- mismo patrón y mismo freno que
        _sync_upload_stats_from_ftp. Se llama al entrar en Estadísticas."""
        now = _time.time()
        if now - self._last_deletion_stats_sync_ts < self._DELETION_STATS_SYNC_MIN_INTERVAL:
            return
        self._last_deletion_stats_sync_ts = now

        remote_path = self._deletion_stats_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    return
                try:
                    remote_data = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_data, dict):
                    return
                self.after(0, lambda: self._apply_synced_deletion_stats(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _sync_slim_stats_from_ftp(self):
        """Refresca el mirror local del ranking de adelgazadores desde el
        FTP en segundo plano -- mismo patrón y mismo freno que
        _sync_upload_stats_from_ftp. Se llama al entrar en Estadísticas."""
        now = _time.time()
        if now - self._last_slim_stats_sync_ts < self._SLIM_STATS_SYNC_MIN_INTERVAL:
            return
        self._last_slim_stats_sync_ts = now

        remote_path = self._slim_stats_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    return
                try:
                    remote_data = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_data, dict):
                    return
                self.after(0, lambda: self._apply_synced_slim_stats(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _sync_category_upload_stats_from_ftp(self):
        """Refresca el mirror local del ranking de subidores por
        categoría desde el FTP en segundo plano -- mismo patrón y mismo
        freno que _sync_upload_stats_from_ftp. Se llama al entrar en
        Estadísticas."""
        now = _time.time()
        if now - self._last_category_upload_stats_sync_ts < self._CATEGORY_UPLOAD_STATS_SYNC_MIN_INTERVAL:
            return
        self._last_category_upload_stats_sync_ts = now

        remote_path = self._category_upload_stats_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    return
                try:
                    remote_data = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_data, dict):
                    return
                self.after(0, lambda: self._apply_synced_category_upload_stats(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _push_category_stat_removal_to_ftp(self, ftp_path: str, size_bytes: int):
        """Igual que _push_category_stat_addition_to_ftp, pero quitando la
        carpeta borrada por completo -- llamado desde
        _save_deletion_history_entry para todo borrado OK. size_bytes no se
        usa (remove_folder quita el total ya conocido, no resta un valor
        suelto); se recibe por simetría con el resto de hooks de borrado."""
        from core.category_stats import resolve_category_and_folder
        resolved = resolve_category_and_folder(
            ftp_path, self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}), is_folder_path=True)
        if resolved is None:
            _log.info(
                "Estadísticas: %r no encaja con ninguna categoría configurada -- "
                "no se resta del recuento por categoría", ftp_path)
            return
        category_id, _category_name, folder_name = resolved
        remote_stats_path = self._category_stats_remote_path()
        if not remote_stats_path:
            return

        def worker():
            from core.category_stats import remove_folder, unwrap_from_remote, wrap_for_remote
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    _log.warning("Estadísticas: no se pudo conectar al FTP para restar %r (%s)", ftp_path, _msg)
                    return
                with self._stats_push_lock:
                    from core.shared_data import read_shared_json
                    base, is_new = read_shared_json(own_ftp, remote_stats_path)
                    if base is None:
                        return
                    if is_new:
                        remote_data = None
                    else:
                        try:
                            remote_data = unwrap_from_remote(base)
                        except ValueError:
                            remote_data = None
                    if remote_data is None:
                        # Ver el mismo comentario en
                        # _push_category_stat_addition_to_ftp -- no fijar una
                        # base nueva de la versión de escaneo actual con solo
                        # este borrado, dejar que el bootstrap real la
                        # establezca.
                        return
                    merged = remove_folder(remote_data, category_id, folder_name)
                    data = _json.dumps(wrap_for_remote(merged), ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_stats_path)
                    if up_ok:
                        _log.info("Estadísticas: %r quitado del recuento por categoría", folder_name)
                    self.after(0, lambda: self._apply_synced_category_stats(merged))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _sync_category_stats_from_ftp(self):
        """Refresca el mirror local del recuento/tamaño por categoría desde
        el FTP en segundo plano -- mismo patrón y mismo freno que
        _sync_upload_stats_from_ftp. Se llama al entrar en Estadísticas.

        Si el archivo remoto todavía no existe, está corrupto, O es de una
        versión de escaneo antigua (ver core.category_stats.SCAN_VERSION --
        sube cuando un fix cambia cómo se cuenta/agrupa, para que datos ya
        calculados con la lógica vieja no se sigan aplicando tal cual para
        siempre), y el bootstrap no se ha intentado todavía en esta sesión,
        en vez de dejar el panel vacío o con datos desactualizados se
        recorre el árbol completo del FTP UNA vez para sembrarlo de nuevo
        con lo que ya hay en el servidor (ver _bootstrap_category_stats) --
        después de eso, todo es incremental
        (_push_category_stat_addition_to_ftp/_push_category_stat_removal_to_ftp)
        y nunca se vuelve a recorrer el árbol entero."""
        now = _time.time()
        if now - self._last_category_stats_sync_ts < self._CATEGORY_STATS_SYNC_MIN_INTERVAL:
            return
        self._last_category_stats_sync_ts = now

        remote_path = self._category_stats_remote_path()
        if not remote_path:
            return

        def worker():
            from core.category_stats import unwrap_from_remote
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                raw = own_ftp.download_bytes(remote_path)
                remote_data = None
                if raw:
                    try:
                        remote_data = unwrap_from_remote(_json.loads(raw.decode("utf-8")))
                    except ValueError:
                        remote_data = None
                if not remote_data and not self._category_stats_bootstrap_done:
                    remote_data = self._bootstrap_category_stats(own_ftp)
                elif remote_data:
                    # Bug real: la categoría "Libros" (ebooks) se creó/usó
                    # DESPUÉS del único escaneo completo que sembró este
                    # archivo (ver _bootstrap_category_stats, que solo
                    # corre una vez por diseño) -- se quedó sin ninguna
                    # entrada para siempre, mientras "Comics-Mangas", que sí
                    # tenía contenido a tiempo, se contó bien. Backfill
                    # dirigido: solo escanea las categorías configuradas
                    # HOY que todavía no tengan ninguna entrada, sin tocar
                    # las que ya están bien (evita repetir un escaneo caro
                    # de categorías grandes -- "Series" tiene 338 carpetas).
                    remote_data = self._backfill_missing_category_stats(own_ftp, remote_data)
                if remote_data:
                    self.after(0, lambda: self._apply_synced_category_stats(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _bootstrap_category_stats(self, own_ftp) -> dict:
        """Recorre el árbol completo del FTP UNA sola vez (nunca más
        después de esto) para sembrar el archivo compartido de recuento/
        tamaño por categoría -- mismo patrón que tenía
        gui/stats_view.py antes de pasar a datos incrementales
        (list_tree_recursive + sizes_by_top_level_folder por cada raíz de
        ftp_categories, con fallback a list_dirs+get_folder_size si el
        servidor no soporta LIST -R), MÁS los archivos sueltos directamente
        en la raíz de cada categoría (típico de películas guardadas sin
        carpeta propia) agrupados por nombre base -- mismo criterio que
        _start_cleanup_scan en Liberar espacio (group_loose_files_by_name).
        Sin esto, cualquier categoría organizada así (archivo por
        archivo, no carpeta por carpeta) se contaría como vacía. Se llama
        con una conexión YA abierta (own_ftp, reutilizada de
        _sync_category_stats_from_ftp) -- no abre una segunda. Sube el
        resultado al FTP antes de devolverlo.

        Marca self._category_stats_bootstrap_done en True como primer
        paso, con éxito o no, para no reintentar este escaneo caro en cada
        sincronización si algo falla a mitad.

        Todo el escaneo corre con el timeout del socket de control
        ampliado (ver FTPClient.widened_timeout) -- el fijo de 15s puesto
        en connect() basta para comandos sueltos, pero listar una
        categoría con miles de archivos (películas sueltas, sin carpeta
        propia) en un NAS lento puede tardar bastante más, y ese timeout
        se comía el listado a mitad SIN avisar (ftplib.all_errors lo
        atrapa igual que cualquier otro fallo de red), dejando la
        categoría contada de menos o directamente vacía -- justo el
        síntoma reportado ("hay cerca de 3000 películas, solo cuenta 3";
        categorías enteras sin aparecer)."""
        self._category_stats_bootstrap_done = True
        data = {}
        cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []})
        with own_ftp.widened_timeout(300):
            for cat_list in cats.values():
                for cat in cat_list:
                    if not (cat.get("root") or "").strip():
                        continue
                    sizes = self._scan_category_sizes(own_ftp, cat, log_prefix="bootstrap")
                    if sizes:
                        data[cat.get("id", cat["root"])] = {
                            "category_name": cat.get("name", cat["root"]), "folders": sizes}
        if not data:
            return {}
        from core.category_stats import wrap_for_remote
        import json as _json
        raw = _json.dumps(wrap_for_remote(data), ensure_ascii=False, indent=2).encode("utf-8")
        own_ftp.upload_bytes(raw, self._category_stats_remote_path())
        return data

    def _backfill_missing_category_stats(self, own_ftp, remote_data: dict) -> dict:
        """Detecta categorías configuradas HOY (Ajustes → Categorías) que
        no tienen ninguna entrada en remote_data y las escanea -- SOLO a
        ellas, sin repetir el escaneo de las que ya tienen datos (evita el
        coste de recorrer categorías grandes que ya están bien, p.ej.
        "Series" con cientos de carpetas).

        Bug real: la categoría "Libros" (ebooks) se creó/empezó a recibir
        contenido DESPUÉS del único escaneo completo que sembró
        category_stats.json (ver _bootstrap_category_stats, que por diseño
        solo corre una vez por sesión/hasta que haya datos válidos) -- se
        quedó sin ninguna entrada para siempre (ni "0 bytes" siquiera),
        mientras "Comics-Mangas", que sí tenía contenido a tiempo, se
        contó bien. _sync_category_stats_from_ftp nunca volvía a
        recorrer el árbol una vez que remote_data ya era "válido" (no
        vacío, versión de escaneo correcta) -- exactamente el mismo tipo
        de dato ya sembrado y nunca recalculado documentado para el resto
        de bugs de esta pestaña. Esto lo resuelve sin necesidad de subir
        SCAN_VERSION (que forzaría re-escanear TODO, categorías grandes
        incluidas) -- solo rellena el hueco real."""
        cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []})
        missing = [cat for cat_list in cats.values() for cat in cat_list
                   if (cat.get("root") or "").strip()
                   and cat.get("id", cat["root"]) not in remote_data]
        if not missing:
            return remote_data
        added = False
        with own_ftp.widened_timeout(300):
            for cat in missing:
                sizes = self._scan_category_sizes(own_ftp, cat, log_prefix="backfill")
                if sizes:
                    remote_data[cat.get("id", cat["root"])] = {
                        "category_name": cat.get("name", cat["root"]), "folders": sizes}
                    added = True
        if added:
            from core.category_stats import wrap_for_remote
            import json as _json
            raw = _json.dumps(wrap_for_remote(remote_data), ensure_ascii=False, indent=2).encode("utf-8")
            own_ftp.upload_bytes(raw, self._category_stats_remote_path())
        return remote_data

    def _deletion_history_path(self) -> Path:
        return _appdata_dir() / "deletion_history.json"

    def _load_deletion_history(self) -> list:
        try:
            p = self._deletion_history_path()
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _save_deletion_history_entry(self, name: str, ftp_path: str, size_bytes: int,
                                     reason: str, status: str, error_msg: str = ""):
        """reason: descripción legible de qué filtros la marcaron como
        candidata (p.ej. "vista, sin repetir en 12 meses") -- para poder
        entender después POR QUÉ se borró algo, no solo QUÉ."""
        entry = {
            "ts":        _time.time(),
            "name":      name,
            "ftp_path":  ftp_path,
            "size":      size_bytes,
            "reason":    reason,
            "status":    status,
            "error_msg": error_msg,
            # Quién lo borró -- ver _save_history_entry.
            "person":    self.config_data.get("app_user_name", ""),
        }
        with self._history_lock:
            history = self._load_deletion_history()
            history.append(entry)
            if len(history) > 500:
                history = history[-500:]
            try:
                from core.atomic_write import write_json_atomic
                write_json_atomic(self._deletion_history_path(), history)
            except Exception as e:
                _log.warning("Historial: no se pudo guardar %s: %s", self._deletion_history_path(), e)
        self._push_activity_entry_to_ftp(entry, "borrado")
        if status == "ok":
            # Recuento/tamaño por categoría (ver core/category_stats.py) --
            # solo borrados que de verdad terminaron bien.
            self._push_category_stat_removal_to_ftp(ftp_path, size_bytes)
            # Ranking de "limpiadores" (ver core/deletion_stats.py) --
            # mismo criterio, solo borrados OK.
            self._push_deletion_stat_to_ftp(entry["person"], size_bytes, entry["ts"])

    def _sync_web_requests_history(self):
        """Lee en segundo plano la cola de solicitudes de la web (la misma
        que usa _download_requests_cycle) y la pinta en Historial si
        "Solicitudes web" sigue encendido. Con freno, como
        _sync_activity_history_from_ftp."""
        now = _time.time()
        if now - self._last_web_requests_sync_ts < self._WEB_REQUESTS_SYNC_MIN_INTERVAL:
            return
        self._last_web_requests_sync_ts = now
        remote_path = self._download_requests_remote_path()
        if not remote_path:
            self._apply_web_requests_history(
                [], "Falta la carpeta de datos compartidos del servidor (Configuración).")
            return

        def worker():
            from core import download_requests as _dr
            from core.shared_data import read_shared_json
            own_ftp = self._download_requests_ftp()
            if own_ftp is None:
                self.after(0, lambda: self._apply_web_requests_history(
                    None, "No se pudo conectar con el servidor."))
                return
            try:
                data, _is_new = read_shared_json(own_ftp, remote_path, "dict")
            except Exception:
                data = None
            finally:
                try:
                    own_ftp.disconnect()
                except Exception:
                    pass
            if data is None:
                self.after(0, lambda: self._apply_web_requests_history(
                    None, "No se pudo leer la cola de solicitudes."))
                return
            rows = _dr.history_rows(data)
            self.after(0, lambda: self._apply_web_requests_history(rows, ""))
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _history_entry_matches(entry: dict, query: str) -> bool:
        """True si *entry* (un registro crudo de self._history_all, antes
        de formatear para pintarlo) contiene *query* (ya en minúsculas) en
        alguno de los campos que el usuario reconocería al buscar: archivo/
        nombre, destino remoto/motivo de borrado, y quién lo hizo. No busca
        en fecha/tamaño/estado -- no son lo que alguien escribiría para
        encontrar un registro concreto."""
        kind = entry.get("kind", "subida")
        if kind == "solicitud":
            haystack = (entry.get("name", ""), entry.get("detail", ""), entry.get("person", ""),
                        entry.get("status_es", ""))
        elif kind == "borrado":
            haystack = (entry.get("name", ""), entry.get("reason", ""), entry.get("person", ""))
        else:
            haystack = (entry.get("filename", ""), entry.get("remote", ""), entry.get("person", ""))
        return any(query in (field or "").lower() for field in haystack)

    def _retry_history_upload_worker(self, local_path: str, remote_dir: str, remote_filename: str):
        # Mismo cupo de "Subidas simultáneas" que una subida normal (ver
        # core/upload_slots.py) -- sin esto, un reintento desde Historial
        # podría abrir una conexión FTP extra por encima del límite que el
        # usuario configuró, a la vez que una subida manual o automática
        # en curso.
        if not self._upload_slots.acquire(cancel_event=self._upload_cancel):
            self.after(0, lambda: self._set_status("Reintento cancelado", WARNING_COLOR))
            return
        try:
            own_ftp = self._new_ftp_client()
            ok, msg = own_ftp.connect(
                self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
                self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
                self.config_data.get("ftp_use_tls", False))
            if not ok:
                self.after(0, lambda: self._set_status(f"No se pudo conectar: {msg}", ERROR_COLOR))
                return
            try:
                speed_kbs = float(self.config_data.get("ftp_speed_limit", 0)) * 1024
                up_ok, up_msg = own_ftp.upload_file(
                    local_path, remote_dir, speed_limit_kbs=speed_kbs,
                    try_resume=True, remote_filename=remote_filename)
            finally:
                own_ftp.disconnect()
        finally:
            self._upload_slots.release()

        try:
            size = Path(local_path).stat().st_size
        except OSError:
            size = 0
        status = "ok" if up_ok else "error"
        self._save_history_entry(
            Path(local_path).name, f"{remote_dir.rstrip('/')}/{remote_filename}",
            status, size, error_msg="" if up_ok else up_msg, local_path=local_path)

        if up_ok:
            self.after(0, lambda: self._set_status(f"Reintento OK: {remote_filename}", SUCCESS_COLOR))
        else:
            self.after(0, lambda: self._set_status(f"Reintento fallido: {up_msg}", ERROR_COLOR))
        self.after(0, self._refresh_history_view)

    def _scan_category_sizes(self, own_ftp, cat: dict, log_prefix: str = "escaneo") -> dict:
        """Escanea UNA categoría (carpetas propias + archivos sueltos en la
        raíz, agrupados por nombre base) y devuelve {nombre: bytes} -- lógica
        compartida entre _bootstrap_category_stats (todas las categorías,
        una vez por sesión) y _backfill_missing_category_stats (solo las
        que falten, ver más abajo). El llamador es responsable de envolver
        la llamada en own_ftp.widened_timeout(...) -- listar una categoría
        grande puede tardar bastante más que el timeout de 15s por defecto
        de connect()."""
        from core.cleanup_candidates import group_loose_files_by_name
        root = (cat.get("root") or "").rstrip("/")
        if not root:
            return {}
        tree = own_ftp.list_tree_recursive(root)
        if tree is not None:
            sizes = sizes_by_top_level_folder(tree, root)
            root_files = tree.get(root, [])
            via = "LIST -R"
        else:
            sizes = {name: own_ftp.get_folder_size(f"{root}/{name}")
                     for name in own_ftp.list_dirs(root)}
            root_files = own_ftp.list_files_with_sizes(root)
            via = "list_dirs (sin soporte LIST -R)"
        loose_groups = group_loose_files_by_name(root_files)
        for base_name, group in loose_groups.items():
            sizes[base_name] = sizes.get(base_name, 0) + group["size_bytes"]
        _log.info(
            "Estadísticas: %s categoría %r (%s) vía %s -- "
            "%d carpeta(s), %d archivo(s) suelto(s) en la raíz -> "
            "%d agrupado(s), %d elemento(s) en total",
            log_prefix, cat.get("name", root), root, via,
            len(sizes) - len(loose_groups), len(root_files), len(loose_groups), len(sizes))
        return sizes
