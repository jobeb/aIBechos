"""
"Liberar espacio" y "Protegidos" sin interfaz: escaneo de candidatas a
borrar (lo que menos se ve en el servidor), episodios por carpeta, modo
plano, "adelgazar" (cambiar un archivo pesado por una versión más ligera),
borrar del servidor con registro, y la lista compartida por FTP. Incluye el
borrado de una serie desde "Episodios que faltan". Mixin que hereda
QtAppCore (gui_qt/core_host.py).

Ganchos de interfaz: _apply_cleanup_filters(), _render_cleanup_page(...),
_apply_synced_cleanup_candidates(payload), _update_cleanup_delete_progress(...),
_finish_delete_cleanup_item(...), _render_protected_table(),
_make_confirm_delete_dialog(...) -> objeto con .result.
"""

import threading
import time as _time

from core import shared_data
from core.ftp_client import sizes_by_top_level_folder
from core.series_match import match_names_exclusively
from core.trending import trending_score
from core.applog import get_logger
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


def _store_cleanup_retree(trees: dict, cache: dict, ftp_path: str, tree: dict, now: float) -> None:
    """Guarda un árbol recién listado (ver FTPClient.get_folder_tree) en
    los DOS sitios que lee App._cleanup_ep_files: trees por carpeta (como
    el análisis completo) y cache plana por ftp_path exacto (como el
    listado bajo demanda). La segunda es la que salva cuando el servidor
    devuelve claves relativas (LIST -R con "./x" en vez de la ruta
    absoluta): esas nunca casan por prefijo con ftp_path y sin la caché
    la expansión seguiría diciendo "No se pudo listar todavía" aunque el
    reescaneo hubiera ido bien. Pura salvo los dicts que muta, testeable.
    No lanza excepción."""
    try:
        flat = []
        for folder, entries in (tree or {}).items():
            if folder:
                folder_files = []
                for name, size in (entries or []):
                    try:
                        clean = (name, int(size or 0))
                    except (TypeError, ValueError):
                        continue
                    folder_files.append(clean)
                    flat.append((clean[0], clean[1], folder))
                try:
                    trees[folder] = folder_files
                except Exception:
                    pass
            else:
                for name, size in (entries or []):
                    try:
                        flat.append((name, int(size or 0), folder))
                    except (TypeError, ValueError):
                        pass
        try:
            cache[ftp_path] = (now, flat)
        except Exception:
            pass
    except Exception:
        pass


