"""
Lógica de la pestaña Archivos sin interfaz. FilesCoreMixin junta tres partes:

  aquí                    la lista: añadir archivos (y descomprimir), la
                          reacción a los eventos de AutoWatcher, la base de
                          "ya procesados" que comparte con él, el historial
                          de subidas, la sesión, quitar/borrar y reproducir
  app_files_upload.py     la cola de subida FTP/SFTP completa y en qué carpeta
                          del servidor va cada archivo (FilesUploadMixin)
  app_files_identify.py   identificar (TMDB/libros/cómics, con apoyo de IA),
                          asignar resultados y renombrar (FilesIdentifyMixin)

Es una mixin que hereda QtAppCore (gui_qt/core_host.py), que aporta estos
"ganchos" de interfaz (nombres heredados de la antigua interfaz Tk):

  hilos / estado   after(ms, fn), _after_from_worker(fn, critical=False, timeout_s=...),
                   _set_status(texto, color)
  lista            _refresh_table(), _update_row(entry), _update_detail(...),
                   _update_status_bar(), _update_status_bar_debounced(),
                   _prompt_same_series_if_needed(entries)
  progreso FTP     _ftp_row_set(entry, estado, progreso, velocidad), _ftp_row_live(...),
                   _ftp_row_done(entry), _ftp_row_uploading(entry), _refresh_ftp_columns(),
                   _restore_ftp_row_buttons(), _refresh_ftp_space()
  diálogos         _make_overwrite_dialog(...), _make_stale_upload_dialog(...),
                   _make_series_match_dialog(...)  -> objeto con .result (hilo de la interfaz)
  otras pestañas   _remove_uploaded_episode_from_missing_list(info),
                   _remove_uploaded_movie_from_movies_list(info), _cancel_missing_episodes_scan(),
                   _refresh_cleanup_item_after_replace(...), _queue_upload_notification(...),
                   _apply_synced_activity_history(...)

y el estado que App.__init__ ya creaba (files, _upload_* , _series_folder_cache,
_ftp_dir_cache, _history_lock...) -- ver FilesCoreMixin._init_files_core_state,
que la interfaz Qt llama y Tk no necesita.
"""

import json
import os
import subprocess
import threading
import time as _time
from pathlib import Path

