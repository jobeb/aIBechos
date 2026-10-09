"""
Datos compartidos con el resto de equipos por el FTP (carpeta compartida):
rutas de cada archivo y las estadísticas que se actualizan al subir (ranking
de subidas, por categoría, adelgazamientos, actividad). Mixin sin interfaz que
hereda QtAppCore (gui_qt/core_host.py).
"""

import threading

from core import shared_data
from core.applog import get_logger

_log = get_logger("aIBechos.gui", "app.log")


class SharedSyncMixin:

    def _shared_data_path(self, filename: str) -> str:
        """Ruta remota de *filename* dentro de la carpeta compartida (ver
        "Carpeta compartida (datos)" en Ajustes -> Cliente -> Conexión
        FTP, shared_data_ftp_path) -- favoritos, reservas y configuración
        de servidor son 3 archivos independientes con nombre fijo dentro
        de esa misma carpeta, no derivados unos de otros por sufijo (así
        se llamaban todos "favoritos_algo.json" antes, aunque no tuvieran
        nada que ver con favoritos -- ver el nombre de archivo de cada
        uno en _favorites_remote_path/_reservations_remote_path/
        _server_config_remote_path). "" si no hay carpeta configurada
        (las 3 funciones quedan deshabilitadas, cada mirror local se
        queda con el último estado conocido)."""
        folder = self.config_data.get("shared_data_ftp_path", "").strip()
        if not folder:
            return ""
        return f"{folder.rstrip('/')}/{filename}"

    def _activity_remote_path(self) -> str:
        """Ruta remota del historial de actividad compartido (subidas y
        borrados de todos los clientes, ver Historial → "Ver todo el
        servidor") -- archivo propio dentro de la misma carpeta
        compartida, mismo motivo que _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("actividad"))

    def _upload_stats_remote_path(self) -> str:
        """Ruta remota del ranking de subidas por usuario (ver
        core/upload_stats.py, pestaña Estadísticas) -- archivo propio
        dentro de la misma carpeta compartida, mismo motivo que
        _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("estadisticas_usuarios"))

    def _category_stats_remote_path(self) -> str:
        """Ruta remota del recuento/tamaño por categoría (ver
        core/category_stats.py, pestaña Estadísticas) -- archivo propio
        dentro de la misma carpeta compartida, mismo motivo que
        _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("estadisticas_categorias"))

    def _slim_stats_remote_path(self) -> str:
        """Ruta remota del ranking de adelgazamientos por usuario (ver
        core/slim_stats.py, pestaña Estadísticas) -- archivo propio
        dentro de la misma carpeta compartida, mismo motivo que
        _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("estadisticas_adelgazadores"))

    def _category_upload_stats_remote_path(self) -> str:
        """Ruta remota del ranking de subidas desglosado por categoría
        (ver core/category_upload_stats.py, pestaña Estadísticas) --
        archivo propio dentro de la misma carpeta compartida, mismo
        motivo que _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("estadisticas_subidores_categoria"))

    def _push_activity_entry_to_ftp(self, entry: dict, kind: str):
        """Añade *entry* (una subida o un borrado, ya guardada en el
        historial LOCAL antes de llamar a esto) al historial de actividad
        compartido entre clientes del mismo servidor -- silencioso si no
        hay carpeta compartida configurada o la subida falla, mismo
        criterio que el resto de sincronizaciones de la app: el historial
        local es la copia que de verdad importa, esto es un añadido de
        mejor esfuerzo.

        A diferencia de reservas/favoritos/veredictos de doblaje (un dict
        que se fusiona clave a clave), el remoto aquí es una LISTA que
        solo crece por añadido -- se descarga fresca, se le añade esta
        entrada con su "kind" ("subida"/"borrado"), se recorta a los
        últimos 500 igual que los históricos locales, y se vuelve a
        subir."""
        remote_path = self._activity_remote_path()
        if not remote_path:
            return

        def worker():
            import json as _json
            from core.shared_data import read_shared_json
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
                with self._stats_push_lock:
                    remote_list, _is_new = read_shared_json(own_ftp, remote_path, kind="list")
                    if remote_list is None:
                        return
                    remote_list.append({**entry, "kind": kind})
                    if len(remote_list) > 500:
                        remote_list = remote_list[-500:]
                    data = _json.dumps(remote_list, ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                    if up_ok:
                        self.after(0, lambda: self._apply_synced_activity_history(remote_list))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _push_upload_stat_to_ftp(self, person: str, size_bytes: int, ts: float):
        """Suma una subida al ranking compartido (ver core/upload_stats.py,
        pestaña Estadísticas) -- llamado desde _save_history_entry para
        toda subida OK, manual o de AutoWatcher. A diferencia del
        historial de actividad (que recorta a 500), este archivo nunca
        rota: cada subida SUMA a un total por persona, para siempre.
        Mismo patrón lectura-modificación-escritura que
        _push_shared_dub_verdict_to_ftp."""
        remote_path = self._upload_stats_remote_path()
        if not remote_path:
            _log.info(
                "Estadísticas: sin \"Carpeta compartida (datos)\" configurada -- "
                "no se puede sumar la subida de %r al ranking", person)
            return

        def worker():
            from core.upload_stats import add_upload
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
                    _log.warning("Estadísticas: no se pudo conectar al FTP para sumar la subida de %r al ranking (%s)",
                                 person, _msg)
                    return
                with self._stats_push_lock:
                    from core.shared_data import read_shared_json
                    remote_data, _is_new = read_shared_json(own_ftp, remote_path)
                    if remote_data is None:
                        return
                    merged = add_upload(remote_data, person, size_bytes, ts)
                    data = _json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                    if up_ok:
                        _log.info("Estadísticas: subida de %r sumada al ranking (%d bytes)", person, size_bytes)
                        self.after(0, lambda: self._apply_synced_upload_stats(merged))
                    else:
                        _log.warning("Estadísticas: no se pudo subir el ranking actualizado tras sumar a %r (%s)",
                                     person, _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_upload_stats(self, merged: dict):
        from core.upload_stats import save_local_cache as _save_upload_stats_cache
        self._shared_upload_stats = merged
        _save_upload_stats_cache(merged)
        if self._stats_visible:
            self._stats_view.refresh_from_cache()

    def _push_slim_stat_to_ftp(self, person: str, saved_bytes: int, ts: float):
        """Suma un adelgazamiento completado al ranking compartido (ver
        core/slim_stats.py, pestaña Estadísticas) -- llamado desde
        _on_auto_file_event al recibir "slim_replaced" del watcher (gordo
        borrado + ligera subida). Archivo APARTE del ranking de subidas y
        del de borrados (ver el docstring de core/slim_stats.py para el
        motivo); mismo patrón lectura-modificación-escritura que
        _push_upload_stat_to_ftp."""
        remote_path = self._slim_stats_remote_path()
        if not remote_path:
            _log.info(
                "Estadísticas: sin \"Carpeta compartida (datos)\" configurada -- "
                "no se puede sumar el adelgazamiento de %r al ranking", person)
            return
        if not (person or "").strip() or int(saved_bytes or 0) <= 0:
            return

        def worker():
            from core.slim_stats import add_slim
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
                        "Estadísticas: no se pudo conectar al FTP para sumar el adelgazamiento de %r (%s)",
                        person, _msg)
                    return
                with self._stats_push_lock:
                    from core.shared_data import read_shared_json
                    remote_data, _is_new = read_shared_json(own_ftp, remote_path)
                    if remote_data is None:
                        return
                    merged = add_slim(remote_data, person, saved_bytes, ts)
                    data = _json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                    if up_ok:
                        _log.info("Estadísticas: adelgazamiento de %r sumado al ranking (%d bytes ahorrados)",
                                  person, saved_bytes)
                        self.after(0, lambda: self._apply_synced_slim_stats(merged))
                    else:
                        _log.warning(
                            "Estadísticas: no se pudo subir el ranking de adelgazadores actualizado tras sumar a %r (%s)",
                            person, _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_slim_stats(self, merged: dict):
        from core.slim_stats import save_local_cache as _save_slim_stats_cache
        self._shared_slim_stats = merged
        _save_slim_stats_cache(merged)
        if self._stats_visible:
            self._stats_view.refresh_from_cache()

    def _push_category_upload_stat_to_ftp(self, remote_path: str, person: str, size_bytes: int, ts: float):
        """Suma una subida al ranking de subidores DE ESA CATEGORÍA
        concreta (ver core/category_upload_stats.py, un panel "Top
        subidores" por categoría en Estadísticas) -- llamado desde
        _save_history_entry para toda subida OK. A diferencia del
        recuento por categoría (core/category_stats.py, que necesita un
        bootstrap con el árbol completo del FTP porque el servidor sí
        conserva qué archivos hay), este ranking es puramente incremental
        -- el servidor no guarda QUIÉN subió cada archivo, así que no hay
        nada que "re-escanear"; se construye solo a partir de los eventos
        de subida, igual que core/upload_stats.py."""
        from core.category_stats import resolve_category_and_folder
        resolved = resolve_category_and_folder(
            remote_path, self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}))
        if resolved is None:
            return
        category_id, category_name, _folder_name = resolved
        remote_stats_path = self._category_upload_stats_remote_path()
        if not remote_stats_path:
            return

        def worker():
            from core.category_upload_stats import add_category_upload
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
                        "Estadísticas: no se pudo conectar al FTP para sumar la subida de %r a "
                        "\"Top subidores\" de %r (%s)", person, category_name, _msg)
                    return
                with self._stats_push_lock:
                    from core.shared_data import read_shared_json
                    remote_data, _is_new = read_shared_json(own_ftp, remote_stats_path)
                    if remote_data is None:
                        return
                    merged = add_category_upload(remote_data, category_id, category_name, person, size_bytes, ts)
                    data = _json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_stats_path)
                    if up_ok:
                        _log.info("Estadísticas: subida de %r sumada a \"Top subidores\" de %r (%d bytes)",
                                  person, category_name, size_bytes)
                        self.after(0, lambda: self._apply_synced_category_upload_stats(merged))
                    else:
                        _log.warning(
                            "Estadísticas: no se pudo subir el ranking por categoría actualizado tras sumar "
                            "a %r en %r (%s)", person, category_name, _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_category_upload_stats(self, merged: dict):
        from core.category_upload_stats import save_local_cache as _save_category_upload_stats_cache
        self._shared_category_upload_stats = merged
        _save_category_upload_stats_cache(merged)
        if self._stats_visible:
            self._stats_view.refresh_from_cache()

    def _push_category_stat_addition_to_ftp(self, remote_path: str, size_bytes: int):
        """Suma el tamaño de una subida OK a la carpeta de nivel superior a
        la que pertenece (ver core/category_stats.py, panel "Por
        categoría" de Estadísticas) -- llamado desde _save_history_entry.
        Si remote_path no encaja con ninguna categoría configurada, o es un
        archivo suelto sin carpeta propia, no hace nada (mismo criterio de
        exclusión que sizes_by_top_level_folder)."""
        from core.category_stats import resolve_category_and_folder
        resolved = resolve_category_and_folder(
            remote_path, self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}))
        if resolved is None:
            _log.info(
                "Estadísticas: %r no encaja con ninguna categoría configurada (ftp_categories) -- "
                "no se suma al recuento por categoría", remote_path)
            return
        category_id, category_name, folder_name = resolved
        remote_stats_path = self._category_stats_remote_path()
        if not remote_stats_path:
            _log.info(
                "Estadísticas: sin \"Carpeta compartida (datos)\" configurada -- "
                "no se puede sumar %r al recuento por categoría", remote_path)
            return

        def worker():
            from core.category_stats import add_folder_bytes, unwrap_from_remote, wrap_for_remote
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
                    _log.warning("Estadísticas: no se pudo conectar al FTP para sumar %r (%s)",
                                 remote_path, _msg)
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
                        # Todavía sin una base de la versión de escaneo actual
                        # (primera vez, o versión antigua pendiente de
                        # recontar, ver SCAN_VERSION) -- no crear una base
                        # nueva aquí con solo ESTA subida: eso "fijaría" la
                        # versión con datos incompletos y el bootstrap real
                        # (que sí recorre el servidor entero) ya no se
                        # dispararía nunca (_sync_category_stats_from_ftp solo
                        # arranca el bootstrap si remote_data sigue siendo
                        # None). Se deja sin más: el bootstrap, cuando corra,
                        # ya contará esta subida al escanear el estado real
                        # del servidor.
                        # IMPORTANTE: aunque no se sume ahora, sí se registra
                        # la intención de subida en el historial local para
                        # que, al visitar Estadísticas y disparar el bootstrap,
                        # se cuente este y todos los registros previos.
                        _log.info(
                            "Estadísticas: %r (categoría %r)NO sumado todavía -- el archivo "
                            "compartido de categorías aún no tiene una base válida (hace falta "
                            "visitar la pestaña Estadísticas al menos una vez para que el "
                            "bootstrap la cree; entonces esta subida ya se contará sola). "
                            "Se registra en historial local para posterior conteo. "
                            "folder_name=%s, category_name=%s",
                            folder_name, category_name, folder_name, category_name)
                        # Aún así, registramos la entrada en el historial local para
                        # que el usuario vea la subida en el historial aunque las
                        # estadísticas por categoría aún no estén activas.
                        return
                    merged = add_folder_bytes(remote_data, category_id, category_name, folder_name, size_bytes)
                    data = _json.dumps(wrap_for_remote(merged), ensure_ascii=False, indent=2).encode("utf-8")
                    up_ok, _up_msg = own_ftp.upload_bytes(data, remote_stats_path)
                    if up_ok:
                        _log.info("Estadísticas: %r (categoría %r) sumado -- %d bytes",
                                  folder_name, category_name, size_bytes)
                        self.after(0, lambda: self._apply_synced_category_stats(merged))
                    else:
                        _log.warning("Estadísticas: no se pudo subir el recuento actualizado tras sumar %r (%s)",
                                     folder_name, _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_category_stats(self, merged: dict):
        from core.category_stats import save_local_cache as _save_category_stats_cache
        self._shared_category_stats = merged
        _save_category_stats_cache(merged)
        if self._stats_visible:
            self._stats_view.refresh_from_cache()