class CleanupCoreMixin:
    # Constantes de clase (antes en App, gui/app.py)
    _CLEANUP_CANDIDATES_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_cleanup_candidates_from_ftp


    def _cleanup_candidates_remote_path(self) -> str:
        """Ruta remota de la lista compartida de "Liberar espacio" (ver
        core/cleanup_candidates_cache.py) -- archivo propio dentro de la
        misma carpeta compartida, mismo motivo que
        _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("liberar_espacio"))

    def _push_cleanup_candidates_to_ftp(self, items: list, last_scan_ts: float):
        """Comparte el resultado de un análisis completo de "Liberar
        espacio" (ver core/cleanup_candidates_cache.py) -- llamado tras
        "Analizar servidor", para que el resto de clientes del mismo
        servidor vean este resultado sin tener que repetir el análisis
        (que puede tardar más de un minuto). A diferencia del recuento
        por categoría (que suma/resta incrementalmente), un análisis
        fresco SIEMPRE reemplaza la lista compartida entera -- acaba de
        consultar el estado real del servidor, es la fuente más fiable
        posible."""
        remote_path = self._cleanup_candidates_remote_path()
        if not remote_path:
            return

        def worker():
            from core.cleanup_candidates_cache import wrap_for_remote
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
                    _log.warning("Liberar espacio: no se pudo conectar al FTP para compartir el análisis (%s)",
                                 _msg)
                    return
                payload = wrap_for_remote(items, last_scan_ts, self.config_data.get("app_user_name", ""))
                data = _json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                if up_ok:
                    _log.info("Liberar espacio: análisis compartido con %d elemento(s)", len(items))
                else:
                    _log.warning("Liberar espacio: no se pudo subir el análisis compartido (%s)", _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _push_cleanup_deletion_to_ftp(self, ftp_path: str):
        """Quita el elemento borrado de la lista compartida de "Liberar
        espacio" -- llamado tras un borrado con éxito. A diferencia de
        _push_cleanup_candidates_to_ftp (que reemplaza la lista entera
        tras un análisis fresco), aquí solo hace falta descargar la lista
        compartida actual, quitar ese elemento por su ftp_path, y volver
        a subir -- no hace falta repetir el análisis completo solo para
        reflejar un borrado."""
        remote_path = self._cleanup_candidates_remote_path()
        if not remote_path:
            return

        def worker():
            from core.cleanup_candidates_cache import wrap_for_remote, unwrap_from_remote
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
                if remote_data is None:
                    return   # nada compartido todavía (o corrupto) -- nada que actualizar
                remaining = [it for it in remote_data["items"] if it.ftp_path != ftp_path]
                if len(remaining) == len(remote_data["items"]):
                    return   # no estaba en la lista compartida -- nada que hacer
                payload = wrap_for_remote(remaining, remote_data.get("last_scan_ts"),
                                          remote_data.get("scanned_by", ""))
                data = _json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                if up_ok:
                    self.after(0, lambda: self._apply_synced_cleanup_candidates(
                        {"items": remaining, "last_scan_ts": remote_data.get("last_scan_ts"),
                         "scanned_by": remote_data.get("scanned_by", "")}, force=True, rerender=False))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _sync_cleanup_candidates_from_ftp(self):
        """Refresca el mirror local de "Liberar espacio" desde el FTP en
        segundo plano -- mismo patrón y mismo freno que
        _sync_upload_stats_from_ftp. Se llama al entrar en Liberar
        espacio. Solo se aplica si el resultado compartido es MÁS
        RECIENTE que el que ya tenemos (ver _apply_synced_cleanup_candidates)
        -- un análisis remoto viejo nunca pisa uno local más fresco."""
        now = _time.time()
        if now - self._last_cleanup_candidates_sync_ts < self._CLEANUP_CANDIDATES_SYNC_MIN_INTERVAL:
            return
        self._last_cleanup_candidates_sync_ts = now

        remote_path = self._cleanup_candidates_remote_path()
        if not remote_path:
            return

        def worker():
            from core.cleanup_candidates_cache import unwrap_from_remote
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
                    payload = unwrap_from_remote(_json.loads(raw.decode("utf-8")))
                except ValueError:
                    return
                if payload is None:
                    return
                self.after(0, lambda: self._apply_synced_cleanup_candidates(payload))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _confirm_delete_missing_ep_series(self, r: dict):
        if not self.config_data.get("ftp_host", ""):
            self._set_status("Configura la conexión FTP en Ajustes para poder borrar series", WARNING_COLOR)
            return
        self._set_status(f"Buscando \"{r['name']}\" en el servidor...", PENDING_COLOR)
        threading.Thread(target=self._resolve_missing_ep_series_path, args=(r,), daemon=True).start()

    def _resolve_missing_ep_series_path(self, r: dict):
        import types
        own_ftp = self._new_ftp_client()
        ok, msg = own_ftp.connect(
            self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
            self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
            self.config_data.get("ftp_use_tls", False))
        if not ok:
            self.after(0, lambda: self._set_status(f"No se pudo conectar al FTP: {msg}", ERROR_COLOR))
            return
        try:
            known_folder = r.get("folder_name")
            info = types.SimpleNamespace(title=r["name"], media_type="tv", folder_name=known_folder)
            cat, folder_name = self._find_category_with_existing_folder(
                own_ftp, info, force_refresh=True, known_year=self._missing_ep_known_year(r))
            if not folder_name:
                # Diagnóstico para la próxima vez que esto falle: sin esto,
                # "no encontrado" no dice si es que r["folder_name"] estaba
                # vacío (Jellyfin no lo dio, o la fila viene de Plex, que
                # todavía no lo trae), o si estando presente el listado del
                # FTP no lo encontró de todas formas -- dos causas muy
                # distintas que un mismo mensaje en pantalla no distingue.
                cats_checked = [c.get("root", "") for c in
                               self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}).get("tv", [])]
                _log.warning(
                    "Borrar serie: '%s' (tmdb_id=%s, source=%s) no encontrada -- "
                    "folder_name conocido=%r, categorías comprobadas=%s",
                    r["name"], r.get("tmdb_id"), r.get("source"), known_folder, cats_checked)
                self.after(0, lambda: self._set_status(
                    f"No se encontró \"{r['name']}\" en el servidor -- no se puede borrar", ERROR_COLOR))
                return
            # .rstrip("/"): algunas categorías guardan la raíz con barra
            # final (p.ej. "/datos2/series/") -- sin quitarla, la ruta
            # quedaba con doble barra ("/datos2/series//Accused"). La
            # mayoría de servidores FTP la toleran igual, pero mejor no
            # confiar en eso para una ruta que se va a usar para borrar.
            ftp_path = f"{cat.get('root', '').rstrip('/')}/{folder_name}"
            size_bytes = own_ftp.get_folder_size(ftp_path)
        finally:
            own_ftp.disconnect()
        self.after(0, lambda: self._show_delete_missing_ep_series_dialog(r, ftp_path, size_bytes))

    def _show_delete_missing_ep_series_dialog(self, r: dict, ftp_path: str, size_bytes: int):
        self._set_status("", PENDING_COLOR)
        dlg = self._make_confirm_delete_dialog(r["name"], ftp_path, size_bytes,
                                    "Borrado manual desde Episodios que faltan")
        if not dlg.result:
            return
        threading.Thread(target=self._delete_missing_ep_series_worker,
                         args=(r, ftp_path, size_bytes), daemon=True).start()

    def _delete_missing_ep_series_worker(self, r: dict, ftp_path: str, size_bytes: int):
        own_ftp = self._new_ftp_client()
        ok, msg = own_ftp.connect(
            self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
            self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
            self.config_data.get("ftp_use_tls", False))
        if ok:
            ok, msg = own_ftp.delete_folder_recursive(ftp_path)
            own_ftp.disconnect()
        self._save_deletion_history_entry(
            name=r["name"], ftp_path=ftp_path, size_bytes=size_bytes,
            reason="Borrado manual desde Episodios que faltan",
            status="ok" if ok else "error", error_msg="" if ok else msg)
        self.after(0, lambda: self._finish_delete_missing_ep_series(r, ok, msg))

    def _finish_delete_missing_ep_series(self, r: dict, ok: bool, msg: str):
        if ok:
            self._set_status(f"Eliminado: {r['name']}", SUCCESS_COLOR)
            self._refresh_ftp_space()   # el borrado cambia el espacio libre real
            self._remove_series_from_missing_episodes(r["tmdb_id"])
        else:
            self._set_status(f"No se pudo eliminar {r['name']}: {msg}", ERROR_COLOR)

    @staticmethod
    def _fmt_cleanup_scan_age(age_seconds) -> str:
        if age_seconds is None:
            return "hace un tiempo"
        if age_seconds < 3600:
            return f"hace {int(age_seconds // 60)} min"
        if age_seconds < 86400:
            return f"hace {int(age_seconds // 3600)} h"
        return f"hace {int(age_seconds // 86400)} día(s)"

    def _scan_cleanup_candidates(self, progress_cb=None) -> list:
        """Combina Jellyfin/Plex (visionado) y FTP (tamaño, categoría) en
        una lista de CleanupItem -- la fuente de datos cruda que luego se
        filtra con core.cleanup_candidates.filter_candidates() según lo
        que el usuario tenga marcado. Conexión FTP propia (no self.ftp),
        para no competir con otro uso simultáneo de la conexión
        compartida (subidas, refresco de espacio...)."""
        from core.cleanup_candidates import CleanupItem, merge_usage_entries
        from core.media_server_refresh import get_jellyfin_usage_stats, get_plex_usage_stats, parse_media_date

        t0 = _time.monotonic()
        # Claves (nombre, tipo) en vez de solo nombre -- una serie y una
        # película pueden compartir el mismo título EXACTO (p.ej.
        # "Fargo", serie y película sin relación entre sí), y antes se
        # fusionaban en una sola entrada, haciendo que las dos carpetas
        # del FTP acabaran con el mismo tmdb_id/visionado aunque fueran
        # contenidos distintos.
        usage_by_key = {}

        def _usage_bucket(media_type):
            return "tv" if media_type == "tv" else "movie"

        def _merge_usage(name, media_type, entry):
            key = (name, _usage_bucket(media_type))
            existing = usage_by_key.get(key)
            usage_by_key[key] = merge_usage_entries(existing, entry) if existing else entry

        if self.config_data.get("jellyfin_enabled"):
            if progress_cb:
                progress_cb(0, 0, "Consultando Jellyfin (visionado y tamaños)...")
            t_jf = _time.monotonic()
            stats = get_jellyfin_usage_stats(
                self.config_data.get("jellyfin_host", ""), self.config_data.get("jellyfin_api_key", ""),
                username=self.config_data.get("jellyfin_username", "")) or {}
            for key, entry in stats.items():
                entry["source"] = "jellyfin"
                entry["server_id"] = key
                _merge_usage(entry["name"], entry.get("media_type"), entry)
            _log.info("Liberar espacio: Jellyfin -> %d elemento(s) en %.1fs",
                      len(stats), _time.monotonic() - t_jf)
        if self.config_data.get("plex_enabled"):
            if progress_cb:
                progress_cb(0, 0, "Consultando Plex (visionado y tamaños)...")
            t_px = _time.monotonic()
            stats = get_plex_usage_stats(
                self.config_data.get("plex_host", ""), self.config_data.get("plex_token", "")) or {}
            for key, entry in stats.items():
                entry["source"] = "plex"
                entry["server_id"] = key
                _merge_usage(entry["name"], entry.get("media_type"), entry)
            _log.info("Liberar espacio: Plex -> %d elemento(s) en %.1fs",
                      len(stats), _time.monotonic() - t_px)

        own_ftp = self._new_ftp_client()
        ok, _msg = own_ftp.connect(
            self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
            self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
            self.config_data.get("ftp_use_tls", False))
        if not ok:
            return []

        try:
            if progress_cb:
                progress_cb(0, 0, "Listando carpetas del FTP...")
            from core.cleanup_candidates import group_loose_files_by_name

            cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []})
            folder_list = []       # [(media_type, categoria, root, carpeta), ...]
            loose_groups_list = []  # [(media_type, categoria, root, nombre_base, {"size_bytes","file_names"}), ...]
            ftp_tree_sizes = {}    # (root, carpeta) -> tamaño, solo si "LIST -R" funcionó para ese root
            dash_r_roots = 0
            for media_type, cat_list in cats.items():
                # "Liberar espacio" decide qué borrar según el visionado en
                # Plex/Jellyfin -- un dato que no existe para libros/cómics,
                # así que "libro" queda fuera de este escaneo en esta
                # primera versión (igual que se excluyó de AutoWatcher) en
                # vez de generalizar todo este flujo (bucket de uso,
                # icono, ficha de detalle vía TMDB...) para un tipo que
                # nunca tendría datos que mostrar aquí.
                if media_type not in ("tv", "movie"):
                    continue
                for cat in cat_list:
                    root = cat.get("root", "").rstrip("/")
                    if not root:
                        continue
                    # "LIST -R" trae el árbol entero (con tamaños) de esta
                    # categoría en UNA sola petición -- si el servidor lo
                    # soporta (vsftpd con ls_recurse_enable=YES en
                    # vsftpd.conf), evita tener que pedir el tamaño de
                    # cada serie/película una por una. Si no lo soporta,
                    # se sigue como antes (recorrido por carpeta).
                    tree = own_ftp.list_tree_recursive(root)
                    if tree is not None:
                        dash_r_roots += 1
                        for name, total_bytes in sizes_by_top_level_folder(tree, root).items():
                            ftp_tree_sizes[(root, name)] = total_bytes
                        root_files = tree.get(root, [])
                        # Guardar el árbol para "Adelgazar": expandir una
                        # serie reutiliza estos tamaños por archivo sin
                        # peticiones FTP extra (ver _cleanup_ep_files).
                        try:
                            self._cleanup_trees.update(tree)
                            self._cleanup_trees_ts = _time.time()
                        except Exception:
                            pass
                    else:
                        root_files = own_ftp.list_files_with_sizes(root)
                    for folder in own_ftp.list_dirs(root):
                        folder_list.append((media_type, cat, root, folder))
                    # Series/películas guardadas como archivos sueltos
                    # directamente en la raíz (sin carpeta propia -- visto
                    # de verdad: una biblioteca de películas con
                    # vídeo+póster+backdrop+.nfo sueltos, sin una carpeta
                    # por película) -- sin esto, "Liberar espacio" solo
                    # veía las carpetas y se saltaba casi todo el
                    # contenido guardado así.
                    for base_name, group in group_loose_files_by_name(root_files).items():
                        loose_groups_list.append((media_type, cat, root, base_name, group))

            if dash_r_roots:
                _log.info("Liberar espacio: LIST -R soportado en %d/%d categoría(s)",
                          dash_r_roots, sum(len(v) for v in cats.values()))
            if loose_groups_list:
                _log.info("Liberar espacio: %d elemento(s) detectados como archivos sueltos (sin carpeta propia)",
                          len(loose_groups_list))

            # Emparejamiento EXCLUSIVO, separado por tipo de medio: antes,
            # cada carpeta buscaba su mejor coincidencia por separado con
            # best_match(), lo que permitía que dos carpetas de nombre
            # parecido (una serie y su remake, "X" y "X: la película"...)
            # se llevaran el MISMO título de Jellyfin/Plex -- dejando a
            # una de las dos con datos de visionado que en realidad eran
            # de la otra. Ahora se calcula de una vez, con el parecido
            # más fuerte ganando prioridad, y ningún título se asigna dos
            # veces -- y SOLO dentro del mismo tipo (serie con serie,
            # película con película): una serie y una película pueden
            # compartir título EXACTO sin ser lo mismo (p.ej. "Fargo",
            # serie y película sin relación), y comparándolas todas
            # juntas se fusionaban por error.
            # lax_fallback=True: la carpeta del servidor a veces solo trae
            # el título corto ("Boruto") mientras Jellyfin/Plex tiene el
            # largo ("Boruto: Naruto Next Generations") -- la pasada
            # estricta no lo casa (0.33 < 0.75), igual que ocurría con el
            # cruce FTP de "Episodios que faltan" (ver _find_episodes_in_ftp,
            # que usa series_similarity laxa). El fallback casa SOLO los que
            # se quedaron sin pareja y solo contra targets libres, sin
            # reabrir el caso "Animal"/"Animal Crackers" (ver docstring de
            # match_names_exclusively).
            candidate_names_by_bucket = {"tv": [], "movie": []}
            for _mt, _cat, _root, folder in folder_list:
                candidate_names_by_bucket[_usage_bucket(_mt)].append(folder)
            for _mt, _cat, _root, base_name, _grp in loose_groups_list:
                candidate_names_by_bucket[_usage_bucket(_mt)].append(base_name)
            usage_names_by_bucket = {"tv": [], "movie": []}
            for _name, _bucket in usage_by_key.keys():
                usage_names_by_bucket[_bucket].append(_name)
            name_to_usage_name_by_bucket = {}
            for _bucket in ("tv", "movie"):
                if usage_names_by_bucket[_bucket] and candidate_names_by_bucket[_bucket]:
                    name_to_usage_name_by_bucket[_bucket] = match_names_exclusively(
                        candidate_names_by_bucket[_bucket], usage_names_by_bucket[_bucket],
                        min_ratio=0.75, lax_fallback=True)
                else:
                    name_to_usage_name_by_bucket[_bucket] = {}

            items = []
            total = len(folder_list) + len(loose_groups_list)
            matched_count = 0
            ftp_size_count = 0
            t_walk = _time.monotonic()
            for i, (media_type, cat, root, folder) in enumerate(folder_list):
                if progress_cb:
                    progress_cb(i + 1, total, folder)
                path = f"{root.rstrip('/')}/{folder}"
                bucket = _usage_bucket(media_type)
                usage = None
                matched_name = name_to_usage_name_by_bucket[bucket].get(folder)
                if matched_name:
                    usage = usage_by_key.get((matched_name, bucket))
                    matched_count += 1
                # Orden de preferencia para el tamaño: 1) LIST -R (ya
                # calculado para TODA la categoría en una sola petición,
                # el más barato), 2) Jellyfin/Plex si hay coincidencia por
                # nombre, 3) cálculo por FTP carpeta por carpeta (el más
                # lento, solo como último recurso).
                size = ftp_tree_sizes.get((root, folder)) or (usage or {}).get("size_bytes") or 0
                if not size:
                    ftp_size_count += 1
                    size = own_ftp.get_folder_size(path)
                items.append(CleanupItem(
                    tmdb_id=(usage or {}).get("tmdb_id"),
                    name=folder,
                    media_type="tv" if media_type == "tv" else "movie",
                    ftp_path=path,
                    category_name=cat.get("name", ""),
                    year=(usage or {}).get("year", ""),
                    size_bytes=size,
                    fully_watched=bool((usage or {}).get("fully_watched")),
                    play_count=(usage or {}).get("play_count", 0) or 0,
                    last_played_ts=parse_media_date((usage or {}).get("last_played")),
                    date_added_ts=parse_media_date((usage or {}).get("date_added")),
                    source=(usage or {}).get("source", ""),
                    server_id=(usage or {}).get("server_id", ""),
                ))
            for j, (media_type, cat, root, base_name, group) in enumerate(loose_groups_list):
                if progress_cb:
                    progress_cb(len(folder_list) + j + 1, total, base_name)
                bucket = _usage_bucket(media_type)
                usage = None
                matched_name = name_to_usage_name_by_bucket[bucket].get(base_name)
                if matched_name:
                    usage = usage_by_key.get((matched_name, bucket))
                    matched_count += 1
                # El tamaño ya se conoce con exactitud (se sumó al listar
                # la raíz), no hace falta ningún cálculo extra por FTP.
                size = group["size_bytes"] or (usage or {}).get("size_bytes") or 0
                items.append(CleanupItem(
                    tmdb_id=(usage or {}).get("tmdb_id"),
                    name=base_name,
                    media_type="tv" if media_type == "tv" else "movie",
                    ftp_path=f"{root}/{base_name}",
                    category_name=cat.get("name", ""),
                    year=(usage or {}).get("year", ""),
                    size_bytes=size,
                    fully_watched=bool((usage or {}).get("fully_watched")),
                    play_count=(usage or {}).get("play_count", 0) or 0,
                    last_played_ts=parse_media_date((usage or {}).get("last_played")),
                    date_added_ts=parse_media_date((usage or {}).get("date_added")),
                    loose_file_paths=[f"{root}/{fn}" for fn in group["file_names"]],
                    source=(usage or {}).get("source", ""),
                    server_id=(usage or {}).get("server_id", ""),
                ))
            self._cleanup_last_ftp_size_count = ftp_size_count
            _log.info(
                "Liberar espacio: %d carpeta(s) + %d archivo(s) suelto(s), %d emparejadas por nombre, "
                "%d con tamaño calculado por FTP (lento), recorrido en %.1fs, total %.1fs",
                len(folder_list), len(loose_groups_list), matched_count, ftp_size_count,
                _time.monotonic() - t_walk, _time.monotonic() - t0)
            return items
        finally:
            own_ftp.disconnect()

    @staticmethod
    def _cleanup_sort_key_fn(key: str):
        """Función de clave de orden para self._cleanup_filtered_items
        según la columna elegida (ver _on_cleanup_header_click) --
        "tendencia" reutiliza la misma función que ya pinta la celda
        (core.trending.trending_score), para que el orden coincida
        siempre con lo que se ve."""
        if key == "tendencia":
            return lambda it: trending_score(it.play_count, it.last_played_ts, _time.time())
        if key == "tamano":
            return lambda it: it.size_bytes
        if key == "year":
            return lambda it: it.year or ""
        return lambda it: it.name.lower()

    def _cleanup_item_reason_text(self, item) -> str:
        """Resumen legible de por qué este elemento aparece en la lista --
        se muestra en la fila y se guarda tal cual en el historial de
        borrados si se elimina, para saber después el motivo, no solo el qué."""
        parts = []
        siblings = getattr(self, "_cleanup_duplicate_siblings", {}).get(id(item))
        if siblings:
            paths = "; ".join(s.ftp_path for s in siblings)
            parts.append(f"⚠ también en: {paths}")
        if item.loose_file_paths:
            n = len(item.loose_file_paths)
            parts.append(f"{n} archivo{'s' if n != 1 else ''} suelto{'s' if n != 1 else ''} (sin carpeta propia)")
        if item.date_added_ts:
            months = int((_time.time() - item.date_added_ts) / (30 * 24 * 3600))
            parts.append(f"añadida hace {months} mes(es)")
        if item.fully_watched:
            if item.last_played_ts:
                months = int((_time.time() - item.last_played_ts) / (30 * 24 * 3600))
                parts.append(f"vista, sin repetir hace {months} mes(es)")
            else:
                parts.append("vista por completo")
        elif item.play_count:
            parts.append(f"reproducida {item.play_count} vez/veces")
        else:
            parts.append("nunca vista")
        return ", ".join(parts) if parts else "sin datos de visionado"

    def _cleanup_desired_bytes(self, series_name: str) -> int | None:
        try:
            from core.slim_candidates import get_desired_bytes
            return get_desired_bytes(
                self.config_data.get("slim_desired_sizes", {}) or {}, series_name or "")
        except Exception:
            return None

    def _cleanup_ep_files(self, item) -> list | None:
        """[(nombre, tamaño, carpeta), ...] de TODO el árbol bajo
        item.ftp_path, o None si aún no se conoce (hay que listar). Los
        archivos sueltos (sin carpeta propia) no tienen tamaños por
        archivo -- también None (no expandibles)."""
        if getattr(item, "loose_file_paths", None):
            return None
        # 1) Árbol del propio análisis (LIST -R, sin coste extra).
        trees = getattr(self, "_cleanup_trees", {}) or {}
        prefix = (item.ftp_path or "").rstrip("/") + "/"
        found = []
        for folder, files in trees.items():
            if not folder:
                continue
            if folder.rstrip("/") == (item.ftp_path or "").rstrip("/") or folder.startswith(prefix):
                for name, size in (files or []):
                    try:
                        found.append((name, int(size or 0), folder))
                    except (TypeError, ValueError):
                        pass
        if found:
            return found
        # 2) Listado bajo demanda ya en caché.
        cached = (getattr(self, "_cleanup_ep_cache", {}) or {}).get(item.ftp_path)
        if cached:
            ts, files = cached
            if _time.time() - ts < getattr(self, "_cleanup_ep_cache_ttl", 600):
                return files
        return None

    def _slim_candidates_for_item(self, item, files: list | None) -> tuple[list, int | None]:
        """(candidatos, target_representativo). candidates son dicts de
        core.slim_candidates.find_slim_candidates + 'folder'."""
        from core.slim_candidates import find_slim_candidates, resolve_target_size
        if not files:
            return [], None
        is_movie = item.media_type != "tv"
        desired = self._cleanup_desired_bytes(item.name)
        ratio = self._cleanup_slim_ratio()
        # La mediana se calcula sobre los hermanos de verdad: por temporada
        # para series (no mezcla calidades distintas), global para pelis.
        cands = []
        if not is_movie:
            from core.download_quality import _parse_season_episode
            by_season: dict = {}
            for name, size, folder in files:
                try:
                    se = _parse_season_episode(name or "")
                    key = se[0] if se else 0
                except Exception:
                    key = 0
                by_season.setdefault(key, []).append((name, size, folder))
            for _season, group in by_season.items():
                sizes = [s for _n, s, _f in group if s and s > 0]
                for d in find_slim_candidates(
                        [(n, s) for n, s, _f in group], item.name, False,
                        desired, ratio, sizes_for_typical=sizes):
                    try:
                        folder = next(f for n, _s, f in group if n == d["name"])
                    except StopIteration:
                        folder = item.ftp_path
                    d["folder"] = folder
                    cands.append(d)
            cands.sort(key=lambda d: d["size"], reverse=True)
        else:
            for d in find_slim_candidates(
                    [(n, s) for n, s, _f in files], item.name, True, desired, ratio):
                d["folder"] = item.ftp_path
                cands.append(d)
        target = None
        if cands:
            # El más frecuente como representativo para el botón de serie.
            from collections import Counter
            target = Counter(d["target"] for d in cands).most_common(1)[0][0]
        else:
            # Sin candidatos, igual interesa mostrar el objetivo (p.ej.
            # para fijar el "deseado"): mediana global o techo.
            try:
                sizes = [s for _n, s, _f in files if s and s > 0]
                sample = next((n for n, s, _f in files if s and s > 0), "")
                target, _src = resolve_target_size(
                    sizes, filename=sample, is_movie=is_movie, desired_bytes=desired)
            except Exception:
                target = None
        return cands, target

    def _cleanup_files_by_owner(self, items: list) -> dict:
        """{ftp_path: [(nombre, tamaño, carpeta)]} para *items*, con los
        árboles del propio análisis (LIST -R, sin red): por cada carpeta
        del árbol se sube por sus padres hasta la candidata dueña
        (profundidad acotada, búsquedas en dict) en vez de rebanar todo
        el árbol por cada item. Si un item no aparece en los árboles, su
        entrada queda vacía y el llamador puede completarla con
        _cleanup_ep_cache (listado bajo demanda). {} si no hay árboles."""
        trees = getattr(self, "_cleanup_trees", {}) or {}
        if not trees:
            return {}
        by_path: dict = {}
        for it in items:
            if getattr(it, "loose_file_paths", None):
                continue   # sueltos: sin tamaños por archivo, no evaluables
            by_path[(it.ftp_path or "").rstrip("/")] = it
        by_series: dict = {p: [] for p in by_path}
        for folder, entries in trees.items():
            if not folder or not entries:
                continue
            cur = folder.rstrip("/")
            owner = None
            for _ in range(8):
                if cur in by_path:
                    owner = cur
                    break
                parent, _, _ = cur.rpartition("/")
                if not parent or parent == cur:
                    break
                cur = parent
            if owner is None:
                continue
            bucket = by_series[owner]
            for name, size in (entries or []):
                try:
                    bucket.append((name, int(size or 0), folder))
                except (TypeError, ValueError):
                    pass
        return by_series

    def _filter_slim_only(self, items: list) -> list:
        """Deja solo las candidatas con ≥1 capítulo/película adelgazable
        (ver core/slim_candidates.py). Usa los árboles del propio análisis
        (LIST -R, sin red); sin esos datos (caché antigua sin re-analizar)
        no se puede saber y se avisa en vez de filtrar a ciegas."""
        trees = getattr(self, "_cleanup_trees", {}) or {}
        ep_cache = getattr(self, "_cleanup_ep_cache", {}) or {}
        if not trees and not ep_cache:
            self._set_status("«Solo adelgazables» necesita un análisis reciente: "
                             "pulsa «Analizar servidor» primero.", WARNING_COLOR)
            return items
        # Mapa serie -> archivos una sola vez (ver _cleanup_files_by_owner).
        by_series = self._cleanup_files_by_owner(items)
        kept = []
        for it in items:
            files = by_series.get((it.ftp_path or "").rstrip("/"))
            if not files:
                cached = ep_cache.get(it.ftp_path)
                if cached and _time.time() - cached[0] < getattr(self, "_cleanup_ep_cache_ttl", 600):
                    files = cached[1]
            if not files:
                continue
            try:
                cands, _t = self._slim_candidates_for_item(it, files)
            except Exception:
                continue
            if cands:
                kept.append(it)
        return kept

    def _cleanup_flat_mode(self) -> bool:
        """Vista plana "Por capítulo" activa (checkbox marcado)."""
        try:
            var = getattr(self, "_cleanup_flat_var", None)
            return bool(var is not None and var.get())
        except Exception:
            return False

    def _build_cleanup_flat_rows(self, items: list) -> list | None:
        """Filas planas de gordos para la vista "Por capítulo": por cada
        candidata, sus capítulos/películas muy por encima del objetivo
        (ver core/slim_candidates.find_oversize_flat -- misma regla que
        Adelgazar; pelis con mediana por resolución). Cada fila lleva su
        item para los botones 🔍 (buscar en eMule) y ⬇ (descarga auto).
        Orden global por ratio (los más desviados primero). None si no
        hay datos (falta análisis reciente): se avisa en vez de mostrar
        una lista vacía engañosa."""
        from core.slim_candidates import find_oversize_flat
        trees = getattr(self, "_cleanup_trees", {}) or {}
        ep_cache = getattr(self, "_cleanup_ep_cache", {}) or {}
        if not trees and not ep_cache:
            self._set_status("«Por capítulo» necesita un análisis reciente: "
                             "pulsa «Analizar servidor» primero.", WARNING_COLOR)
            return None
        by_series = self._cleanup_files_by_owner(items)
        ratio = self._cleanup_slim_ratio()
        rows = []
        for it in items:
            if getattr(it, "loose_file_paths", None):
                continue
            files = by_series.get((it.ftp_path or "").rstrip("/")) or []
            if not files:
                cached = ep_cache.get(it.ftp_path)
                if cached and _time.time() - cached[0] < getattr(self, "_cleanup_ep_cache_ttl", 600):
                    files = cached[1]
            if not files:
                continue
            is_movie = it.media_type != "tv"
            try:
                cands = find_oversize_flat(
                    [(n, s) for n, s, _f in files], it.name, is_movie,
                    self._cleanup_desired_bytes(it.name), ratio)
            except Exception:
                continue
            for d in cands:
                try:
                    folder = next(f for n, _s, f in files if n == d["name"])
                except StopIteration:
                    folder = it.ftp_path
                d["item"] = it
                d["folder"] = folder
                rows.append(d)
        rows.sort(key=lambda d: d.get("ratio") or 0, reverse=True)
        return rows

    def _fetch_cleanup_ep_files(self, item):
        key = item.ftp_path
        self._cleanup_slim_loading.add(key)

        def worker():
            tree = None
            own_ftp = self._new_ftp_client()
            try:
                own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                tree = own_ftp.get_folder_tree(key)
            except Exception:
                tree = None
            finally:
                try:
                    own_ftp.disconnect()
                except Exception:
                    pass

            def _apply():
                self._cleanup_slim_loading.discard(key)
                if tree:
                    files = []
                    for folder, entries in tree.items():
                        for name, size in (entries or []):
                            try:
                                files.append((name, int(size or 0), folder))
                            except (TypeError, ValueError):
                                pass
                    self._cleanup_ep_cache[key] = (_time.time(), files)
                if key in self._cleanup_expanded:
                    self._render_cleanup_page()
            self.after(0, _apply)
        threading.Thread(target=worker, daemon=True).start()

    def _save_cleanup_desired(self, series_name: str, entry):
        try:
            mb = float((entry.get() or "").strip().replace(",", "."))
        except (ValueError, AttributeError):
            self._set_status("Tamaño deseado no válido (MB).", WARNING_COLOR)
            return
        try:
            from core.series_match import normalize_series_name
            key = normalize_series_name(series_name or "")
        except Exception:
            key = (series_name or "").strip().lower()
        if mb <= 0:
            desired = dict(self.config_data.get("slim_desired_sizes", {}) or {})
            desired.pop(key, None)
            self.config_data.set("slim_desired_sizes", desired)
            self.config_data.save()
            self._set_status(f"Deseado de '{series_name}' borrado (se vuelve a mediana).",
                             PENDING_COLOR)
        else:
            desired = dict(self.config_data.get("slim_desired_sizes", {}) or {})
            desired[key] = mb
            self.config_data.set("slim_desired_sizes", desired)
            self.config_data.save()
            self._set_status(f"Deseado de '{series_name}': {mb:g} MB.", SUCCESS_COLOR)
        self._render_cleanup_page()

    def _slim_query_for(self, item, filename: str) -> tuple[str, int | None]:
        """(query aMule, expected_year) para re-descargar *filename*.
        La query final pasa por sanitize_search_query: ()[]{} rompen el
        parser de aMule y como keywords no aportan nada."""
        try:
            from core.amule_search import sanitize_search_query as _sanq
        except Exception:
            def _sanq(q):
                return (q or "").strip()
        is_movie = item.media_type != "tv"
        if is_movie:
            year = None
            try:
                year = int(item.year) if str(item.year or "").strip().isdigit() else None
            except (TypeError, ValueError):
                year = None
            return _sanq(item.name or filename), year
        try:
            from core.download_quality import _parse_season_episode
            se = _parse_season_episode(filename or "")
        except Exception:
            se = None
        if se:
            # Con numeración reconocida, misma query que la descarga auto
            # de siempre: respeta templates por serie y castellano (ver
            # build_amule_query); si algo falla, formato "Serie NxNN".
            try:
                from core.amule_search import build_amule_query
                q = build_amule_query(
                    item.name or "", se[0], se[1],
                    templates=self.config_data.get("series_search_patterns") or {},
                    prefers_castellano=self._series_prefers_castellano(item.name or ""))
                if q and q.strip():
                    return _sanq(q), None
            except Exception:
                pass
            return _sanq(f"{item.name} {se[0]}x{se[1]:02d}"), None
        # Sin numeración reconocida (p.ej. "Doctor.Who.1108..."): el nombre
        # tal cual con puntos ROMPE el parser de aMule ("syntax error /
        # Undefined search expression") -- normalizar a términos con
        # espacios (ver filename_stem_to_search_terms).
        try:
            from core.amule_search import filename_stem_to_search_terms
            terms = filename_stem_to_search_terms(filename or "")
        except Exception:
            terms = (filename or "").rsplit(".", 1)[0] if "." in (filename or "") else (filename or "")
        q = f"{item.name} {terms}".strip()
        if "castellano" in (filename or "").lower() and "castellano" not in q.lower():
            q = f"{q} castellano"
        return _sanq(q), None

    def _record_slim_replacement(self, item, filename: str, size: int, folder, query: str):
        """Registra el reemplazo pendiente de un gordo (ver
        core/slim_pending.py): cuando la versión ligera llegue a la
        carpeta vigilada, AutoWatcher borrará el gordo del servidor
        antes de subirla. Solo se llama con descarga YA LANZADA (ok):
        si no hay ligera en camino, el gordo no se toca jamás. Sin hilos
        GUI aquí dentro (se llama también desde workers): solo dict,
        fichero y log."""
        try:
            from core.slim_pending import record as _record_pending, norm_key
            from core.download_quality import _parse_season_episode
        except Exception:
            return
        try:
            is_movie = item.media_type != "tv"
            season = episode = None
            year = ""
            if is_movie:
                year = str(getattr(item, "year", "") or "").strip()
            else:
                try:
                    se = _parse_season_episode(filename or "")
                except Exception:
                    se = None
                if not se:
                    return  # sin T/E no se puede emparejar con seguridad
                season, episode = se
            key = norm_key(getattr(item, "name", "") or "")
            if not key or not filename or not size or int(size) <= 0:
                return
            base = (folder or getattr(item, "ftp_path", "") or "").rstrip("/")
            try:
                added_by = str(self.config_data.get("app_user_name", "") or "").strip()
            except Exception:
                added_by = ""
            _record_pending({
                "media_type": "movie" if is_movie else "tv",
                "key_norm": key,
                "season": season, "episode": episode, "year": year,
                "heavy_remote_file": f"{base}/{filename}",
                "heavy_size": int(size),
                "max_light_size": int(int(size) * 0.85),
                "query": query or "",
                "added_ts": _time.time(),
                "added_by": added_by,
            })
            _log.debug("Reemplazo pendiente registrado: %s -> %s", filename, base)
        except Exception:
            pass

    def _slim_search_on_amule(self, item, filename: str):
        """Lupa 🔍 de una fila de gordo (vista plana "Por capítulo" o
        sub-fila del desplegable Adelgazar): abre la pestaña Descargas con
        la query ya puesta (misma que usaría la descarga auto: "Serie
        NxNN" o título solo para pelis) para elegir a mano. Con prueba de
        conexión previa, igual que la lupa de Episodios que faltan (ver
        _search_missing_ep_on_amule)."""
        query, year = self._slim_query_for(item, filename)
        self._search_missing_ep_on_amule(query, item.name or filename,
                                         expected_year=year,
                                         is_movie=item.media_type != "tv")

    def _resolve_cleanup_ruta_and_open(self, template: str, base_variables: dict, item,
                                       background: bool = False):
        """Mucho más simple que en Archivos/Episodios que faltan: item.ftp_path
        ya se conoce sin buscar nada (la candidata ya está subida), así que
        no hace falta ninguna resolución con caché/conexión aparte."""
        variables = dict(base_variables, ruta=item.ftp_path) if "{ruta}" in template else base_variables
        self._open_custom_link(template, variables, background)

    def _toggle_cleanup_item_favorite(self, item):
        # _apply_cleanup_filters() ya llama a _render_cleanup_results() al
        # final -- reaplicar el filtro es lo que de verdad hace falta,
        # porque marcar favorito aquí saca la fila de la lista (ver arriba,
        # salvo que "Mostrar favoritos" esté activo).
        self._toggle_favorite(item.media_type, item.tmdb_id, item.name,
                               on_done=self._apply_cleanup_filters)

    def _toggle_cleanup_item_reservation(self, item):
        self._toggle_reservation(item.media_type, item.tmdb_id, item.name, item.size_bytes,
                                  on_done=self._apply_cleanup_filters)

    def _delete_cleanup_item_worker(self, item, reason: str):
        own_ftp = self._new_ftp_client()
        ok, msg = own_ftp.connect(
            self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
            self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
            self.config_data.get("ftp_use_tls", False))
        if ok:
            # El borrado de una serie son cientos de llamadas FTP -- el
            # callback notifica la ruta que se está borrando para que la
            # barra de estado muestre progreso en vivo (ver
            # _update_cleanup_delete_progress) y no parezca congelada.
            def progress_cb(path: str):
                self.after(0, lambda p=path: self._update_cleanup_delete_progress(
                    0, 0, f"Eliminando {item.name}: {p}"))
            if item.loose_file_paths:
                # Archivos sueltos (sin carpeta propia) -- se borra cada
                # archivo del grupo (vídeo + póster/backdrop/.../nfo) uno
                # a uno, no una carpeta entera. Se detiene en el primer
                # fallo, igual que delete_folder_recursive.
                ok, msg = True, "Archivos eliminados"
                for file_path in item.loose_file_paths:
                    ok, msg = own_ftp.delete_file(file_path, progress_cb=progress_cb)
                    if not ok:
                        break
            else:
                ok, msg = own_ftp.delete_folder_recursive(item.ftp_path, progress_cb=progress_cb)
            own_ftp.disconnect()
        self._save_deletion_history_entry(
            name=item.name, ftp_path=item.ftp_path, size_bytes=item.size_bytes,
            reason=reason, status="ok" if ok else "error", error_msg="" if ok else msg)
        self.after(0, lambda: self._finish_delete_cleanup_item(item, ok, msg))

    def _release_protected_row(self, key: str, entry: dict):
        # Reutiliza _toggle_reservation tal cual: la fila solo existe
        # aquí si ya está reservada, así que siempre toma la rama de
        # liberar (comprueba dueño, sincroniza con FTP, etc. -- mismo
        # camino que el candado de Archivos/Liberar espacio).
        self._toggle_reservation(entry["media_type"], entry["tmdb_id"], entry.get("name", ""),
                                  entry.get("size_bytes", 0), on_done=self._render_protected_table)

    def _toggle_cleanup_expand(self, item):
        key = item.ftp_path
        if key in self._cleanup_expanded:
            self._cleanup_expanded.discard(key)
            self._cleanup_expanded_seasons = {
                k for k in self._cleanup_expanded_seasons if k[0] != key}
        else:
            # Archivos sueltos: no hay nada que expandir (sus tamaños ya
            # se conocen y no hay capítulos dentro).
            if getattr(item, "loose_file_paths", None):
                self._set_status("Ese elemento son archivos sueltos, no una carpeta con capítulos.",
                                 WARNING_COLOR)
                return
            self._cleanup_expanded.add(key)
            if self._cleanup_ep_files(item) is None and key not in self._cleanup_slim_loading:
                self._fetch_cleanup_ep_files(item)
        self._render_cleanup_page()

    def _toggle_cleanup_season_expand(self, item, season):
        """Acordeón de temporadas dentro de una serie expandida (igual que
        la serie con "v"/">"): al desplegar una se colapsa la otra que
        estuviera desplegada, para que nunca haya muchas filas visibles a
        la vez (límite de objetos GUI de Windows)."""
        key = (item.ftp_path, season)
        if key in self._cleanup_expanded_seasons:
            self._cleanup_expanded_seasons.discard(key)
        else:
            self._cleanup_expanded_seasons = {
                k for k in self._cleanup_expanded_seasons if k[0] != item.ftp_path}
            self._cleanup_expanded_seasons.add(key)
        self._render_cleanup_page()

    def _slim_download_one(self, item, filename: str, size: int, target: int, button=None, folder=None):
        if button is not None:
            try:
                button.configure(state="disabled", text="⏳…")
            except Exception:
                pass
        query, year = self._slim_query_for(item, filename)
        is_movie = item.media_type != "tv"
        self._set_status(f"Buscando versión ligera de '{filename[:50]}'…", PENDING_COLOR)

        def worker():
            try:
                ok, motivo, _h = self._auto_amule_download_series(
                    query, typical_size=target, max_size=int(size * 0.85),
                    is_movie=is_movie, expected_year=year)
            except Exception as e:
                ok, motivo = False, str(e)
            def _apply():
                if button is not None:
                    try:
                        button.configure(state="normal", text="🪶")
                    except Exception:
                        pass
                if ok:
                    self._record_slim_replacement(item, filename, size, folder, query)
                    self._set_status(f"Descarga ligera lanzada: {filename[:50]} "
                                     "(el gordo se borrará solo al completarse)", SUCCESS_COLOR)
                else:
                    # La query EN el mensaje: si aMule la rechaza ("syntax
                    # error..."), sin verla es imposible saber qué
                    # carácter la rompió. Detalle completo en el log.
                    _log.warning("Adelgazar %s: query %r falló: %s", filename, query, motivo)
                    self._set_status(f"Sin versión ligera para {filename[:40]}: {motivo} "
                                     f"(query: {query[:80]}) (el gordo no se toca)", WARNING_COLOR)
            self.after(0, _apply)
        threading.Thread(target=worker, daemon=True).start()

    def _slim_download_many(self, item, cands: list, label: str):
        if not cands:
            return
        if not self._make_confirm_dialog("Adelgazar (descargar ligero)",
                f"{label}: lanzar {len(cands)} descarga(s) ligera(s) en aMule?",
                "Solo descarga versiones más ligeras (<85% del peso actual). "
                "Cuando cada ligera llegue, su gordo se borra SOLO del servidor "
                "y la ligera se sube en su lugar. Si alguna descarga falla, "
                "su gordo no se toca.",
                confirm_text="Descargar", cancel_text="Cancelar").result:
            return
        is_movie = item.media_type != "tv"
        self._set_status(f"Adelgazando {label}: {len(cands)} búsqueda(s)…", PENDING_COLOR)
        # Queries en el hilo GUI (ver _slim_query_for: lee estado de la
        # app vía _series_prefers_castellano, y tkinter no es thread-safe).
        planned = []
        for d in cands:
            try:
                q, y = self._slim_query_for(item, d["name"])
            except Exception:
                continue
            planned.append((d, q, y))

        def worker():
            done, failed = 0, 0
            for d, query, year in planned:
                try:
                    ok, motivo, _h = self._auto_amule_download_series(
                        query, typical_size=d["target"], max_size=int(d["size"] * 0.85),
                        is_movie=is_movie, expected_year=year)
                except Exception as e:
                    ok, motivo = False, str(e)
                if ok:
                    self._record_slim_replacement(
                        item, d["name"], d["size"], d.get("folder") or item.ftp_path, query)
                    done += 1
                else:
                    _log.warning("Adelgazar %s: query %r falló: %s", d["name"], query, motivo)
                    failed += 1
            def _apply():
                self._set_status(f"Adelgazar {label}: {done} lanzada(s), {failed} sin ligera "
                                 "(los gordos sin ligera no se tocan).",
                                 SUCCESS_COLOR if done else WARNING_COLOR)
            self.after(0, _apply)
        threading.Thread(target=worker, daemon=True).start()

    def _rescan_cleanup_item(self, item):
        """Re-lista SOLO la carpeta de *item* en el servidor (barato, sin
        repetir "Analizar servidor") y re-aplica filtros -- para el botón
        ↻ de cada serie y para el refresco tras un reemplazo (ver
        _refresh_cleanup_item_after_replace). Si ya hay un reescaneo suyo
        en curso, se ignora el segundo clic."""
        base = (getattr(item, "ftp_path", "") or "").rstrip("/")
        if not base:
            return
        if base in getattr(self, "_cleanup_rescanning", set()):
            self._set_status(f"'{item.name}' ya se está actualizando…", PENDING_COLOR)
            return
        self._cleanup_rescanning.add(base)
        trees = getattr(self, "_cleanup_trees", {}) or {}
        for folder in [f for f in trees
                       if (f or "").rstrip("/") == base or (f or "").rstrip("/").startswith(base + "/")]:
            trees.pop(folder, None)
        try:
            (getattr(self, "_cleanup_ep_cache", {}) or {}).pop(item.ftp_path, None)
        except Exception:
            pass
        btn = (getattr(self, "_cleanup_rescan_buttons", {}) or {}).get(item.ftp_path)
        if btn is not None:
            try:
                btn.configure(state="disabled", text="…")
            except Exception:
                pass
        # Misma conexión propia que _fetch_cleanup_ep_files (nunca
        # self.ftp, que es del hilo principal).
        self._set_status(f"Actualizando '{item.name}' en Liberar espacio…", PENDING_COLOR)

        def worker():
            tree = None
            own_ftp = self._new_ftp_client()
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    _log.warning("Reescaneo de '%s': sin conexión (%s)", item.name, _msg)
                    tree = None
                else:
                    tree = own_ftp.get_folder_tree(base)
                    if not tree:
                        _log.warning("Reescaneo de '%s': árbol vacío para %s", item.name, base)
            except Exception:
                _log.warning("Reescaneo de '%s' falló", item.name, exc_info=True)
                tree = None
            finally:
                try:
                    own_ftp.disconnect()
                except Exception:
                    pass

            def _apply():
                try:
                    self._cleanup_rescanning.discard(base)
                except Exception:
                    pass
                if tree:
                    try:
                        _ep_cache = getattr(self, "_cleanup_ep_cache", None)
                        if _ep_cache is None:
                            _ep_cache = {}
                            self._cleanup_ep_cache = _ep_cache
                    except Exception:
                        _ep_cache = {}
                    _store_cleanup_retree(
                        trees, _ep_cache, item.ftp_path, tree, _time.time())
                    self._apply_cleanup_filters(preserve_page=True)
                    self._set_status(f"'{item.name}' actualizado en Liberar espacio.",
                                     SUCCESS_COLOR)
                else:
                    btn = (getattr(self, "_cleanup_rescan_buttons", {}) or {}).get(item.ftp_path)
                    if btn is not None:
                        try:
                            btn.configure(state="normal", text="↻")
                        except Exception:
                            pass
                    self._set_status(f"No se pudo actualizar '{item.name}': "
                                     "pulsa «Analizar servidor».", WARNING_COLOR)
            self.after(0, _apply)
        threading.Thread(target=worker, daemon=True).start()

    def _quota_status(self, user: str) -> tuple:
        """(used_gb, quota_gb, color, aviso) para una etiqueta de cuota de
        reservas -- aviso PROACTIVO en cuanto se supera el 90% del límite
        configurado (ver _reservation_quota_bytes), no solo al fallar un
        intento de reservar algo que ya no cabe (ver _toggle_reservation).
        Calculado en un único sitio para que Liberar espacio y Protegidos
        coincidan siempre en cuándo avisar."""
        from core.reservations import used_bytes
        quota_bytes = self._reservation_quota_bytes()
        used = used_bytes(self._reservations, user)
        quota_gb = quota_bytes / (1024 ** 3)
        ratio = used / quota_bytes if quota_bytes else 0
        if ratio >= 1.0:
            color, aviso = ERROR_COLOR, " -- cuota agotada"
        elif ratio >= 0.9:
            color, aviso = WARNING_COLOR, " ⚠ cerca del límite"
        else:
            color, aviso = PENDING_COLOR, ""
        return used / (1024 ** 3), quota_gb, color, aviso