from core import remote_presence as rp
from core.app_files_identify import FilesIdentifyMixin
from core.app_files_upload import FilesUploadMixin
from core.appdirs import app_data_dir as _appdata_dir, is_macos, is_windows
from core.applog import get_logger
from core.file_entry import FileEntry, _dedupe_entries, _entries_from_dicts
from core.fmt import fmt_size as _fmt_size
from core.renamer import is_archive_file, is_book_file, is_video_file
from core.status_colors import ACCENT, ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class FilesCoreMixin(FilesUploadMixin, FilesIdentifyMixin):

    def _on_auto_file_event(self, path, tipo, new_name=None, progress=None, speed=None,
                             media_info=None, confidence=None, reason=None, renamed_on_disk=True,
                             size=None, remote_full=None, heavy_remote_file=None,
                             added_by=None, saved_bytes=None, is_slim=None):
        """Recibe eventos de archivo del AutoWatcher y actualiza la tabla."""
        path = str(Path(path))   # normalizar separadores antes de comparar/asignar
        from core.path_key import canon_path as _canon
        try:
            _ev_key = _canon(path)
        except Exception:
            _ev_key = path

        def _entry_key_of(e):
            try:
                return e.path_key
            except Exception:
                try:
                    return _canon(e.path)
                except Exception:
                    return getattr(e, "path", "")

        def _update():
            # Buscar entrada existente por CLAVE canónica (ver
            # core/path_key.py): el path tal como lo ve os.walk puede
            # traer otra caja que el de la fila restaurada o añadida a
            # mano del MISMO archivo — comparar strings tal cual la
            # duplicaba en cada reinicio.
            entry = next((e for e in self.files if _entry_key_of(e) == _ev_key), None)
            if entry is None:
                # El archivo ya fue renombrado en un ciclo anterior (p.ej. la
                # subida FTP falló y AutoWatcher lo reprocesa): el path en disco
                # cambió pero sigue siendo el mismo archivo — buscarlo por el
                # nombre con el que quedó tras el renombrado para no duplicar la fila.
                fname = Path(path).name
                try:
                    import os as _os
                    _fname_key = _os.path.normcase(fname)
                    entry = next((e for e in self.files
                                  if _os.path.normcase(e.new_name or "") == _fname_key
                                  and e.status != "subido"), None)
                except Exception:
                    entry = next((e for e in self.files
                                  if e.new_name == fname and e.status != "subido"), None)
                if entry is not None:
                    entry.path = path
            if entry is not None and is_slim:
                # El watcher confirma que viene de un adelgazamiento: la
                # fila lo mostrará como "Adelgazando" (ver _file_status_text).
                try:
                    entry.is_slim = True
                except Exception:
                    pass

            if tipo == "start":
                if entry is None:
                    entry = FileEntry(path)
                    entry.status = "auto"
                    self.files.append(entry)
                    self._refresh_table()
                else:
                    entry.status    = "auto"
                    entry.error_msg = ""   # nuevo intento -- descartar el motivo anterior
                    self._update_row(entry)
                self._autosave_session()
                return

            if tipo == "slim_replaced":
                # El watcher sustituyó un gordo por su ligera (ver
                # core/slim_pending.py): la foto de Liberar espacio quedó
                # obsoleta para esa serie -- re-listar SOLO su carpeta.
                # Va antes del "entry is None" de abajo porque no necesita
                # fila en Archivos. Además se suma el ahorro al ranking de
                # adelgazadores (quien lo lanzó sale del pendiente; si el
                # pendiente era antiguo y no trae autor, cae al nombre
                # actual de Ajustes).
                self._refresh_cleanup_item_after_replace(heavy_remote_file)
                try:
                    _slim_person = (added_by or "") or str(
                        self.config_data.get("app_user_name", "") or "").strip()
                    _slim_saved = int(saved_bytes or 0)
                except (TypeError, ValueError):
                    _slim_person, _slim_saved = "", 0
                if _slim_person.strip() and _slim_saved > 0:
                    try:
                        self._push_slim_stat_to_ftp(_slim_person.strip(), _slim_saved, _time.time())
                    except Exception:
                        pass
                return

            if entry is None:
                # Evento huérfano: el archivo no tiene fila en Archivos
                # (típico en adelgazamientos: la ligera cae sola vía aMule a
                # la vigilada sin pasar por +Archivos, o la fila se filtró
                # por otra página). Antes se descartaba en silencio y la
                # subida ocurría igual en el servidor sin dejar rastro en la
                # GUI (real: Fargo T4 "sube pero no cambia de Renombrado").
                # Se registra para diagnóstico, y un "uploaded" huérfano
                # guarda igualmente su entrada en el Historial.
                # Una vez por archivo y tipo: "uploading" llega en cada
                # tramo de progreso, y una sola subida dejaba cientos de
                # líneas iguales (264 con un Dragon Ball Daima).
                _orphan_key = (path, tipo)
                if _orphan_key not in self._orphan_events_logged:
                    self._orphan_events_logged.add(_orphan_key)
                    _log.warning("Evento auto '%s' sin fila para %s (nombre %s)",
                                 tipo, path, new_name)
                if tipo == "uploaded":
                    try:
                        self._save_history_entry(Path(path).name, remote_full or path,
                                                 "ok", size or 0, local_path=path)
                    except Exception:
                        pass
                return

            if tipo == "renamed":
                entry.new_name = new_name or ""
                entry.status   = "renombrado"
                if media_info is not None:
                    entry.media_info = media_info
                if confidence is not None:
                    entry.confidence = confidence
                # El renombrado es en la misma carpeta: el archivo real ahora
                # vive en <misma carpeta>/<new_name>, no en la ruta original
                # con la que se detectó. Sin esto, cualquier acción manual
                # posterior sobre esta entrada (subir, ver detalles) usaba una
                # ruta que ya no existe en disco ("Archivo no encontrado").
                # Si "Renombrar en origen" está desactivado, el archivo real
                # NO se tocó — entry.path debe seguir apuntando al original.
                if new_name and renamed_on_disk:
                    entry.path = str(Path(path).parent / new_name)
                self._update_row(entry)
                if self._selected_entry is entry:
                    self._update_detail(entry)
                self._autosave_session()

            elif tipo == "queued":
                # Ya se resolvió categoría/duplicado/espacio, pero todavía
                # no hay turno de "Subidas simultáneas" -- si varios
                # archivos se detectaron a la vez, todos pasan por aquí casi
                # al mismo tiempo, y solo uno de ellos conseguirá turno real
                # (ver "uploading" más abajo, que si eso sí pisa este
                # estado en cuanto empieza a transferir de verdad).
                entry.status       = "en_cola"
                entry.new_name     = new_name or entry.new_name
                self._update_row(entry)
                self._autosave_session()

            elif tipo == "uploading":
                # Llega en cada tramo de progreso: solo el primero (cambio
                # de estado/nombre) repinta la fila entera; el resto solo la
                # barra. Antes cada tick hacía _update_row y, con el archivo
                # seleccionado, recargaba la ficha lateral sin parar.
                changed = (entry.status != "subiendo"
                           or (new_name and new_name != entry.new_name))
                entry.status       = "subiendo"
                entry.ftp_progress = progress or 0.0
                entry.ftp_speed    = speed or 0.0
                entry.new_name     = new_name or entry.new_name
                if changed:
                    self._update_row(entry)
                self._ftp_row_live(entry, entry.ftp_progress, entry.ftp_speed)

            elif tipo == "uploaded":
                entry.status       = "subido"
                entry.ftp_progress = 1.0
                entry.ftp_speed    = 0.0
                entry.new_name     = new_name or entry.new_name
                self._update_row(entry)
                self._ftp_row_live(entry, 1.0, 0.0)
                # Notificación de escritorio -- agrupada, ver
                # _queue_upload_notification (no una por archivo).
                fname = entry.new_name or entry.name
                self._queue_upload_notification(fname)
                # Guardar en historial (modo automático) -- size viene del
                # propio AutoWatcher (ya lo calculó antes de subir, ver
                # core/auto_watcher.py), NO recalculado aquí: en este punto
                # el archivo pudo moverse/borrarse ya (mover a "procesados"
                # o eliminar tras subir), así que Path(path).stat() podría
                # fallar o dar un tamaño distinto al que de verdad se subió.
                # remote_full lo manda AutoWatcher; antes se guardaba aquí
                # la ruta LOCAL en el campo "remote", con lo que el historial
                # no sabía dónde había quedado el archivo en el servidor.
                # El "or path" solo cubre a un AutoWatcher que no lo envíe.
                # filename = entry.name (el ORIGINAL, nunca se sobreescribe
                # al renombrar -- ver FileEntry) para que el historial
                # muestre el nombre con el que llegó el archivo, no el que
                # se le puso al renombrar; el nombre final sigue visible en
                # la columna "destino" (remote incluye el nombre remoto).
                self._save_history_entry(entry.name, remote_full or path, "ok", size or 0,
                                          local_path=path)
                self._remove_uploaded_episode_from_missing_list(entry.media_info)
                self._remove_uploaded_movie_from_movies_list(entry.media_info)
                self._refresh_ftp_space()
                # Fila final ("subido") guardada YA: el post-proceso puede
                # borrar el original justo después, y un apagón antes del
                # cierre se llevaría la fila (real: Snoopy 12/10).
                self._autosave_session(force=True)

            elif tipo == "skip":
                entry.status    = "omitido"
                entry.new_name  = new_name or entry.new_name
                entry.error_msg = reason or ""
                self._update_row(entry)
                if self._selected_entry is entry:
                    self._update_detail(entry)
                self._autosave_session()

            elif tipo == "error":
                entry.status    = "error"
                entry.error_msg = reason or ""
                entry.new_name  = new_name or entry.new_name
                if media_info is not None:
                    entry.media_info = media_info
                if confidence is not None:
                    entry.confidence = confidence
                self._update_row(entry)
                if self._selected_entry is entry:
                    self._update_detail(entry)
                self._autosave_session()

        # Todo menos los ticks de progreso ("uploading", que se repiten
        # solos al siguiente chunk) es crítico: si se descarta, la fila
        # queda desincronizada para siempre (p.ej. "Renombrado" con el
        # archivo ya subido y borrado). El PRIMER tick (progress 0.0) es
        # la transición de estado renombrado/en_cola -> subiendo, no un
        # mero avance de barra: también es crítico (real: Fargo T4 se
        # quedaba en "Renombrado" con la barra a 0 aunque subía).
        _first_tick = (tipo == "uploading" and (progress or 0.0) <= 0)
        self._after_from_worker(_update, critical=(tipo != "uploading" or _first_tick))

    def _add_paths_extracting_archives(self, paths: list, same_series_prompt: bool = False):
        """Punto de entrada común para _add_files/_on_drop/_add_folder --
        separa archivos comprimidos (.zip/.7z/.rar/.tar y variantes) del
        resto y los descomprime antes de añadirlos (ver
        core/archive_extract.py). A diferencia del Modo Automático, esto se
        hace SIEMPRE aquí, sin depender de "auto_extract_archives" en
        Ajustes -- es una acción explícita del usuario, presente
        confirmándola, no la vigilancia desatendida que ese interruptor
        protege."""
        archives = [p for p in paths if is_archive_file(p)]
        others   = [p for p in paths if p not in archives]
        if not archives:
            self._add_entries(others, same_series_prompt=same_series_prompt)
            return
        self._set_status(f"Descomprimiendo {len(archives)} archivo(s)...", WARNING_COLOR)
        threading.Thread(target=self._extract_archives_worker,
                          args=(archives, others, same_series_prompt), daemon=True).start()

    def _extract_archives_worker(self, archives, others, same_series_prompt=False):
        """Descomprime *archives* (uno por uno, incluidos los anidados que
        aparezcan dentro -- a diferencia del Modo Automático, que los deja
        para el siguiente ciclo de escaneo, aquí no hay "siguiente ciclo":
        es una sola acción que debe resolverlos todos ya) y junta el
        resultado con *others* antes de añadirlo a la tabla. El archivo
        comprimido original recibe la misma "Acción tras procesar" que ya
        se usa para la subida manual (ver _apply_manual_action_to_path)."""
        from core.archive_extract import extract_archive
        resolved = list(others)
        failed = []
        pending = list(archives)
        while pending:
            archive_path = pending.pop(0)
            ok, dest_or_msg = extract_archive(archive_path)
            if not ok:
                failed.append((Path(archive_path).name, dest_or_msg))
                _log.warning("No se pudo descomprimir %s: %s", archive_path, dest_or_msg)
                continue
            dest_dir = Path(dest_or_msg)
            for f in sorted(dest_dir.rglob("*")):
                if not f.is_file():
                    continue
                fs = str(f)
                if is_archive_file(fs):
                    pending.append(fs)
                elif is_video_file(fs) or is_book_file(fs):
                    resolved.append(fs)
            self._apply_manual_action_to_path(Path(archive_path))

        def _finish(res=resolved, fail=failed):
            if fail:
                names = ", ".join(n for n, _ in fail)
                self._set_status(f"No se pudo descomprimir: {names}", ERROR_COLOR)
            self._add_entries(res, same_series_prompt=same_series_prompt)
        self.after(0, _finish)

    def _add_entries(self, paths, same_series_prompt: bool = False):
        # Identidad por clave canónica (ver core/path_key.py): el path
        # crudo tal como llega (diálogo, arrastre, rglob) puede traer
        # otra caja u otros separadores que el de una fila ya existente
        # del MISMO archivo — comparar strings tal cual duplicaba filas.
        from core.path_key import canon_path
        existing = set()
        for e in self.files:
            try:
                existing.add(e.path_key)
            except Exception:
                try:
                    existing.add(canon_path(e.path))
                except Exception:
                    pass
        added = []
        for p in paths:
            try:
                key = canon_path(p)
            except Exception:
                key = p
            if key not in existing:
                existing.add(key)
                entry = FileEntry(p)
                self.files.append(entry)
                added.append(entry)
        self._refresh_table()
        self._autosave_session()
        if not added:
            return
        if same_series_prompt:
            book_comic_added = [e for e in added if e.is_book]
            video_added      = [e for e in added if not e.is_book]
            self._prompt_same_series_if_needed(book_comic_added, video_added)
        else:
            self.after(50, self._search_new_entries, added)

    @staticmethod
    def _db_key(original_path: str) -> str:
        """Clave canónica en auto_processed.json (ver core/path_key.py):
        la misma que usa AutoWatcher para sus marcas, para que caja o
        separadores distintos no rompan la búsqueda."""
        try:
            from core.path_key import canon_path
            return canon_path(original_path)
        except Exception:
            return original_path or ""

    def _slim_pending_for_path(self, original_path: str):
        """Pendiente de adelgazamiento que casa con el archivo local
        *original_path*, o None (ver core/slim_pending.find_match: exige
        identidad y que pese <= 85% del gordo)."""
        try:
            from core.slim_pending import load_pending, find_match
            from core.api_client import detect_episode
            import re as _re
            try:
                size = Path(original_path).stat().st_size
            except OSError:
                return None
            name = Path(original_path).name
            det = detect_episode(name) or {}
            season, episode = det.get("season"), det.get("episode")
            if season and episode:
                return find_match(load_pending(), "tv", det.get("title", ""),
                                  season, episode, "", size)
            m = _re.search(r"\b(?:19|20)\d{2}\b", name)
            return find_match(load_pending(), "movie", det.get("title", "") or name,
                              None, None, m.group(0) if m else "", size)
        except Exception:
            return None

    def _auto_processed_status(self, original_path: str) -> str:
        """Estado registrado en auto_processed.json para original_path, o "" si no hay nada."""
        import json as _json
        try:
            from core.auto_watcher import _processed_db_path, _DB_LOCK
            with _DB_LOCK:
                p = _processed_db_path()
                if not p.exists():
                    return ""
                db = _json.loads(p.read_text(encoding="utf-8"))
            return db.get(self._db_key(original_path), {}).get("status", "")
        except Exception:
            return ""

    def _mark_auto_processed(self, original_path: str, status: str, new_name: str = ""):
        """Marca un archivo como procesado en auto_processed.json para que el watcher lo ignore.
        Usa _DB_LOCK (compartido con AutoWatcher, ver core/auto_watcher.py::
        _save_entry) para que este leer-modificar-escribir no se pise con
        otra subida manual en paralelo ni con el modo automático escribiendo
        a la vez -- sin el lock, dos hilos podían basar su escritura en la
        misma lectura desactualizada y uno de los dos perdía su marca
        "subido", haciendo que ese archivo "reapareciera" como nuevo.

        Al pasar a "subiendo" o a "en_cola_manual" se guarda el estado
        previo (p.ej. "identificado_manual") como "prev_status" en la
        propia entrada -- ver _unmark_auto_processed/
        _cleanup_stale_uploading_marks, que lo usan para restaurar en vez
        de borrar del todo si la subida se interrumpe (la app se cierra a
        medias) sin llegar a "subido". "en_cola_manual" como PREVIO no se
        restaura nunca (reserva transitoria, ver _restore_or_delete_entry).

        Excepción: "renombrado"/"identificado_manual" de un ligero con
        pendiente de adelgazamiento NO se marca -- esa protección ("el
        usuario lo lleva, no tocar") dejaría el reemplazo colgado para
        siempre, con la fila en "Renombrado" y el pendiente sin completar
        jamás (ver _slim_pending_for_path)."""
        import json as _json, time as _t
        try:
            if status in ("renombrado", "identificado_manual") \
                    and self._slim_pending_for_path(original_path) is not None:
                try:
                    _log.info("Adelgazamiento: %s tiene pendiente, no se protege del automático",
                              Path(original_path).name)
                except Exception:
                    pass
                return
            from core.auto_watcher import _processed_db_path, _DB_LOCK
            key = self._db_key(original_path)
            with _DB_LOCK:
                p = _processed_db_path()
                db = {}
                if p.exists():
                    try:
                        db = _json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                entry = {"status": status, "new_name": new_name, "ts": _t.time()}
                if status in ("subiendo", "en_cola_manual"):
                    prev = db.get(key, {})
                    prev_status = prev.get("status", "")
                    if prev_status and prev_status not in ("subiendo", "en_cola_manual"):
                        entry["prev_status"]   = prev_status
                        entry["prev_new_name"] = prev.get("new_name", "")
                db[key] = entry
                p.write_text(_json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _unmark_auto_processed(self, original_path: str):
        """Deshace la marca "subiendo" de auto_processed.json para este
        archivo tras una subida manual que no llega a completarse --  si
        había un estado anterior protegido (p.ej. "identificado_manual",
        guardado en "prev_status" por _mark_auto_processed), se restaura en
        vez de borrar la entrada entera: si no, AutoWatcher se olvidaría de
        que el archivo ya se había identificado a mano y lo trataría como
        nuevo. Solo se borra del todo si no había nada protegido antes (el
        archivo era realmente nuevo)."""
        import json as _json
        try:
            from core.auto_watcher import _processed_db_path, _DB_LOCK
            with _DB_LOCK:
                p = _processed_db_path()
                if not p.exists():
                    return
                db = _json.loads(p.read_text(encoding="utf-8"))
                key = self._db_key(original_path)
                if key in db:
                    self._restore_or_delete_entry(db, key)
                    p.write_text(_json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _restore_or_delete_entry(db: dict, key: str):
        """Restaura la entrada *key* de *db* a su "prev_status" (si es uno
        real) o la borra del todo -- lógica compartida entre
        _unmark_auto_processed y _cleanup_stale_uploading_marks. Muta *db*
        in-place, no escribe a disco. "en_cola_manual" no cuenta como
        previo restaurable en ningún caso: es una reserva transitoria de
        tanda (ver _reserve_manual_entries) y restaurarla dejaría el
        archivo vetado al automático para siempre."""
        entry = db.get(key, {})
        prev_status = entry.get("prev_status", "")
        if prev_status and prev_status != "en_cola_manual":
            db[key] = {
                "status":   prev_status,
                "new_name": entry.get("prev_new_name", ""),
                "ts":       entry.get("ts", 0),
            }
        else:
            del db[key]

    def _sort_files_by_episode(self):
        """Reordena la lista por temporada y episodio (series primero, luego películas por título)."""
        def _sort_key(e):
            det = e.detected or {}
            season  = det.get("season")  or 0
            episode = det.get("episode") or 0
            # Si tiene temporada/episodio → serie: orden numérico
            # Si no → película u otro: orden alfabético al final
            is_series = bool(season or episode)
            return (0 if is_series else 1, season, episode, e.name.lower())
        self.files.sort(key=_sort_key)
        self._refresh_table()

    def _history_path(self) -> Path:
        return _appdata_dir() / "upload_history.json"

    def _load_history(self) -> list:
        try:
            p = self._history_path()
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _stale_remote_from_history(self, local_path: str, filename: str, new_remote: str) -> str:
        """Delegación a core.remote_presence.stale_remote_from_history (ver
        allí la lógica y su docstring)."""
        return rp.stale_remote_from_history(self._load_history(), local_path, filename, new_remote)

    def _save_history_entry(self, filename: str, remote: str, status: str, size: int, error_msg: str = "",
                             local_path: str = ""):
        entry = {
            "ts":        _time.time(),
            "filename":  filename,
            "remote":    remote,
            "status":    status,
            "size":      size,
            "error_msg": error_msg,
            # Ruta local en el momento de la subida -- solo para poder
            # reintentar directamente desde Historial sin tener que
            # volver a Archivos (ver _retry_history_upload). Vacía en
            # registros de antes de este campo; el botón "Reintentar"
            # se deshabilita en ese caso.
            "local_path": local_path,
            # Quién lo subió -- para el historial de actividad compartido
            # entre clientes (ver _push_activity_entry_to_ftp), y también
            # útil ya en local si en el futuro hace falta distinguir.
            "person":    self.config_data.get("app_user_name", ""),
        }
        with self._history_lock:
            history = self._load_history()
            history.append(entry)
            # Mantener solo los últimos 500 registros
            if len(history) > 500:
                history = history[-500:]
            try:
                from core.atomic_write import write_json_atomic
                write_json_atomic(self._history_path(), history)
            except Exception as e:
                _log.warning("Historial: no se pudo guardar %s: %s", self._history_path(), e)
            self._history_dirty = True
        # Siempre notificar la subida al historial de actividad compartido
        self._push_activity_entry_to_ftp(entry, "subida")
        if status == "ok":
            # Ranking de subidas (ver core/upload_stats.py) -- solo
            # subidas que de verdad terminaron bien, nunca errores.
            # Este hilo es daemon: si la app se cierra, se pierde, pero
            # mientras tanto se intenta actualizar el ranking global.
            self._push_upload_stat_to_ftp(entry["person"], size, entry["ts"])
            # Recuento/tamaño por categoría (ver core/category_stats.py) --
            # mismo criterio, solo subidas OK.
            # Si remote no encaja con ninguna categoría configurada, no
            # hace nada y se deferirá al bootstrap al entrar en Stats.
            self._push_category_stat_addition_to_ftp(remote, size)
            # Ranking de subidores DE ESA CATEGORÍA (ver
            # core/category_upload_stats.py) -- un panel "Top subidores"
            # por categoría en Estadísticas, aparte del ranking global.
            # Igual que el anterior, usa la ruta remota real para resolver
            # la categoría y acumula aunque el daemon pueda morir.
            self._push_category_upload_stat_to_ftp(remote, entry["person"], size, entry["ts"])

    def _preview_remote_path(self, entry) -> str:
        """Ruta FTP de destino para *entry* -- si el usuario la ha fijado a
        mano (columna "Destino" de la tabla), esa gana; si no, se prueba
        primero si ya hay carpeta en OTRA categoría (usando solo lo que ya
        esté en caché -- ver _find_category_with_existing_folder, y
        _prefetch_ftp_category_dirs para cuándo se rellena esa caché) y,
        si no, la que le correspondería por categoría/género. No se
        conecta al servidor por su cuenta ni bloquea la interfaz -- si
        hace falta listar una carpeta que no está ya en caché, eso solo
        pasa de verdad al subir (ver _upload_entry_with)."""
        filename = entry.new_name or Path(entry.path).name
        if entry.remote_dir_override:
            return f"{entry.remote_dir_override.rstrip('/')}/{filename}"
        info = entry.media_info
        if not info:
            return ""
        category = self._category_for(info)
        serie_name = info.title
        if info.media_type == "tv":
            existing_category, existing_name = self._find_category_with_existing_folder(
                self.ftp, info, use_cache_only=True)
            if existing_category:
                category = existing_category
                serie_name = existing_name   # reutilizar el nombre EXACTO ya existente, no el título tal cual de TMDB
        if not category:
            return ""
        root = category.get("root", "")
        if not root:
            return ""
        full_tpl = root.rstrip("/") + "/" + category.get("template", "{serie}/")
        remote_dir = self.ftp.build_remote_path(
            full_tpl, serie_name, info.season, info.year, info.media_type)
        return f"{remote_dir.rstrip('/')}/{filename}"

    def _on_auto_event(self, tipo, msg):
        colors = {"info": ACCENT, "ok": SUCCESS_COLOR,
                  "skip": WARNING_COLOR, "error": ERROR_COLOR}
        self._after_from_worker(lambda: self._set_status(msg, colors.get(tipo, ACCENT)))

    def _play_file(self, entry):
        """▶: si el archivo ya está subido, se abre en Jellyfin (su ficha,
        para pulsar play allí); si no, se abre el archivo local con la
        aplicación predeterminada, como hasta ahora."""
        if getattr(entry, "status", "") == "subido" and self._jellyfin_play_available(entry):
            self._set_status("Buscando en Jellyfin...", PENDING_COLOR)
            threading.Thread(target=self._play_in_jellyfin_worker, args=(entry,),
                             daemon=True).start()
            return
        self._play_local_file(entry)

    def _jellyfin_play_available(self, entry) -> bool:
        """Hay a dónde ir a reproducir: identificado como serie/película
        (los libros no están en Jellyfin) y Jellyfin activado con host y
        key. Sin esto se abre el local como hasta ahora."""
        info = getattr(entry, "media_info", None)
        if info is None or getattr(info, "media_type", "") not in ("tv", "movie"):
            return False
        try:
            if not int(getattr(info, "tmdb_id", 0) or 0):
                return False
        except (TypeError, ValueError):
            return False
        try:
            cfg = self.config_data
            return bool(cfg.get("jellyfin_enabled") and (cfg.get("jellyfin_host", "") or "").strip()
                        and (cfg.get("jellyfin_api_key", "") or "").strip())
        except Exception:
            return False

    def _play_in_jellyfin_worker(self, entry):
        """Localiza el item en Jellyfin (serie por tmdb_id + capítulo por
        temporada/episodio; película directa) y abre su ficha en el
        navegador para pulsar play allí. A propósito NO se abre el stream
        directo (/Videos/.../stream): según el formato el navegador lo
        descarga (stream.avi) o lo pone en su reproductor propio en vez
        de en Jellyfin. Si no está (aún no reindexó la subida, o falló la
        red) se cae al archivo local si existe."""
        info = entry.media_info
        try:
            cfg = self.config_data
            host = (cfg.get("jellyfin_host", "") or "").rstrip("/")
            key = cfg.get("jellyfin_api_key", "") or ""
            if not (cfg.get("jellyfin_enabled") and host and key):
                raise RuntimeError("Jellyfin no configurado")
            from core.media_server_refresh import (find_jellyfin_episode_id,
                                                   find_jellyfin_item_by_tmdb_id)
            found = find_jellyfin_item_by_tmdb_id(host, key, int(info.tmdb_id), info.media_type)
            item_id = None
            if found:
                if info.media_type == "tv" and info.season and info.episode:
                    item_id = find_jellyfin_episode_id(host, key, found["id"],
                                                       info.season, info.episode)
                else:
                    item_id = found["id"]
            if not item_id:
                raise LookupError("no está en Jellyfin todavía")
            import webbrowser
            # Misma URL que "Abrir en Jellyfin": la ficha del item, no el
            # stream directo (ver arriba).
            webbrowser.open(f"{host}/web/#/details?id={item_id}")
            title = info.title or entry.name
            self.after(0, lambda: self._set_status(f"Abierto en Jellyfin: {title}",
                                                   SUCCESS_COLOR))
        except Exception as e:
            _log.warning("Abrir en Jellyfin falló para %r: %s", entry.name, e)
            self.after(0, lambda: self._play_local_or_warn(entry))

    def _play_local_or_warn(self, entry):
        """Rescate cuando Jellyfin no lo tiene: local si existe, si no el
        motivo (la subida aún no reindexó y el original ya no está)."""
        try:
            if Path(entry.path).is_file():
                self._play_local_file(entry)
                self._set_status("Jellyfin aún no lo tiene: abriendo el archivo local",
                                 WARNING_COLOR)
                return
        except OSError:
            pass
        self._set_status("Aún no está en Jellyfin (¿reindexando?) y el local ya no existe",
                         ERROR_COLOR)

    def _play_local_file(self, entry):
        """Abre el archivo con la aplicación predeterminada del sistema."""
        try:
            if is_windows():
                os.startfile(entry.path)   # no existe en macOS/Linux
            elif is_macos():
                subprocess.run(["open", entry.path], check=True)
            else:
                subprocess.run(["xdg-open", entry.path], check=True)
        except Exception as e:
            self._set_status(f"No se pudo abrir: {e}", ERROR_COLOR)

    def _open_containing_folder(self, entry):
        """Abre la carpeta que contiene el archivo en el explorador del
        sistema -- en Windows/macOS, además, deja el archivo ya
        seleccionado dentro de esa carpeta (no solo abierta)."""
        try:
            if is_windows():
                # "/select," pegado sin espacio a la ruta es la sintaxis que
                # entiende explorer.exe para abrir la carpeta con el archivo
                # ya resaltado, en vez de solo abrir la carpeta a secas.
                subprocess.run(["explorer", "/select,", entry.path])
            elif is_macos():
                subprocess.run(["open", "-R", entry.path], check=True)
            else:
                subprocess.run(["xdg-open", str(Path(entry.path).parent)], check=True)
        except Exception as e:
            self._set_status(f"No se pudo abrir la carpeta: {e}", ERROR_COLOR)

    @staticmethod
    def _entry_series_tmdb_id(entry):
        """El tmdb_id de la SERIE de este archivo, o None si no procede.

        El autocompletado busca capítulos que falten, así que solo tiene
        sentido en series ya identificadas: una película no tiene capítulos y
        un archivo sin identificar todavía no se sabe de qué serie es."""
        info = getattr(entry, "media_info", None)
        if info is None or getattr(info, "media_type", "") not in ("tv", "anime"):
            return None
        tid = getattr(info, "tmdb_id", None)
        return int(tid) if tid else None

    def _file_size_text(self, entry) -> str:
        """Peso del archivo en disco (columna "Peso", junto a "Vel.") --
        leído del filesystem, no de un dato guardado al identificar el
        archivo. El modo automático puede mover el archivo a "procesados/"
        o borrarlo justo después de subirlo (según "Acción tras subir" en
        Ajustes) sin avisar a esta fila -- entry.path se queda apuntando a
        una ruta que ya no existe, y sin este caché la columna se quedaba
        en blanco justo cuando la subida terminaba bien, como si algo
        hubiera fallado. Se guarda el último tamaño leído con éxito en la
        propia entrada y se reutiliza mientras el archivo no esté
        disponible -- solo vacío si nunca se pudo leer ni una vez."""
        try:
            size = Path(entry.path).stat().st_size
            entry._last_known_size_text = _fmt_size(size)
            entry._last_known_size_bytes = size
        except OSError:
            pass
        return entry._last_known_size_text

    def _entry_is_favorite(self, entry) -> bool:
        if not entry.media_info:
            return False
        return self._is_favorite(entry.media_info.media_type, entry.media_info.tmdb_id)

    def _fav_symbol(self, entry) -> str:
        return "★" if self._entry_is_favorite(entry) else "☆"

    def _toggle_entry_favorite(self, entry):
        if not entry.media_info:
            return
        mi = entry.media_info
        self._toggle_favorite(mi.media_type, mi.tmdb_id, mi.title,
                               on_done=lambda e=entry: self._update_row(e))

    def _entry_is_reserved(self, entry) -> bool:
        if not entry.media_info:
            return False
        return self._is_reserved(entry.media_info.media_type, entry.media_info.tmdb_id)

    def _lock_symbol(self, entry) -> str:
        return "🔒" if self._entry_is_reserved(entry) else "🔓"

    def _toggle_entry_reservation(self, entry):
        # Deliberadamente restringido a archivos ya subidos (ver el botón
        # en _refresh_table): antes de eso no hay nada en el servidor que
        # proteger. Reservar un episodio protege la serie ENTERA en
        # Liberar espacio (misma clave media_type+tmdb_id) -- así que la
        # cuota debe cargarse con el tamaño real de la serie/película
        # completa, no el de este único archivo, o un usuario podría
        # protegerse una serie de 80GB pagando solo el 1.2GB de un
        # episodio (ver _best_known_size_bytes).
        if not entry.media_info or entry.status != "subido":
            return
        mi = entry.media_info
        size_bytes = self._best_known_size_bytes(mi.media_type, mi.tmdb_id, self._file_size_bytes(entry))
        self._toggle_reservation(mi.media_type, mi.tmdb_id, mi.title, size_bytes,
                                  on_done=lambda e=entry: self._update_row(e))

    def _best_known_size_bytes(self, media_type: str, tmdb_id: int, fallback_bytes: int) -> int:
        """Mejor estimación del tamaño REAL en el servidor de una serie/
        película completa, para cargar la cuota de reservas con precisión
        al reservar desde Archivos (un solo archivo) -- si "Liberar
        espacio" ya la tiene en su caché de último análisis
        (self._cleanup_raw_items, cargada al arrancar desde
        cleanup_candidates_cache.json aunque no se haya visitado esa
        pestaña en esta sesión), se usa ESE tamaño de carpeta completa en
        vez de fallback_bytes (el de un único archivo)."""
        for it in getattr(self, "_cleanup_raw_items", []):
            if it.media_type == media_type and it.tmdb_id == tmdb_id:
                return it.size_bytes
        return fallback_bytes

    def _file_size_bytes(self, entry) -> int:
        try:
            return Path(entry.path).stat().st_size
        except OSError:
            return 0

    def _remove_entry(self, entry):
        """La ✕ de una fila: pregunta qué hacer y actúa en consecuencia.

        Siempre pregunta, aunque la única opción posible sea quitarla de la
        lista, para que la ✕ nunca borre nada por sorpresa."""
        historial = self._load_history()
        nombre    = entry.new_name or entry.name
        remote    = rp.remote_path_from_history(historial, entry.path, nombre)

        # Registro viejo con el campo "remote" estropeado (guardaba la ruta
        # local): consta subido pero no se sabe dónde quedó. Solo en ese
        # caso hay que preguntarle al servidor, y eso va en un hilo -- si
        # está caído, hacerlo aquí congelaría la app hasta el timeout.
        if not remote and rp.was_uploaded_according_to_history(historial, entry.path, nombre):
            self._set_status(f"Buscando en el servidor: {nombre}", PENDING_COLOR)
            threading.Thread(target=self._lookup_remote_then_ask,
                              args=(entry, nombre), daemon=True).start()
            return

        self._ask_removal(entry, remote)

    def _lookup_remote_then_ask(self, entry, nombre: str):
        """Hilo: busca la ruta real en el servidor y luego abre el diálogo."""
        try:
            remote = self._ftp_lookup_uploaded_file(entry, nombre)
        except Exception:
            _log.warning("No se pudo buscar en el servidor: %s", nombre, exc_info=True)
            remote = ""
        self.after(0, lambda: self._ask_removal(entry, remote))

    def _ftp_lookup_uploaded_file(self, entry, nombre: str) -> str:
        """Ruta completa del archivo en el servidor, o "" si no aparece.

        Reutiliza _existing_ftp_path_for_info para dar con la carpeta de la
        serie/película (la misma lógica que decide dónde subirla) y lista
        solo esa carpeta -- no se recorre el servidor entero."""
        info = getattr(entry, "media_info", None)
        if info is None:
            return ""
        with self._ftp_cmd_lock:
            carpeta = self._existing_ftp_path_for_info(self.ftp, info, use_cache_only=False)
            if not carpeta:
                return ""
            ficheros = self.ftp.list_files(carpeta) or []
        for f in ficheros:
            if Path(f).name == nombre:
                return rp.join_remote(carpeta, Path(f).name)
        return ""

    def _delete_entry_remote_then_local(self, entry, local_path: str, remote_path: str):
        """Hilo: borra primero en el servidor y, solo si eso va bien, en
        disco. En ese orden a propósito -- si fallara el remoto tras haber
        borrado el local, el archivo del servidor se quedaría sin ninguna
        copia local desde la que reintentar."""
        # Si estaba subiendo, cancelar primero para no borrar un archivo a medio subir bloqueado
        try:
            if entry in getattr(self, "_upload_queue", []):
                self._queue_skip_entry(entry)
                try:
                    self._upload_queue.remove(entry)
                except ValueError:
                    pass
                _time.sleep(0.4)
        except Exception:
            pass
        try:
            ok, msg = self._delete_remote_file(remote_path)
        except Exception as e:
            ok, msg = False, str(e)

        def _finish():
            if not ok:
                _log.warning("No se pudo borrar en el servidor: %s (%s)", remote_path, msg)
                self._set_status(f"No se pudo borrar en el servidor: {msg}", ERROR_COLOR)
                return   # la fila se queda, para poder reintentar
            if self._delete_local_file(entry, local_path, remote_borrado=True):
                self._drop_entry_row(entry)

        self.after(0, _finish)

    def _drop_entry_row(self, entry):
        """Quita la fila de la tabla y le dice al modo automático que deje
        el archivo en paz.

        Sin ese aviso, el siguiente escaneo de la carpeta vigilada lo vuelve
        a detectar, dispara el evento "start" y la fila reaparece sola, con
        lo que quitarla se vuelve imposible (visto de verdad con una
        película que TMDB no tiene: volvía cada pocos segundos)."""
        # Si estaba en cola o subiendo, sacarlo de la cola y cancelar su subida
        try:
            if entry in getattr(self, "_upload_queue", []):
                try:
                    self._upload_queue.remove(entry)
                except ValueError:
                    pass
            self._queue_skip_entry(entry)
        except Exception:
            pass
        self._discard_from_auto_watcher(entry)
        self.files = [e for e in self.files if e is not entry]
        self._multi_selected.discard(entry)
        self._refresh_table()
        self._autosave_session()
        if self._selected_entry is entry:
            self._clear_detail()
        self._update_assign_button_label()

    def _delete_local_file(self, entry, local_path: str, remote_borrado: bool = False) -> bool:
        """Borra el archivo del disco. False si falla, para que la fila NO
        desaparezca de la lista y se pueda reintentar: perder la fila tras
        un borrado a medias dejaría el archivo huérfano y sin rastro."""
        try:
            if local_path and Path(local_path).exists():
                os.remove(local_path)
        except Exception as e:
            _log.warning("Error borrando en local: %s", local_path, exc_info=True)
            # Si el del servidor ya se borró hay que decirlo, o el usuario
            # no sabe en qué estado ha quedado la cosa.
            aviso = " (el del servidor SÍ se borró)" if remote_borrado else ""
            self._set_status(f"No se pudo borrar en local: {e}{aviso}", ERROR_COLOR)
            return False

        destino = "en local y en el servidor" if remote_borrado else "en local"
        self._set_status(f"Borrado {destino}: {entry.new_name or entry.name}", SUCCESS_COLOR)
        return True

    def _discard_from_auto_watcher(self, entry):
        """Marca *entry* como "descartado" en auto_processed.json para que
        AutoWatcher no lo vuelva a meter en la lista. Silencioso si el
        archivo ya tenía un estado protegido.

        La escritura vive en core.auto_watcher.mark_discarded -- allí está
        junto al lector que la interpreta y, a diferencia de un método de
        App, se puede probar."""
        from core.auto_watcher import mark_discarded
        mark_discarded(entry.path)

    def _current_selection(self) -> list:
        """Ancla + selección múltiple, en el orden en que aparecen de
        verdad en self.files (no el orden en que se fueron marcando)."""
        if self._selected_entry is None:
            return []
        sel = [self._selected_entry] + [e for e in self._multi_selected if e is not self._selected_entry]
        order = {id(e): i for i, e in enumerate(self.files)}
        sel.sort(key=lambda e: order.get(id(e), 0))
        return sel

    def _sort_files_by_current_key(self):
        """Aplica a self.files el orden por la columna elegida
        (_files_sort_key/_files_sort_asc). Separado del clic de cabecera para
        poder re-aplicar también el orden guardado de una sesión anterior al
        cargar la lista (ver _apply_persisted_files_sort)."""
        key = self._files_sort_key
        if not key:
            return
        if key == "name":
            self.files.sort(key=lambda e: Path(e.path).name.lower(),
                            reverse=not self._files_sort_asc)
        elif key == "det":
            self.files.sort(key=lambda e: (e.detected or {}).get("title", "").lower(),
                            reverse=not self._files_sort_asc)
        elif key == "nn":
            self.files.sort(key=lambda e: (e.new_name or "").lower(),
                            reverse=not self._files_sort_asc)
        elif key == "dest":
            self.files.sort(key=lambda e: self._preview_remote_path(e).lower(),
                            reverse=not self._files_sort_asc)
        elif key == "stat":
            self.files.sort(key=lambda e: e.status, reverse=not self._files_sort_asc)
        elif key == "size":
            def _sz(e):
                if e._last_known_size_bytes is None:
                    try:
                        e._last_known_size_bytes = Path(e.path).stat().st_size
                    except OSError:
                        e._last_known_size_bytes = 0
                return e._last_known_size_bytes
            self.files.sort(key=_sz, reverse=not self._files_sort_asc)

    def _move_entry(self, entry, delta: int):
        try:
            idx = self.files.index(entry)
        except ValueError:
            return
        new_idx = max(0, min(len(self.files) - 1, idx + delta))
        if new_idx != idx:
            self.files.pop(idx)
            self.files.insert(new_idx, entry)
            self._refresh_table()

    def _move_entry_to(self, entry, new_idx: int):
        try:
            self.files.remove(entry)
        except ValueError:
            return
        self.files.insert(new_idx, entry)
        self._refresh_table()

    def _reset_entry(self, entry):
        entry.status       = "pendiente"
        entry.ftp_progress = 0.0
        entry.ftp_speed    = 0.0
        entry.ftp_status   = ""
        entry._stale_checked = False
        entry._duplicate_checked = False
        entry._last_upload_remote_size = 0
        self._update_row(entry)
        self._refresh_ftp_columns()

    def _session_path(self) -> Path:
        return _appdata_dir() / "session.json"

    #: Segundos entre autoguardados de sesión no forzados (ver
    #: _autosave_session).
    _SESSION_AUTOSAVE_SECONDS = 10.0

    def _autosave_session(self, force: bool = False) -> None:
        """Guarda session.json sin esperar al cierre limpio (antes solo se
        guardaba en shutdown: un apagón a diario se llevaba las filas del
        día, real: Snoopy 12/10 subido y borrado de la tabla al reiniciar
        porque session.json era de las 17:20). force=True guarda ya
        (transiciones finales del AutoWatcher); si no, como mucho cada
        _SESSION_AUTOSAVE_SECONDS, con un guardado de remate programado
        por si no llega otro evento. Los ticks de progreso ("uploading")
        no pasan por aquí: el % no es crítico y llegarían uno por chunk.
        Nunca lanza."""
        try:
            now = _time.monotonic()
            last = float(getattr(self, "_session_autosave_at", 0.0) or 0.0)
            if force or now - last >= self._SESSION_AUTOSAVE_SECONDS:
                self._session_autosave_at = now
                self._session_flush_pending = False
                self._save_session()
                return
            self._session_dirty = True
            if not getattr(self, "_session_flush_pending", False):
                self._session_flush_pending = True
                wait_ms = int((self._SESSION_AUTOSAVE_SECONDS - (now - last)) * 1000) + 50
                after = getattr(self, "after", None)
                if callable(after):
                    after(wait_ms, self._flush_session)
                else:
                    # Sin bucle de interfaz (tests): guardar ya, no hay
                    # nada que posponga.
                    self._flush_session()
        except Exception:
            pass

    def _flush_session(self) -> None:
        """Guardado de remate de _autosave_session. Nunca lanza."""
        try:
            self._session_flush_pending = False
            if getattr(self, "_session_dirty", False):
                self._session_dirty = False
                self._session_autosave_at = _time.monotonic()
                self._save_session()
        except Exception:
            pass

    def _save_session(self):
        if getattr(self, "_session_load_failed", False):
            try:
                _log.warning("Sesión: NO se guarda (la carga inicial falló); "
                             "se evita persistir una lista vacía sobre datos buenos")
            except Exception:
                pass
            return
        try:
            from core.session_store import save_session_dicts
            save_session_dicts(self._session_path(), [e.to_dict() for e in self.files])
        except Exception:
            pass

    def _load_session(self):
        from core.session_store import load_session_dicts
        self._session_load_failed = False
        try:
            raw, err = load_session_dicts(self._session_path())
            if err is not None:
                self._session_load_failed = True
                try:
                    _log.warning("Sesión: no se pudo cargar (%s); Archivos queda vacío hasta reiniciar", err)
                except Exception:
                    pass
                try:
                    self._set_status(f"Sesión no cargada: {err}", ERROR_COLOR)
                except Exception:
                    pass
                return
            entries, skipped = _entries_from_dicts(raw)
            self.files = _dedupe_entries(entries)
            # Rescate: estados transitorios de cola ("en_cola",
            # "esperando_confirmacion", "subiendo") no pueden existir con la
            # app recién abierta -- son de una tanda muerta al cerrar (diálogo
            # con la X, cierre a mitad de subida...). Sin esto la fila queda
            # muerta igual que antes del fix de _unqueue_status: ni el ▲ ni
            # Subir la aceptan (real: Andrómeda 2x05 tras reiniciar).
            for e in self.files:
                if getattr(e, "status", "") in ("en_cola", "esperando_confirmacion",
                                                "subiendo"):
                    e.status = self._unqueue_status(e, e.status)
            self._apply_persisted_files_sort()
            self._refresh_table()
            try:
                _log.info("Sesión: %d archivo(s) cargados%s", len(self.files),
                          f" ({skipped} entrada(s) inválida(s) descartadas)" if skipped else "")
            except Exception:
                pass
        except Exception:
            self._session_load_failed = True
            try:
                _log.exception("Sesión: fallo inesperado al cargar")
            except Exception:
                pass

    def _ask_removal(self, entry, remote_path: str):
        """Abre el diálogo con las opciones aplicables y ejecuta la elegida."""
        if entry not in self.files:
            return   # la fila ya no está (el watcher la movió, otra acción...)

        local_path   = entry.path
        local_exists = bool(local_path) and Path(local_path).exists()

        opciones = rp.removal_options(local_exists, remote_path)
        accion = self._make_remove_entry_dialog(entry.new_name or entry.name, opciones,
                                     local_path, remote_path).result
        if accion is None:
            self._set_status("", None)
            return

        if not rp.is_destructive(accion):
            self._drop_entry_row(entry)
            return

        borra_remoto = accion == rp.QUITAR_LOCAL_Y_REMOTO
        if not self._make_confirm_removal_dialog(entry.new_name or entry.name, local_path,
                                      remote_path if borra_remoto else "").result:
            return

        if not borra_remoto:
            is_uploading = (entry in getattr(self, "_upload_queue", [])) or getattr(entry, "status", "") in ("subiendo", "en_cola")
            if is_uploading:
                self._set_status(f"Cancelando subida y borrando: {entry.new_name or entry.name}", PENDING_COLOR)
                def _bg_local():
                    self._queue_skip_entry(entry)
                    # La subida puede ser del modo automático y no de la
                    # cola manual: _queue_skip_entry no la ve, y seguía
                    # subiendo sin fila hasta el final (real: La Odisea).
                    watcher = getattr(self, "_watcher", None)
                    if watcher is not None and local_path:
                        try:
                            watcher.cancel_file(local_path)
                        except Exception:
                            _log.warning("No se pudo cancelar la subida automática de %s",
                                         local_path, exc_info=True)
                    try:
                        self._upload_queue.remove(entry)
                    except ValueError:
                        pass
                    for _ in range(10):
                        try:
                            if local_path and Path(local_path).exists():
                                os.remove(local_path)
                                break
                        except Exception as e:
                            if "32" in str(e) or "being used" in str(e).lower() or "WinError" in str(type(e).__name__):
                                _time.sleep(0.5)
                                continue
                            break
                        _time.sleep(0.1)
                    still_exists = bool(local_path and Path(local_path).exists())
                    def _finish2():
                        if not still_exists:
                            self._drop_entry_row(entry)
                            self._set_status(f"Borrado: {entry.new_name or entry.name}", SUCCESS_COLOR)
                        else:
                            _log.warning("No se pudo borrar en local tras cancelar subida: %s", local_path)
                            self._set_status(f"No se pudo borrar (archivo en uso): {entry.new_name or entry.name}", ERROR_COLOR)
                            self._drop_entry_row(entry)
                    self.after(0, _finish2)
                threading.Thread(target=_bg_local, daemon=True).start()
                return
            if self._delete_local_file(entry, local_path):
                self._drop_entry_row(entry)
            return

        # El borrado remoto abre conexión FTP: en el hilo de la interfaz
        # congelaría la app hasta que el servidor responda (o hasta que
        # venza el timeout, si está caído).
        self._set_status(f"Borrando en el servidor: {entry.new_name or entry.name}", PENDING_COLOR)
        threading.Thread(target=self._delete_entry_remote_then_local,
                          args=(entry, local_path, remote_path), daemon=True).start()

    def _cleanup_stale_uploading_marks(self):
        """Al arrancar, ninguna subida manual puede estar realmente "en
        marcha" todavía (acabamos de abrir la app) — cualquier marca
        "subiendo" o "en_cola_manual" que quede en auto_processed.json es
        forzosamente de una sesión anterior cerrada a medias (cierre
        forzado, cuelgue, etc.).

        Antes esto borraba la entrada entera, lo que también borraba
        cualquier "identificado_manual" previo que _mark_auto_processed
        había guardado en "prev_status" al empezar esa subida -- visto de
        verdad: un cómic ya identificado a mano quedó "subiendo" al cerrar
        la app en mitad de la subida, el siguiente arranque lo borró del
        todo, y el modo automático (activado más tarde) lo detectó como
        nuevo y lo volvió a identificar (con otro resultado, mal) y subir
        por su cuenta -- duplicado en el FTP. Ahora se restaura el estado
        anterior en vez de borrar (ver _restore_or_delete_entry); solo se
        borra del todo si no había nada protegido antes."""
        import json as _json
        try:
            from core.auto_watcher import _processed_db_path, _DB_LOCK
            with _DB_LOCK:
                p = _processed_db_path()
                if not p.exists():
                    return
                db = _json.loads(p.read_text(encoding="utf-8"))
                stale = [k for k, v in db.items()
                         if v.get("status") in ("subiendo", "en_cola_manual")]
                if not stale:
                    return
                for k in stale:
                    self._restore_or_delete_entry(db, k)
                p.write_text(_json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
