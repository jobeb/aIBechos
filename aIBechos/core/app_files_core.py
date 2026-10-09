"""
Lógica de la pestaña Archivos sin interfaz: añadir archivos (y descomprimir),
identificar (TMDB/libros/cómics, con apoyo de IA), renombrar, la cola de subida
FTP/SFTP completa, el historial de subidas, la base de "ya procesados" que
comparte con AutoWatcher, y la reacción a los eventos de AutoWatcher.

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

import difflib
import json
import os
import subprocess
import threading
import time as _time
from pathlib import Path

from core import remote_presence as rp
from core.api_client import MediaInfo, detect_episode
from core.appdirs import app_data_dir as _appdata_dir, is_macos, is_windows
from core.applog import get_logger
from core.file_entry import FileEntry, _dedupe_entries, _entries_from_dicts
from core.fmt import fmt_size as _fmt_size
from core.ftp_categories import choose_category
from core.ftp_client import _ftp_safe
from core.mangadex_client import MangaDexClient
from core.renamer import (build_name_for_media_info, is_archive_file, is_book_file,
                          is_video_file, rename_file)
from core.series_match import best_match, series_similarity
from core.status_colors import ACCENT, ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class FilesCoreMixin:

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

            elif tipo == "uploading":
                entry.status       = "subiendo"
                entry.ftp_progress = progress or 0.0
                entry.ftp_speed    = speed or 0.0
                entry.new_name     = new_name or entry.new_name
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

            elif tipo == "skip":
                entry.status    = "omitido"
                entry.new_name  = new_name or entry.new_name
                entry.error_msg = reason or ""
                self._update_row(entry)
                if self._selected_entry is entry:
                    self._update_detail(entry)

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

        # Todo menos los ticks de progreso ("uploading", que se repiten
        # solos al siguiente chunk) es crítico: si se descarta, la fila
        # queda desincronizada para siempre (p.ej. "Renombrado" con el
        # archivo ya subido y borrado). El PRIMER tick (progress 0.0) es
        # la transición de estado renombrado/en_cola -> subiendo, no un
        # mero avance de barra: también es crítico (real: Fargo T4 se
        # quedaba en "Renombrado" con la barra a 0 aunque subía).
        _first_tick = (tipo == "uploading" and (progress or 0.0) <= 0)
        self._after_from_worker(_update, critical=(tipo != "uploading" or _first_tick))

    def _get_free_space_with_jellyfin_fallback(self, ftp_conn, root: str):
        """Espacio libre (bytes) para *root* usando el FTP; si el servidor no
        soporta ningún comando de espacio (p.ej. vsftpd), cae a Jellyfin si
        está configurado (>= 10.11, System/Info/Storage), emparejando por la
        raíz de la categoría -- ver core/jellyfin_storage_match.py. None si
        ninguna de las dos vías da un dato."""
        free = ftp_conn.get_free_space(root)
        if free is None and self.config_data.get("jellyfin_enabled"):
            from core.media_server_refresh import get_jellyfin_free_space_for_root
            free = get_jellyfin_free_space_for_root(
                root, self.config_data.get("jellyfin_host", ""),
                self.config_data.get("jellyfin_api_key", ""))
        return free

    def _streams_for_upload(self) -> int:
        """Cuántas conexiones puede repartirse cada archivo.

        El ajuste es un presupuesto TOTAL, no por archivo: si se suben varios a
        la vez ya hay paralelismo entre ellos y no conviene multiplicarlo --
        abrir veinte sesiones SSH de golpe es buena forma de que el servidor
        empiece a rechazarlas. Con un solo archivo, que es cuando la velocidad
        se queda corta, se usan todas."""
        if str(self.config_data.get("ftp_protocol", "ftp")).lower() != "sftp":
            return 1        # por FTP no hay nada que repartir
        presupuesto = int(self.config_data.get("ftp_upload_streams", 4) or 1)
        a_la_vez = max(1, min(getattr(self, "_upload_batch_size", 1),
                              int(self.config_data.get("ftp_parallel", 1) or 1)))
        return max(1, presupuesto // a_la_vez)

    def _start_ftp_upload(self, entries):
        if self._upload_running:
            self._set_status("Ya hay una subida en progreso", WARNING_COLOR)
            return
        if not self.config_data.get("ftp_host", ""):
            self._set_status("Configura el servidor FTP en la pestania FTP", WARNING_COLOR)
            return
        # Cuántos archivos lleva la tanda: decide si cada uno puede repartirse
        # entre varias conexiones (ver _streams_for_upload).
        self._upload_batch_size = len(entries)
        if self._missing_ep_scanning:
            # La subida es lo prioritario: un escaneo completo hace muchas
            # llamadas seguidas a TMDB, y compartirlas con la identificación
            # de lo que se está subiendo ahora mismo solo añade tiempos de
            # espera/errores de red sin necesidad -- se cancela solo.
            self._cancel_missing_episodes_scan()
        # Marcar YA como "en marcha" (bloquea clics repetidos). La conexión
        # de verdad la resuelve _queue_worker con su propio pool dedicado
        # (con su propio manejo de error si falla) -- no hace falta probar
        # self.ftp aquí antes, sería una conexión redundante que además
        # competiría por self.ftp con el modo automático sin necesidad.
        self._upload_running = True
        self._set_status("Conectando al servidor FTP...", WARNING_COLOR)
        self._refresh_ftp_space()
        self._begin_ftp_upload(entries)

    def _reserve_manual_entries(self, entries):
        """Reserva temprana de una tanda manual frente al modo automático:
        marca "en_cola_manual" en auto_processed.json (ver
        core/auto_watcher.py::_PROTECTED_STATUSES) para que el watcher no
        coja estos archivos mientras esperan turno. Sin esto, solo se
        marcaba "subiendo" al empezar cada transferencia y el watcher
        podía llevarse un encolado durante la espera (carpetas, diálogos,
        turnos), compitiendo por el mismo archivo. Se libera al terminar
        la tanda (ver epílogo de _queue_worker), al fallar/cancelar cada
        archivo (ver _unmark_auto_processed) y al arrancar si quedó de
        una sesión anterior (ver _cleanup_stale_uploading_marks). Solo
        entradas identificadas (las subibles); el resto las ignora. En
        UNA sola pasada leer-modificar-escribir (no una por archivo: con
        cientos en cola y una base de miles de entradas, eso congelaría
        la GUI, que es donde corre el encolado)."""
        import json as _json, time as _t
        try:
            from core.auto_watcher import _processed_db_path, _DB_LOCK
            keys = []
            for e in entries or ():
                try:
                    if getattr(e, "media_info", None) is None:
                        continue
                    keys.append((self._db_key(e.path), e.new_name or ""))
                except Exception:
                    pass
            if not keys:
                return
            with _DB_LOCK:
                p = _processed_db_path()
                db = {}
                if p.exists():
                    try:
                        db = _json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                now = _t.time()
                for key, new_name in keys:
                    prev = db.get(key, {})
                    prev_status = prev.get("status", "")
                    entry = {"status": "en_cola_manual", "new_name": new_name, "ts": now}
                    if prev_status and prev_status not in ("subiendo", "en_cola_manual"):
                        entry["prev_status"] = prev_status
                        entry["prev_new_name"] = prev.get("new_name", "")
                    db[key] = entry
                p.write_text(_json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _release_manual_entries(self, entries):
        """Suelta la reserva de _reserve_manual_entries para las entradas
        que sigan marcadas "en_cola_manual" (lo subido/fallado ya cambió
        de marca por su cuenta). Una sola pasada, igual que al reservar."""
        import json as _json
        try:
            from core.auto_watcher import _processed_db_path, _DB_LOCK
            keys = []
            for e in entries or ():
                try:
                    keys.append(self._db_key(e.path))
                except Exception:
                    pass
            if not keys:
                return
            with _DB_LOCK:
                p = _processed_db_path()
                if not p.exists():
                    return
                try:
                    db = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return
                touched = False
                for key in keys:
                    if db.get(key, {}).get("status", "") == "en_cola_manual":
                        self._restore_or_delete_entry(db, key)
                        touched = True
                if touched:
                    p.write_text(_json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _begin_ftp_upload(self, entries):
        """Continuación de _start_ftp_upload en el hilo principal, una vez
        resuelta la conexión FTP en _connect_and_start_upload."""
        self._reserve_manual_entries(entries)
        self._upload_queue = list(entries)
        self._upload_cancel.clear()
        self._upload_skip.clear()
        self._upload_current_idx    = -1
        self._upload_current_remote = ""
        self._upload_overwrite_all  = False
        self._upload_skip_all       = False
        self._upload_duplicate_ignore_all = False
        self._upload_stale_choice = None
        # Refrescar en cada tanda por si han cambiado carpetas en el servidor
        self._series_folder_cache.clear()
        self._ftp_dir_cache.clear()
        # Resetear columnas FTP de los archivos en cola — pero si ya trae progreso
        # guardado (reanudado tras cerrar a medias), conservarlo para no fluctuar 0%↔45%
        for entry in entries:
            if not getattr(entry, "ftp_progress", 0):
                entry.ftp_progress = 0.0
            entry.ftp_speed    = 0.0
            if not getattr(entry, "ftp_status", "") or entry.ftp_status.startswith("0%"):
                entry.ftp_status   = "En espera"
            entry.status       = "en_cola"
            self._update_row(entry)
        self._refresh_ftp_columns()
        threading.Thread(target=self._queue_worker, daemon=True).start()

    def _queue_stop_all(self):
        self._upload_cancel.set()
        self._set_status("Deteniendo subida...", WARNING_COLOR)

    def _queue_skip_entry(self, entry):
        """Salta la subida del archivo dado señalando su skip_event."""
        if not self._upload_running:
            return
        slot_of     = getattr(self, "_upload_slot_of",     {})
        skip_events = getattr(self, "_upload_skip_events", [])
        slot = slot_of.get(id(entry))
        if slot is not None and slot < len(skip_events):
            skip_events[slot].set()

    def _category_for(self, info):
        """Elige la categoría FTP (nombre/géneros/rutas/plantilla) que le
        corresponde a *info* según sus géneros de TMDB. None si no hay
        ninguna categoría configurada aplicable."""
        cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []})
        return choose_category(info.genre_ids, cats.get(info.media_type, []))

    def _find_category_with_existing_folder(self, ftp_conn, info, use_cache_only=False, force_refresh=False,
                                             known_year=None):
        """Busca en TODAS las categorías configuradas para el tipo de
        *info* (no solo en la que tocaría por género) si ya existe una
        carpeta con nombre igual o prácticamente idéntico a la serie --
        para que la organización real del servidor prevalezca sobre la
        clasificación automática cuando no coinciden (series movidas a
        mano de categoría, por ejemplo por tener contenido para adultos y
        ya no encajar en una categoría infantil). Solo hace caso a
        coincidencias de alta confianza: nombre exacto tras sanear, o
        ratio >= 0.90 -- ese 0.90 en concreto es el que da series_similarity
        cuando un nombre está literalmente contenido en el otro ("Desencanto"
        dentro de "Desencanto (Disenchantment)", por ejemplo, un nombre de
        carpeta con el título original entre paréntesis) -- no es un
        parecido vago, así que aceptarlo en silencio aquí es razonable;
        cualquier cosa por debajo se deja para la confirmación de siempre
        dentro de la categoría elegida por género (ver
        _resolve_series_folder). Devuelve (categoría, nombre_de_carpeta_
        existente), o (None, None) si no hay ninguna coincidencia de esa
        confianza -- el nombre exacto de la carpeta hace falta para que la
        vista previa de "Destino" no proponga crear una carpeta nueva con
        el título tal cual lo da TMDB cuando ya existe una parecida (p.ej.
        "(Des)encanto" de TMDB vs "Desencanto" ya en el servidor).
        use_cache_only=True (para la columna "Destino", que se recalcula
        en el hilo de la GUI al redibujar filas) no listará ninguna
        carpeta que no esté ya en caché -- evita bloquear la interfaz
        haciendo una conexión/listado FTP de verdad solo por refrescar una
        vista previa; una vez haya conexión real (al subir), la caché ya
        tendrá el dato y la vista previa se pondrá al día sola.
        force_refresh=True (para el borrado desde Episodios que faltan,
        ver _resolve_missing_ep_series_path) ignora self._ftp_dir_cache
        aunque la raíz ya esté cacheada y vuelve a listarla -- self._ftp_dir_cache
        vive mientras dure la sesión de la app y nunca se invalida sola,
        así que una carpeta añadida DESPUÉS del primer listado de esa raíz
        en toda la sesión (o un listado que se cortó corto sin dar error)
        se queda sin ver hasta reiniciar la app; para una acción tan poco
        frecuente y tan seria como borrar, vale la pena pagar el listado
        de verdad en vez de arriesgarse a un "no encontrado" con la
        carpeta ahí delante.

        known_year (opcional, ver core.ftp_categories.find_existing_category_folder
        y _missing_ep_known_year) ayuda a encontrar la carpeta cuando el
        nombre que da Jellyfin/Plex no trae año pero la carpeta real sí
        lo lleva para distinguir un remake del original.

        El propio emparejamiento (nombre exacto/folder_name conocido/
        parecido >=0.90) vive en core.ftp_categories.find_existing_category_folder,
        compartido con AutoWatcher (ver core/auto_watcher.py) -- aquí solo
        queda la parte de CÓMO listar/cachear cada raíz, que sí es propia
        de la GUI (use_cache_only para no bloquear la interfaz)."""
        cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}).get(info.media_type, [])
        known_folder_name = getattr(info, "folder_name", None)

        def dir_lookup(root):
            if root not in self._ftp_dir_cache or force_refresh:
                if use_cache_only:
                    return None
                # Dos listados, no uno -- visto de verdad con "(Des)encanto":
                # ya tenía carpeta en "Series" desde antes, pero un ÚNICO
                # NLST a esa raíz (360 carpetas) a veces vuelve incompleto
                # sin dar ningún error (mismo servidor donde LIST -R se
                # corta sistemáticamente, ver _build_ftp_episode_index) --
                # eso hacía que ESE episodio (y todos los de la misma tanda,
                # que reutilizan esta misma caché) acabaran mal clasificados
                # por género en vez de en la carpeta que ya tenían. La unión
                # de dos intentos independientes es mucho menos probable que
                # pierda la misma carpeta en los dos a la vez.
                first = set(ftp_conn.list_dirs(root))
                second = set(ftp_conn.list_dirs(root))
                if first != second:
                    _log.warning("Listado de '%s' inconsistente entre dos intentos seguidos "
                                "(%d vs %d carpetas) -- usando la unión de ambos", root, len(first), len(second))
                self._ftp_dir_cache[root] = list(first | second)
            return self._ftp_dir_cache[root]

        from core.ftp_categories import find_existing_category_folder
        return find_existing_category_folder(cats, info.title, known_folder_name, dir_lookup,
                                              known_year=known_year)

    def _existing_ftp_path_for_info(self, ftp_conn, info, use_cache_only: bool = True,
                                     known_year=None) -> str:
        """Ruta "root/carpeta" ya existente en el FTP para *info* (según su
        media_type), o "" si no hay ninguna coincidencia -- envoltorio fino
        sobre _find_category_with_existing_folder que solo da formato al
        resultado. Compartido por _missing_ep_series_path (Episodios que
        faltan, siempre media_type="tv") y los paneles de detalle de
        Archivos/Liberar espacio (que pueden pasar cualquier MediaInfo real,
        tv/movie/libro) -- antes cada uno tenía su propia copia de esta
        cola. use_cache_only=False limpia primero las raíces con caché
        vacía de self._ftp_dir_cache (ver el docstring de
        _find_category_with_existing_folder, force_refresh) para forzar un
        listado fresco de verdad."""
        if not use_cache_only:
            cats = self.config_data.get(
                "ftp_categories", {"tv": [], "movie": [], "libro": []}).get(info.media_type, [])
            for cat in cats:
                root = cat.get("root", "")
                if root and not self._ftp_dir_cache.get(root):
                    self._ftp_dir_cache.pop(root, None)
        category, folder_name = self._find_category_with_existing_folder(
            ftp_conn, info, use_cache_only=use_cache_only, known_year=known_year)
        if not category or not folder_name:
            return ""
        return f"{category.get('root', '').rstrip('/')}/{folder_name}"

    def _resolve_series_folder(self, ftp_conn, category: dict, info, entry=None) -> str:
        """Si ya existe en la raíz de *category* una carpeta con nombre
        parecido (idioma, artículo, nombre corto vs largo, título original
        entre paréntesis...) a la serie a subir, la reutiliza directamente
        cuando la confianza es alta (ratio >= 0.90, igual que
        _find_category_with_existing_folder -- mismo criterio en los dos
        sitios, para que la vista previa de "Destino" y la subida real
        coincidan sin pedir una confirmación que ya se daba por hecha en
        la vista previa). Por debajo de eso, pregunta una sola vez por
        serie si hay que reutilizarla en vez de crear una nueva a mayores.
        La respuesta se cachea para el resto de episodios de la misma
        serie dentro de esta tanda de subida."""
        desired = info.title
        with self._series_folder_lock:
            if info.tmdb_id in self._series_folder_cache:
                return self._series_folder_cache[info.tmdb_id]

            chosen = desired
            root = category.get("root", "")
            if root:
                if root not in self._ftp_dir_cache:
                    # Dos listados, no uno, misma unión que
                    # _find_category_with_existing_folder -- esta caché es
                    # compartida (self._ftp_dir_cache) y quien la rellene
                    # primero decide lo que ven todos los demás lectores
                    # mientras dure la sesión (ninguno vuelve a listar si ya
                    # está en caché); un listado único e incompleto aquí
                    # tiene el mismo riesgo de "envenenarla" que ya se vio
                    # en _prefetch_ftp_category_dirs.
                    first = set(ftp_conn.list_dirs(root))
                    second = set(ftp_conn.list_dirs(root))
                    self._ftp_dir_cache[root] = list(first | second)
                existing = self._ftp_dir_cache[root]

                sanitized_desired = _ftp_safe(desired)
                if sanitized_desired in existing:
                    chosen = sanitized_desired
                else:
                    candidate, ratio = best_match(desired, existing, min_ratio=0.55)
                    if candidate:
                        # Reutilizar en silencio SOLO con coincidencia de alta
                        # confianza en modo estricto + anotación (igual que
                        # _find_category_with_existing_folder): el modo laxo
                        # da 0.90 a cualquier prefijo literal, y eso fusionó
                        # "Dragon Ball Daima" con la carpeta "Dragon Ball"
                        # (serie distinta, sin carpeta propia en el servidor)
                        # para luego denunciar su 1x01 como duplicado del
                        # 1x01 de la otra serie. Por debajo del 0.90 estricto
                        # se pregunta como siempre, no se fusiona solo.
                        strict_ratio = series_similarity(
                            desired, candidate, strict=True, allow_annotation=True)
                        if strict_ratio >= 0.90:
                            chosen = candidate
                        else:
                            if entry is not None:
                                entry.status = "esperando_confirmacion"
                                self.after(0, lambda e=entry: self._update_row(e))
                            answer = [None]
                            ev = threading.Event()
                            def _ask(d=desired, c=candidate, ans=answer, e=ev):
                                dlg = self._make_series_match_dialog(d, c)
                                ans[0] = dlg.result
                                e.set()
                            self.after(0, _ask)
                            ev.wait()
                            if answer[0] == "yes":
                                chosen = candidate

            self._series_folder_cache[info.tmdb_id] = chosen
            return chosen

    def _auto_is_processing(self, path: str) -> bool:
        """True si el modo automático tiene *path* en curso ahora mismo
        (ver core/auto_watcher.py::_in_progress: se añade al detectar y se
        quita en todo camino terminal). La subida manual debe omitirlo: la
        dirección contraria ya está cubierta (lo manual marca "subiendo" en
        auto_processed.json y el watcher lo respeta, ver _should_process),
        pero el watcher no marca nada hasta terminar, así que sin esto las
        dos subidas abrían el mismo archivo a la vez y una fallaba al
        desaparecer el archivo bajo sus pies ("No se pudo abrir",
        "Archivo local no encontrado"). Típico con adelgazados: la ligera
        cae en la carpeta vigilada (= Incoming) y el usuario la sube a mano
        mientras el automático ya la está procesando. Nunca lanza
        excepción (ante la duda, False = comportamiento de antes)."""
        try:
            watcher = getattr(self, "_watcher", None)
            if watcher is None or not getattr(watcher, "running", False):
                return False
            from core.path_key import canon_path
            want = canon_path(path)
            if not want:
                return False
            for k in list(getattr(watcher, "_in_progress", None) or ()):
                try:
                    if canon_path(k) == want:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _upload_entry_with(self, entry, ftp_conn, speed_kbs, skip_ev):
        """Sube un único archivo usando la conexión ftp_conn dada. Devuelve (ok, msg)."""
        if self._auto_is_processing(entry.path):
            # El modo automático lo está procesando ahora mismo: omitir en
            # la tanda manual con mensaje claro en vez de competir por el
            # archivo (ver _auto_is_processing). El automático actualiza
            # esta misma fila al terminar (mismo path → mismo entry).
            self._ftp_row_set(entry, "En proceso (auto)", entry.ftp_progress, 0)
            self.after(0, lambda e=entry: self._update_row(e))
            _log.info("Subida: %r lo está procesando el modo automático, se omite en la tanda manual",
                      entry.name)
            return True, "en_proceso_auto"
        if not Path(entry.path).exists():
            # El renombrado de AutoWatcher no espera cupo de subida (solo la
            # transferencia en sí — ver core/upload_slots.py), así que puede
            # renombrar y subir el archivo por su cuenta mientras esta
            # entrada seguía esperando turno en la cola manual con el nombre
            # viejo. Si AutoWatcher ya lo dio por subido, sincronizar el
            # estado aquí en vez de fallar con "archivo no encontrado" en
            # bucle en cada reintento.
            auto_status = self._auto_processed_status(entry.path)
            if auto_status == "subido":
                entry.status = "subido"
                self._ftp_row_set(entry, "Subido (automático)", 1, 0)
                self.after(0, lambda e=entry: self._update_row(e))
                return True, "ya_subido_por_auto"
            entry.status    = "error"
            entry.error_msg = "Archivo local no encontrado (¿se movió, se renombró o lo procesó el modo automático?)"
            self._ftp_row_set(entry, "No encontrado", 0, 0)
            self.after(0, lambda e=entry: self._update_row(e))
            _log.warning("Subida: archivo local no encontrado %r", entry.path)
            return False, "archivo_no_encontrado"

        info = entry.media_info
        if not info:
            self._ftp_row_set(entry, "Sin info TMDB", 0, 0)
            self.after(0, lambda e=entry: self._update_row(e))
            return True, "sin_info"

        if entry.remote_dir_override:
            # El usuario fijó la carpeta a mano (doble clic en la columna
            # "Destino" de la tabla) -- prevalece sobre todo lo demás, ni
            # siquiera se consulta la categoría por género.
            remote_dir = entry.remote_dir_override
            # Sin categoría no hay raíz de la que sacar el espacio libre: se
            # usa la propia carpeta fijada. Sin esto, la comprobación de
            # espacio de más abajo reventaba con UnboundLocalError ('root' se
            # asignaba SOLO en la rama del else), así que TODA subida con
            # destino puesto a mano fallaba con un error sin explicación --
            # visto en el log de un usuario al que no le subía nada.
            root = remote_dir
        else:
            category = self._category_for(info)
            if info.media_type == "tv":
                # Si la serie ya tiene carpeta en OTRA categoría (se movió a
                # mano, p.ej. por tener contenido para adultos y ya no
                # encajar en la categoría infantil que le tocaría por
                # género), esa organización real del servidor prevalece sobre
                # la clasificación automática -- ver _find_category_with_existing_folder.
                existing_category, _existing_name = self._find_category_with_existing_folder(ftp_conn, info)
                if existing_category:
                    if category and existing_category.get("root") != category.get("root"):
                        _log.info(
                            "Subida: '%s' (tmdb_id=%s) -- categoría por género sería '%s', "
                            "pero ya existe carpeta en '%s' -- se usa esta última",
                            info.title, info.tmdb_id, category.get("name"), existing_category.get("name"))
                    category = existing_category
                else:
                    # Sin coincidencia de alta confianza en NINGUNA categoría
                    # -- normal en una serie nueva, pero también el síntoma
                    # visto de verdad con "(Des)encanto": ya tenía carpeta en
                    # "Series", pero esta comprobación no la encontró esa vez
                    # (coincidiendo con un reescaneo pesado a la vez) y acabó
                    # en "SeriesPeques" solo por género (Animación). Con esto
                    # en el log, la próxima vez que pase se ve al momento en
                    # vez de por casualidad al revisar el FTP a mano.
                    _log.info(
                        "Subida: '%s' (tmdb_id=%s) -- sin carpeta existente encontrada en ninguna "
                        "categoría, se usa la de género: '%s'",
                        info.title, info.tmdb_id, category.get("name") if category else None)
            if not category:
                entry.status    = "error"
                entry.error_msg = "Sin categoría FTP configurada"
                self._ftp_row_set(entry, "Sin categoría", 0, 0)
                self.after(0, lambda e=entry: self._update_row(e))
                return False, "sin_categoria"
            root = category.get("root", "")
            if not root:
                entry.status    = "error"
                entry.error_msg = f"Categoría '{category.get('name')}' sin ruta configurada"
                self._ftp_row_set(entry, "Sin ruta", 0, 0)
                self.after(0, lambda e=entry: self._update_row(e))
                return False, "sin_ruta"

            if info.media_type == "tv":
                serie_name = self._resolve_series_folder(ftp_conn, category, info, entry)
            else:
                serie_name = info.title
            full_tpl   = root.rstrip("/") + "/" + category.get("template", "{serie}/")
            remote_dir = ftp_conn.build_remote_path(full_tpl, serie_name, info.season, info.year, info.media_type)
        # "Renombrar archivos en destino" (Ajustes) — si está desactivado, se
        # sube con el nombre ORIGINAL (el que tenía al añadirlo, entry.name,
        # que no cambia aunque luego se renombre en local), aunque la carpeta
        # se organice igualmente por serie/temporada según TMDB.
        rename_remote   = self.config_data.get("rename_remote", True)
        remote_filename = (entry.new_name if rename_remote else entry.name) or Path(entry.path).name
        remote_file = f"{remote_dir.rstrip('/')}/{remote_filename}"

        # Este mismo archivo se subió antes bajo OTRO nombre (identificación
        # vieja y errónea -- p.ej. "Papillon (2017)" como "Papillon (1973)"),
        # así que el servidor puede haber quedado con un resto a medio subir
        # bajo ese nombre viejo. Ofrecer borrarlo antes de subir la versión
        # corregida; si el usuario lo desea, se borra y se continúa. No
        # molesta en el caso normal (misma ruta -> sin resto).
        # "Borrar resto y subir (todos)"/"No borrar, subir (todos)" solo
        # tiene sentido si hay más de un archivo en esta tanda de subida
        # (mismo criterio que el diálogo de sobrescribir de más abajo).
        is_batch = len(self._upload_queue) > 1

        stale_remote = ("" if getattr(entry, "_stale_checked", False) else
                        self._stale_remote_from_history(entry.path, entry.name, remote_file))
        if stale_remote:
            # Si ya se eligió "para todos" en esta tanda, aplicar sin preguntar
            if getattr(self, "_upload_stale_choice", None) == "delete":
                try:
                    ok_del, _msg_del = self._delete_remote_file(stale_remote)
                    _log.info("Resto de subida antiguo borrado (todos): %s -> %s",
                              stale_remote, ok_del)
                except Exception as e:
                    _log.warning("No se pudo borrar el resto de subida antiguo %s: %s",
                                 stale_remote, e)
                entry._stale_checked = True
            elif getattr(self, "_upload_stale_choice", None) == "no_delete":
                entry._stale_checked = True
            else:
                answer = [None]
                ev = threading.Event()
                def _ask_stale(d=remote_file, s=stale_remote, ans=answer, e=ev):
                    dlg = self._make_stale_upload_dialog(d, s, show_all_buttons=is_batch)
                    ans[0] = dlg.result
                    e.set()
                self.after(0, _ask_stale)
                ev.wait()
                entry._stale_checked = True   # no volver a preguntar en reintentos de esta tanda
                if answer[0] is None:
                    # Cancelar esta subida
                    self._ftp_row_set(entry, "Saltado", 0, 0)
                    self.after(0, lambda e=entry: self._update_row(e))
                    return False, "saltado"
                if answer[0] in ("delete_upload", "delete_upload_all"):
                    if answer[0] == "delete_upload_all":
                        self._upload_stale_choice = "delete"
                    try:
                        ok_del, _msg_del = self._delete_remote_file(stale_remote)
                        _log.info("Resto de subida antiguo borrado: %s -> %s",
                                  stale_remote, ok_del)
                    except Exception as e:
                        _log.warning("No se pudo borrar el resto de subida antiguo %s: %s",
                                     stale_remote, e)
                elif answer[0] in ("no_delete", "no_delete_all"):
                    if answer[0] == "no_delete_all":
                        self._upload_stale_choice = "no_delete"
                    # no borrar, continuar
                    pass
                else:
                    # Cancelar esta subida por si el diálogo devolvió algo inesperado
                    self._ftp_row_set(entry, "Saltado", 0, 0)
                    self.after(0, lambda e=entry: self._update_row(e))
                    return False, "saltado"

        try:
            local_size = Path(entry.path).stat().st_size
        except OSError:
            local_size = 0

        # Si el archivo remoto con el MISMO nombre ya está completo (o más
        # grande), es un archivo ya subido de verdad: no hace falta avisar
        # también de "posible contenido duplicado" con otro nombre -- el
        # diálogo de sobreescribir ya es suficiente, y preguntar dos veces
        # seguidas era confuso (real: al re-subir un archivo existente que
        # además tenía otra versión en la carpeta salían dos diálogos).
        remote_size = ftp_conn.get_remote_size(remote_file)
        prev_remote_size = getattr(entry, "_last_upload_remote_size", None)
        stalled = (remote_size is not None and prev_remote_size is not None
                   and remote_size <= prev_remote_size)
        entry._last_upload_remote_size = remote_size
        already_exists = remote_size is not None and remote_size >= local_size
        force_overwrite = False

        # Detección de duplicados — solo cuando el archivo con el nombre
        # exacto NO está completo (no existe o es un parcial más pequeño que
        # se reanudará solo): aquí sí tiene sentido preguntar — ¿hay en esta
        # misma carpeta remota un archivo DISTINTO que representa el mismo
        # contenido (mismo episodio, u otra versión de la misma película)?
        # Distinto de la comprobación de "ya existe" de abajo, que solo mira
        # el nombre remoto exacto -- esto detecta el mismo contenido llegado
        # con un nombre de archivo diferente (otra fuente/calidad/grupo).
        # Solo se comprueba una vez por entrada (no en cada reintento) para
        # no listar la carpeta remota una y otra vez ni repreguntar lo mismo.
        # (Va FUERA del lock de abajo: list_files es una llamada de red, y
        # mantener el lock durante ella frenaría en serie a todos los workers
        # de la tanda aunque no tuvieran nada que confirmar.)
        dup = None
        if not already_exists and not getattr(entry, "_duplicate_checked", False):
            entry._duplicate_checked = True
            from core.duplicate_detect import find_duplicate
            existing_files = ftp_conn.list_files(remote_dir)
            dup = find_duplicate(existing_files, info, remote_filename)

        # Los diálogos de confirmación se serializan con
        # self._upload_confirm_lock: con varias subidas en paralelo, varios
        # workers podían llegar a este punto a la vez (todos veían
        # _upload_overwrite_all=False antes de que ninguno contestara) y
        # cada uno abría su propio diálogo -- contestar "Sobrescribir todos"
        # al primero no cancelaba los demás ya en pantalla. Manteniendo el
        # lock, solo un worker pregunta a la vez, y los que esperan el lock
        # tras contestar "todos" ven el flag ya activo y saltan directo al
        # elif de abajo sin volver a preguntar. Vale igual para "Omitir
        # todos" (_upload_skip_all): una sola respuesta resuelve la tanda.
        with self._upload_confirm_lock:
            if already_exists and not self._upload_overwrite_all and not self._upload_skip_all:
                # Estado visible en la columna "Estado": si no se distingue de
                # "En cola" es fácil no darse cuenta de que hay un diálogo
                # esperando respuesta (p.ej. cuando el modo automático ya subió
                # este mismo archivo antes) y la fila parece quedarse "colgada"
                # sin explicación.
                prev_status = entry.status
                entry.status = "esperando_confirmacion"
                self.after(0, lambda e=entry: self._update_row(e))
                answer = [None]
                ev = threading.Event()
                def _ask(rf=remote_file, ans=answer, e=ev):
                    dlg = self._make_overwrite_dialog(rf, show_all_button=is_batch,
                                           show_skip_all_button=is_batch)
                    ans[0] = dlg.result
                    e.set()
                self.after(0, _ask)
                ev.wait()
                if answer[0] == "all":
                    self._upload_overwrite_all = True
                    force_overwrite = True
                elif answer[0] == "skip_all":
                    # "Omitir todos": los demás workers de la tanda que
                    # lleguen aquí ven el flag y se omiten sin preguntar.
                    self._upload_skip_all = True
                    entry.status = prev_status
                    self.after(0, lambda e=entry: self._update_row(e))
                    return True, "omitido"
                elif answer[0] == "skip":
                    # Omitir o cerrar el diálogo (por defecto también "skip"):
                    # vuelve al estado de antes de preguntar en vez de quedarse
                    # marcado "Omitido" — es un "ahora no", no una exclusión
                    # permanente, así que la fila queda lista para reintentarse
                    # normalmente más adelante.
                    entry.status = prev_status
                    self.after(0, lambda e=entry: self._update_row(e))
                    return True, "omitido"
                else:   # "overwrite"
                    force_overwrite = True
            elif already_exists and self._upload_overwrite_all:
                # "Sobrescribir todos" ya activo de un archivo anterior de esta tanda
                force_overwrite = True
            elif already_exists and self._upload_skip_all:
                # "Omitir todos" ya contestado en otro archivo de esta tanda:
                # se omite sin preguntar (igual que "skip", es un "ahora no"
                # reintentable, no una exclusión permanente).
                return True, "omitido"
            else:
                # El archivo con el nombre exacto NO está completo: si además
                # de la comprobación de "ya existe" de arriba se encontró un
                # duplicado real (el mismo contenido con otro nombre) hay que
                # preguntar igualmente si se sube o no.
                if dup and not self._upload_duplicate_ignore_all:
                    prev_status = entry.status
                    entry.status = "esperando_confirmacion"
                    self.after(0, lambda e=entry: self._update_row(e))
                    answer = [None]
                    ev = threading.Event()
                    def _ask(d=dup, ans=answer, e=ev):
                        dlg = self._make_overwrite_dialog(d,
                            title="Posible contenido duplicado",
                            message=("Ya hay un archivo distinto en el servidor que parece "
                                     "ser el mismo contenido:"),
                            overwrite_label="Subir de todas formas",
                            all_label="Subir todas de todas formas",
                            close_result="skip",
                            show_all_button=is_batch)
                        ans[0] = dlg.result
                        e.set()
                    self.after(0, _ask)
                    ev.wait()
                    if answer[0] == "all":
                        self._upload_duplicate_ignore_all = True
                    elif answer[0] != "overwrite":   # "skip" (Omitir o cerrado con la X)
                        entry.status = prev_status
                        self.after(0, lambda e=entry: self._update_row(e))
                        return True, "omitido"

                if stalled and not self._upload_overwrite_all:
                    # El archivo remoto no avanzó nada desde el intento anterior:
                    # seguir reanudando el mismo punto para siempre no lleva a
                    # ningún sitio si ese punto está atascado (p.ej. un parcial
                    # dañado de un corte anterior) — dar la opción de empezar de
                    # cero en vez de reintentar sin fin sin ninguna salida.
                    prev_status = entry.status
                    entry.status = "esperando_confirmacion"
                    self.after(0, lambda e=entry: self._update_row(e))
                    answer = [None]
                    ev = threading.Event()
                    def _ask(rf=remote_file, ans=answer, e=ev, rs=remote_size, ls=local_size):
                        dlg = self._make_overwrite_dialog(rf,
                            title="La subida no avanza",
                            message=(f"Este archivo lleva al menos un intento sin avanzar en "
                                     f"el servidor (sigue en {_fmt_size(rs)} de {_fmt_size(ls)}). "
                                     f"Puede que el punto de reanudación esté dañado."),
                            overwrite_label="Empezar de cero",
                            all_label="Empezar de cero (todos)",
                            close_result="skip",
                            show_all_button=is_batch)
                        ans[0] = dlg.result
                        e.set()
                    self.after(0, _ask)
                    ev.wait()
                    if answer[0] == "all":
                        self._upload_overwrite_all = True
                        force_overwrite = True
                    elif answer[0] == "overwrite":
                        force_overwrite = True
                    else:   # "skip" (Omitir o cerrado con la X)
                        entry.status = prev_status
                        self.after(0, lambda e=entry: self._update_row(e))
                        return True, "omitido"
                elif stalled and self._upload_overwrite_all:
                    force_overwrite = True

        free = self._get_free_space_with_jellyfin_fallback(ftp_conn, root)
        if free is not None and free < local_size:
            entry.status    = "error"
            entry.error_msg = "Disco lleno en el servidor"
            self._ftp_row_set(entry, "Sin espacio", 0, 0)
            self.after(0, lambda e=entry: self._update_row(e))
            self.after(0, lambda gb=free/(1024**3): self._set_status(
                f"Disco lleno — libre: {gb:.1f} GB", ERROR_COLOR))
            self._upload_cancel.set()
            _log.error("Subida: sin espacio antes de empezar %r (libre: %s, necesita: %s)",
                      remote_filename, _fmt_size(free), _fmt_size(local_size))
            return False, "disco_lleno"

        # "Subidas simultáneas" es un cupo GLOBAL compartido con el modo
        # automático (ver core/upload_slots.py) — si está a 1, esta subida
        # espera aquí (la fila se queda "En cola") a que termine cualquier
        # otra, manual o automática, antes de empezar a transferir de verdad.
        if not self._upload_slots.acquire(cancel_event=self._upload_cancel):
            entry.status = "listo"
            self._ftp_row_set(entry, "Cancelado", entry.ftp_progress, 0)
            self.after(0, lambda e=entry: self._update_row(e))
            return False, "cancelado"

        entry.status     = "subiendo"
        entry.ftp_status = "Subiendo..."
        # Protege el archivo de AutoWatcher mientras dura la transferencia: si
        # el modo automático se activa (o ya lo está) y escanea la carpeta
        # justo ahora, no debe meterse a identificar/renombrar/subir este
        # mismo archivo por su cuenta mientras ya lo tenemos abierto subiéndolo.
        self._mark_auto_processed(entry.path, "subiendo", entry.new_name)
        self.after(0, lambda e=entry: self._update_row(e))
        self.after(0, lambda e=entry: self._ftp_row_uploading(e))

        def progress(sent, total_b, spd, e=entry):
            e.ftp_progress = sent / total_b if total_b > 0 else 0
            e.ftp_speed    = spd
            self.after(0, lambda p=e.ftp_progress, s=spd, en=e: self._ftp_row_live(en, p, s))

        _log.info("Subida: iniciando %r -> %s (resume=%s)",
                  remote_filename, remote_dir, not force_overwrite)
        try:
            ok, msg = ftp_conn.upload_file(
                entry.path, remote_dir, progress,
                cancel_event=self._upload_cancel,
                skip_event=skip_ev,
                speed_limit_kbs=speed_kbs,
                try_resume=not force_overwrite,
                remote_filename=remote_filename,
                streams=self._streams_for_upload(),
            )
        finally:
            self._upload_slots.release()

        try:
            size = Path(entry.path).stat().st_size
        except OSError:
            size = 0

        if ok:
            entry.status = "subido"
            if hasattr(entry, "_last_upload_remote_size"):
                del entry._last_upload_remote_size
            self._ftp_row_set(entry, "Subido", 1, 0)
            self._mark_auto_processed(entry.path, "subido", entry.new_name)
            # filename = entry.name (el ORIGINAL) -- ver el mismo comentario
            # en el modo automático: el historial muestra cómo llegó el
            # archivo, el nombre final queda en la columna "destino".
            self._save_history_entry(
                entry.name,
                remote_file, "ok", size, local_path=entry.path)
            _log.info("Subida: OK %r (%s)", remote_filename, _fmt_size(size))
            from core.media_server_refresh import trigger_refresh
            trigger_refresh(self.config_data)
            # Este método corre en un hilo de subida, no en el de la GUI --
            # a diferencia del mismo hook en _on_auto_file_event (que ya
            # corre dentro de self.after), aquí sí hay que agendarlo.
            self.after(0, lambda mi=entry.media_info: self._remove_uploaded_episode_from_missing_list(mi))
            self.after(0, lambda mi=entry.media_info: self._remove_uploaded_movie_from_movies_list(mi))
            self.after(0, self._refresh_ftp_space)
            self._apply_manual_post_process_action(entry)
        elif msg == "cancelado":
            entry.status = "listo"
            self._ftp_row_set(entry, "Cancelado", entry.ftp_progress, 0)
            _log.info("Subida: cancelada por el usuario %r", remote_filename)
        elif msg == "saltado":
            entry.status = "listo"
            self._ftp_row_set(entry, "Saltado", 0, 0)
            _log.info("Subida: saltada %r", remote_filename)
        elif msg == "disco_lleno":
            entry.status    = "error"
            entry.error_msg = "Disco lleno en el servidor"
            self._ftp_row_set(entry, "Disco lleno", 0, 0)
            self.after(0, lambda: self._set_status("Disco lleno en servidor", ERROR_COLOR))
            self._upload_cancel.set()
            self._save_history_entry(
                entry.name,
                remote_file, "error", size, error_msg=entry.error_msg, local_path=entry.path)
            _log.error("Subida: disco lleno en servidor, cancelando %r", remote_filename)
        else:
            entry.status = "error"
            if stalled:
                entry.error_msg = (
                    f"{msg} — el archivo lleva más de un intento sin avanzar en el "
                    f"servidor (sigue en {_fmt_size(remote_size)} de {_fmt_size(local_size)}). "
                    f"Puede que el disco del servidor esté lleno.")
            else:
                entry.error_msg = msg
            self._ftp_row_set(entry, "Error", 0, 0)
            self._save_history_entry(
                entry.name,
                remote_file, "error", size, error_msg=entry.error_msg, local_path=entry.path)
            _log.error("Subida: ERROR %r — %s", remote_filename, entry.error_msg)

        if not ok:
            # La subida no llegó a completarse: quitar la marca "subiendo"
            # para no dejar el archivo bloqueado para AutoWatcher para
            # siempre — si de verdad se quedó a medias, que pueda reintentarlo
            # él también (o el usuario, a mano, otra vez).
            self._unmark_auto_processed(entry.path)

        self.after(0, lambda e=entry: self._update_row(e))
        return ok or msg in ("omitido", "saltado", "sin_info"), msg

    def _apply_manual_action_to_path(self, path: Path):
        """Aplica "manual_action" (Ajustes: mantener/mover a "procesados"/
        eliminar) a *path* -- compartido entre _apply_manual_post_process_action
        (tras una subida manual) y la descompresión de archivos añadidos a
        mano (ver _add_paths_extracting_archives), que no tienen una
        FileEntry cuyo .path actualizar, solo una ruta suelta. Devuelve la
        ruta resultante (la misma si se mantiene, la nueva si se mueve, o
        None si se elimina)."""
        action = self.config_data.get("manual_action", "Mantener original")
        if action == "Mover a subcarpeta 'procesados'":
            try:
                dest_dir = path.parent / "procesados"
                dest_dir.mkdir(exist_ok=True)
                import shutil
                dest_path = dest_dir / path.name
                shutil.move(str(path), str(dest_path))
                _log.info("Movido a procesados: %s", path.name)
                return dest_path
            except Exception as e:
                _log.error("No se pudo mover: %s", e)
                # El mensaje se congela AHORA (msg=...): Python borra el nombre
                # 'e' al salir del except, y este lambda lo lee más tarde desde
                # el hilo de Tk -- tal cual estaba, avisar del fallo fallaba a
                # su vez con NameError y el usuario no veía nada.
                self.after(0, lambda msg=str(e): self._set_status(
                    f"No se pudo mover archivo: {msg}", WARNING_COLOR))
                return path
        elif action == "Eliminar original":
            try:
                if path.exists():
                    path.unlink()
                    _log.info("Eliminado: %s", path.name)
                return None
            except Exception as e:
                _log.error("No se pudo eliminar: %s", e)
                # Mismo motivo que en "Mover a procesados" de aquí arriba.
                self.after(0, lambda msg=str(e): self._set_status(
                    f"No se pudo eliminar archivo: {msg}", WARNING_COLOR))
                return path
        return path

    def _apply_manual_post_process_action(self, entry):
        """Acción post-proceso tras una subida MANUAL exitosa (pestaña
        Archivos) -- "manual_action" en Ajustes, independiente de
        "auto_action" (que solo aplica al modo automático, ver
        core/auto_watcher.py). Corre en el hilo de subida, no en el de la
        GUI, igual que el resto de _upload_entry_with."""
        result_path = self._apply_manual_action_to_path(Path(entry.path))
        if result_path is not None:
            entry.path = str(result_path)

    def _queue_worker(self):
        from concurrent.futures import ThreadPoolExecutor

        parallel   = max(1, min(5, int(self.config_data.get("ftp_parallel", 1))))

        # Subidas transfiriendo AHORA MISMO (no las conexiones configuradas)
        # -- ver get_speed_kbs. Lo incrementa/decrementa cada worker al coger
        # y soltar su conexión (ver process()).
        _active_uploads = [0]
        _active_lock = threading.Lock()

        # Callable — lee config_data en cada chunk para cambio instantáneo
        # Config almacena MB/s; devolvemos KB/s (ftp_client lo multiplica × 1024 → bytes/s)
        def get_speed_kbs():
            """Límite POR CONEXIÓN: el límite global repartido entre las
            subidas que están transfiriendo de verdad en este momento.

            Antes se dividía entre `parallel` (las conexiones CONFIGURADAS),
            así que subir un único archivo con 5 conexiones configuradas y
            un límite de 20 MB/s daba 20/5 = 4 MB/s reales -- se
            desperdiciaban las otras cuatro quintas partes del límite
            porque las otras conexiones no estaban subiendo nada. Al
            repartir entre las ACTIVAS, un archivo solo puede usar el
            límite entero, y en cuanto arrancan más subidas cada una baja
            su parte sola (esto se reevalúa en cada bloque, ver el callback
            de FTPClient.upload_file, así que se ajusta en caliente sin
            reiniciar la transferencia)."""
            try:
                mbs = float(self.config_data.get("ftp_speed_limit", 0) or 0)
                if mbs <= 0:
                    return 0
                with _active_lock:
                    n = max(1, _active_uploads[0])
                return int((mbs / n) * 1024)  # MB/s → KB/s
            except Exception:
                return 0

        # Crear pool de conexiones FTP
        host     = self.config_data.get("ftp_host", "")
        port     = int(self.config_data.get("ftp_port", 21))
        user     = self.config_data.get("ftp_user", "")
        password = self.config_data.get("ftp_password", "")
        use_tls  = bool(self.config_data.get("ftp_use_tls", False))

        pool = []
        connect_error = ""
        for _ in range(parallel):
            c = self._new_ftp_client()
            ok, msg = c.connect(host, port, user, password, use_tls)
            if ok:
                pool.append(c)
            else:
                connect_error = msg
                self.after(0, lambda m=msg: self._set_status(m, ERROR_COLOR))
                break

        if not pool:
            # Sin esto, las filas se quedaban mostrando "En cola" para
            # siempre: el mensaje de error solo se veía un instante en la
            # barra de estado, pero nada actualizaba las filas ya puestas en
            # cola en _begin_ftp_upload, así que parecía que la subida se
            # había quedado colgada sin explicación.
            for entry in self._upload_queue:
                entry.status    = "error"
                entry.error_msg = connect_error or "No se pudo conectar al servidor FTP"
                self.after(0, lambda e=entry: self._ftp_row_set(e, "Error", 0, 0))
                self.after(0, lambda e=entry: self._update_row(e))
            self._upload_running = False
            return

        if len(pool) < parallel:
            # Diagnóstico: "conexiones en paralelo" pedía "parallel", pero
            # si alguna conexión de más falla a mitad del bucle de arriba,
            # este queda con MENOS -- y como pool no está vacío, seguía
            # adelante en silencio con ese cupo reducido, sin avisar de que
            # las subidas iban a ir con menos paralelismo del configurado.
            _log.warning("Subida: se pidieron %d conexiones en paralelo pero solo se consiguieron %d "
                        "(%s) -- las subidas de esta tanda irán con menos paralelismo del configurado",
                        parallel, len(pool), connect_error or "sin más detalle")
        else:
            _log.info("Subida: %d conexión(es) en paralelo listas para esta tanda", len(pool))

        import queue as _queue
        conn_q = _queue.Queue()
        for c in pool:
            conn_q.put(c)

        # Un skip_event por slot de conexión — guardados en self para que
        # _queue_skip_entry pueda acceder desde el hilo principal
        self._upload_skip_events = [threading.Event() for _ in pool]
        self._upload_slot_of = {}  # id(entry) → slot index
        skip_events = self._upload_skip_events
        slot_of     = self._upload_slot_of

        _slot_lock = threading.Lock()
        _slot_counter = [0]
        _nslots = len(pool)

        def _next_slot():
            with _slot_lock:
                s = _slot_counter[0] % _nslots
                _slot_counter[0] += 1
                return s

        max_retries = max(0, int(self.config_data.get("ftp_retries", 3)))

        def process(entry):
            if self._upload_cancel.is_set():
                return
            slot = _next_slot()
            slot_of[id(entry)] = slot
            ftp_conn = conn_q.get()
            with _active_lock:
                _active_uploads[0] += 1   # ver get_speed_kbs: reparte el límite entre las ACTIVAS
            try:
                for attempt in range(max_retries + 1):
                    if self._upload_cancel.is_set():
                        break
                    # Reconectar si la conexión se cayó
                    if not ftp_conn.is_connected():
                        self.after(0, lambda e=entry: self._ftp_row_set(e, "Reconectando…", e.ftp_progress, 0))
                        ok_rc, _ = ftp_conn.connect(host, port, user, password, use_tls)
                        if not ok_rc:
                            if attempt < max_retries:
                                _time.sleep(2 ** attempt)
                                continue
                            break
                    ok, msg = self._upload_entry_with(
                        entry, ftp_conn, get_speed_kbs, skip_events[slot])
                    skip_events[slot].clear()
                    # No reintentar si cancelado/saltado/omitido, éxito o error de configuración
                    if ok or msg in ("cancelado", "saltado", "omitido", "sin_info",
                                      "sin_categoria", "sin_ruta", "archivo_no_encontrado"):
                        break
                    # Error recuperable — reintentar
                    if attempt < max_retries:
                        wait = 2 ** attempt
                        self.after(0, lambda e=entry, a=attempt+1, m=max_retries:
                            self._ftp_row_set(e, f"Reintento {a}/{m}…", e.ftp_progress, 0))
                        _time.sleep(wait)
            finally:
                with _active_lock:
                    _active_uploads[0] -= 1
                conn_q.put(ftp_conn)

        from concurrent.futures import wait as _fut_wait, FIRST_COMPLETED as _FIRST
        with ThreadPoolExecutor(max_workers=len(pool)) as executor:
            idx     = 0
            pending = {}   # future → entry
            _logged_fill = False
            while not self._upload_cancel.is_set():
                # Enviar trabajos disponibles hasta llenar el pool
                while idx < len(self._upload_queue) and len(pending) < len(pool):
                    e = self._upload_queue[idx]
                    idx += 1
                    f = executor.submit(process, e)
                    pending[f] = e
                if not _logged_fill and len(self._upload_queue) > 1:
                    # Diagnóstico una sola vez, al primer llenado -- cuántos
                    # archivos se enviaron a la vez de golpe frente a los
                    # que había en cola y cuántas conexiones había
                    # disponibles. Si esto muestra 1 en vez de len(pool),
                    # el paralelismo se está perdiendo ANTES de llegar
                    # siquiera a _upload_slots (que se comprobó aparte y sí
                    # permite varias a la vez), no dentro de él.
                    _logged_fill = True
                    _log.info("Subida: %d archivo(s) enviados a la vez al arrancar (de %d en cola, "
                              "%d conexión(es) disponibles)", len(pending), len(self._upload_queue), len(pool))
                if pending:
                    done, _ = _fut_wait(list(pending), timeout=0.3, return_when=_FIRST)
                    for f in done:
                        e = pending.pop(f)
                        try:
                            f.result()
                        except Exception as exc:
                            e.status    = "error"
                            e.error_msg = str(exc)
                            _log.exception("Subida: excepcion inesperada procesando %r", e.name)
                elif idx >= len(self._upload_queue):
                    # Cola vacía -- antes de dar la tanda por terminada,
                    # barrer self.files por si hay archivos que se han
                    # quedado "listo"/"renombrado" DESPUÉS de arrancar esta
                    # subida (p.ej. una identificación en bloque de cientos
                    # de capítulos que todavía seguía en marcha cuando se
                    # pulsó "Subir todo") -- bug real: esos archivos se
                    # quedaban en "Listo" para siempre en vez de "Subido",
                    # salvo que el usuario los subiera manualmente uno a uno
                    # con el botón ▲ de su fila (ver _upload_one, que ya
                    # permite encolar en caliente sobre una tanda en
                    # marcha -- esto hace lo mismo automáticamente).
                    in_queue = {id(e) for e in self._upload_queue}
                    stragglers = [e for e in self.files if id(e) not in in_queue
                                  and e.status in ("listo", "renombrado") and e.media_info]
                    if stragglers:
                        for e in stragglers:
                            e.ftp_progress = 0.0
                            e.ftp_speed    = 0.0
                            e.ftp_status   = "En espera"
                            e.status       = "en_cola"
                            self.after(0, lambda en=e: self._update_row(en))
                        self._reserve_manual_entries(stragglers)
                        self._upload_queue.extend(stragglers)
                        continue
                    # Esperar brevemente por nuevos ítems encolados en caliente
                    _time.sleep(0.2)
                    if idx >= len(self._upload_queue):
                        break

        # Liberar la reserva temprana de la tanda (ver
        # _reserve_manual_entries): lo que nunca llegó a procesarse no
        # debe quedar vetado al automático.
        try:
            self._release_manual_entries(list(self._upload_queue or []))
        except Exception:
            pass

        for c in pool:
            try:
                c.disconnect()
            except Exception:
                pass

        self._upload_running        = False
        self._upload_current_idx    = -1
        self._upload_current_remote = ""
        cancelled = self._upload_cancel.is_set()
        self.after(0, lambda: self._set_status(
            "Subida cancelada" if cancelled else "Subida completada",
            WARNING_COLOR if cancelled else SUCCESS_COLOR))
        self.after(0, self._restore_ftp_row_buttons)

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
        if not added:
            return
        if same_series_prompt:
            book_comic_added = [e for e in added if e.is_book]
            video_added      = [e for e in added if not e.is_book]
            self._prompt_same_series_if_needed(book_comic_added, video_added)
        else:
            self.after(50, self._search_new_entries, added)

    def _upload_one(self, entry):
        """Añade el archivo a la cola. Si hay subida activa lo encola; si no, la inicia."""
        if self._upload_running:
            if entry not in self._upload_queue:
                self._reserve_manual_entries([entry])
                self._upload_queue.append(entry)
                entry.ftp_progress = 0.0
                entry.ftp_speed    = 0.0
                entry.ftp_status   = "En espera"
                entry.status       = "en_cola"
                self._update_row(entry)
                self.after(0, self._refresh_ftp_columns)
                self._set_status(f"Encolado: {entry.name[:50]}", PENDING_COLOR)
            return
        self._start_ftp_upload([entry])

    def _delete_remote_file(self, remote_path: str) -> tuple:
        """Borra un archivo del servidor con conexión propia -- mismo
        patrón que el borrado de la herramienta de liberar espacio (ver
        _delete_cleanup_item): no se reutiliza self.ftp, que es el canal de
        control compartido. Se llama desde un hilo, nunca desde la interfaz."""
        own_ftp = self._new_ftp_client()
        ok, msg = own_ftp.connect(
            self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
            self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
            self.config_data.get("ftp_use_tls", False))
        if not ok:
            return False, msg or "no se pudo conectar"
        try:
            return own_ftp.delete_file(remote_path)
        finally:
            try:
                own_ftp.disconnect()
            except Exception:
                pass

    def _search_new_entries(self, entries: list):
        """Lanza búsqueda para los archivos recién añadidos (o
        reidentificados a mano, ver _set_book_comic_type) -- TMDB para
        vídeo, Google Books/ComicVine para libros/cómics (ver
        _search_entry). La API Key de TMDB solo hace falta si hay ALGÚN
        archivo de vídeo en el lote -- exigirla siempre bloqueaba
        identificar libros/cómics sin ninguna key de TMDB configurada."""
        if any(not e.is_book for e in entries) and not self.config_data.get("tmdb_api_key"):
            self._set_status("Configura tu API Key de TMDB en Configuración", WARNING_COLOR)
            return
        threading.Thread(
            target=self._search_all_worker,
            args=(entries,),
            daemon=True,
        ).start()

    def _search_all_worker(self, entries=None):
        from core.api_client import TMDBClient as _TMDBClient

        if entries is not None:
            pending = [e for e in entries if e.status not in ("renombrado", "subido")]
        else:
            pending = [e for e in self.files if e.status not in ("renombrado", "subido")]
        if not pending:
            return

        api_key = self.config_data["tmdb_api_key"]
        lang    = self.config_data.get("language", "es-ES")
        client  = _TMDBClient(api_key)
        client.set_language(lang)

        for entry in pending:
            # Marcar como buscando
            entry.status = "buscando"
            self.after(0, lambda e=entry: self._update_row(e))
            try:
                self._search_entry(entry, tmdb=client)
            except Exception as ex:
                entry.status    = "error"
                entry.error_msg = str(ex)
            self.after(0, lambda e=entry: self._update_row(e))

        n_ok = sum(1 for e in self.files if e.status == "listo")
        self.after(0, lambda: self._set_status(
            f"Búsqueda completada — {n_ok} encontrados", SUCCESS_COLOR))

    def _try_ai_fallback(self, entry, tmdb):
        """Último recurso cuando TMDB no encuentra nada con el título
        limpiado localmente: si el usuario activó el fallback de IA en
        Ajustes, le pide a la IA que identifique qué es ruido en el nombre
        de archivo. Si el título que resulta (usando nuestra propia
        detección de temporada/episodio + esos términos nuevos) SÍ encuentra
        resultado en TMDB, se aprenden esos términos para que la próxima vez
        no haga falta la IA. Si no ayuda, no se aprende nada -- evita que un
        despiste de la IA contamine la lista para futuros archivos.
        Devuelve (results, query, det) o None."""
        if not self.config_data.get("ai_fallback_enabled"):
            return None
        api_key = self.config_data.get("ai_api_key", "")
        if not api_key:
            return None
        from core.ai_title_fallback import guess_title_via_ai
        stem = entry.name[:-len(entry.ext)] if entry.ext else entry.name
        ai_result = guess_title_via_ai(stem, api_key)
        if not ai_result:
            return None
        retry_det = detect_episode(entry.name, extra_junk_terms=ai_result["junk_tokens"])
        retry_query = retry_det.get("title", "")
        if not retry_query:
            return None
        retry_results = tmdb.search_multi(retry_query)
        if not retry_results:
            return None
        from core.learned_terms import add_learned_terms
        add_learned_terms(ai_result["junk_tokens"])
        return retry_results, retry_query, retry_det

    def _search_entry(self, entry, tmdb=None):
        if entry.is_book:
            self._search_book_entry(entry)
            return
        if tmdb is None:
            tmdb = self.tmdb
        det   = entry.detected
        query = det.get("title", "")
        if not query:
            entry.status    = "error"
            entry.error_msg = "No se pudo detectar el nombre"
            _log.warning("Busqueda: no se pudo detectar nombre para %r", entry.name)
            return
        # prefer_type: el "1x05"/"S01E05" del nombre ya dice si es serie o
        # película -- sin pasarlo, mandaba la popularidad y un título que
        # existe como las dos cosas se resolvía siempre a favor de la
        # película (ver TMDBClient.search_multi).
        results = tmdb.search_multi(query, prefer_type=det.get("media_type", ""))
        if not results:
            fallback = self._try_ai_fallback(entry, tmdb)
            if fallback:
                results, query, det = fallback
                _log.info("Busqueda: '%s' sin resultados, la IA encontro '%s' para %r",
                          det.get("title", query), query, entry.name)
            else:
                entry.status    = "error"
                entry.error_msg = "Sin resultados en TMDB"
                _log.warning("Busqueda: sin resultados en TMDB para '%s' (%r)", query, entry.name)
                return
        top = results[0]

        # Calcular confianza: similitud entre el título detectado y el resultado TMDB
        result_title = (top.get("name", "") or top.get("title", "")).lower()
        confidence   = difflib.SequenceMatcher(None, query.lower(), result_title).ratio()
        entry.confidence = round(confidence * 100)

        info = tmdb.build_media_info(top, season=det.get("season"), episode=det.get("episode"))
        entry.media_info = info
        entry.new_name   = self._build_name(info, entry.ext)
        entry.status     = "listo"
        entry.error_msg  = ""
        _log.info("Busqueda: %r -> '%s' (confianza %d%%)", entry.name, entry.new_name, entry.confidence)
        # Igual que en la búsqueda manual: evita que un reintento automático
        # posterior de AutoWatcher pise este resultado si el archivo venía de
        # la carpeta vigilada y seguía fallando por su cuenta.
        self._mark_auto_processed(entry.path, "identificado_manual", entry.new_name)

    def _search_book_entry(self, entry):
        """Equivalente de _search_entry para libros/cómics -- despacha a
        OpenLibrary/Google Books o ComicVine según la extensión en vez de a
        TMDB (ver entry.is_book/is_comic, calculados una vez en
        FileEntry.__init__). La identificación en sí (incluida OpenLibrary
        como proveedor principal de libros con Google Books de apoyo, y la
        traducción de título vía IA para ComicVine con su caché) vive en
        core/book_identify.py, compartida con AutoWatcher (Modo Automático)
        -- aquí solo se asigna el resultado a la fila y se registra en el
        log de la GUI."""
        from core.book_identify import identify_book_or_comic
        result = identify_book_or_comic(
            entry.detected, entry.is_comic, self.comicvine, self.book_client,
            openlibrary_client=self.openlibrary_client,
            ai_fallback_enabled=self.config_data.get("ai_fallback_enabled", False),
            ai_api_key=self.config_data.get("ai_api_key", ""))
        if result.error:
            entry.status    = "error"
            entry.error_msg = result.error
            _log.warning("Busqueda: %s para '%s' (%r)", result.error, result.used_query, entry.name)
            return
        entry.confidence = result.confidence
        entry.media_info = result.media_info
        entry.new_name   = self._build_name(result.media_info, entry.ext)
        entry.status     = "listo"
        entry.error_msg  = ""
        if result.provider and not entry.is_comic:
            _log.info("Busqueda: identificado vía %s para %r", result.provider, entry.name)
        if result.translated_via_ai:
            _log.info("Busqueda: sin resultados en ComicVine, la IA tradujo a '%s' para %r",
                      result.used_query, entry.name)
        _log.info("Busqueda: %r -> '%s' (confianza %d%%)", entry.name, entry.new_name, entry.confidence)
        self._mark_auto_processed(entry.path, "identificado_manual", entry.new_name)

    def _build_name(self, info, ext):
        templates = {k: self.config_data.get(k) for k in (
            "movie_template", "anime_template", "comic_template",
            "libro_template", "tv_template")}
        return build_name_for_media_info(info, ext, templates)

    def _label_for_result(self, r: dict) -> str:
        """Etiqueta del desplegable de resultados -- cada origen
        (TMDB/OpenLibrary/Google Books/ComicVine, ver self._results_kind)
        devuelve el título/año en un sitio distinto de su JSON."""
        if self._results_kind == "openlibrary":
            name = r.get("title", "")
            year = str(r.get("first_publish_year", "") or "")
            return f"{name} ({year}) [libro-OL]"
        if self._results_kind == "book":
            info = r.get("volumeInfo", {}) or {}
            name = info.get("title", "")
            year = (info.get("publishedDate", "") or "")[:4]
            return f"{name} ({year}) [libro]"
        if self._results_kind == "comic":
            name = (r.get("volume") or {}).get("name") or r.get("name", "")
            year = str(r.get("start_year", "") or "")
            return f"{name} ({year}) [cómic]"
        if self._results_kind == "mangadex":
            attrs = r.get("attributes", {}) or {}
            name = MangaDexClient._first_localized(attrs.get("title", {}))
            year = str(attrs.get("year", "") or "")
            return f"{name} ({year}) [MangaDex]"
        if self._results_kind == "anilist":
            t = r.get("title", {}) or {}
            name = t.get("english") or t.get("romaji") or t.get("native") or ""
            year = str((r.get("startDate") or {}).get("year", "") or "")
            return f"{name} ({year}) [AniList]"
        if self._results_kind == "kitsu":
            attrs = r.get("attributes", {}) or {}
            name = attrs.get("canonicalTitle") or (attrs.get("titles") or {}).get("en") or ""
            year = (attrs.get("startDate") or "")[:4]
            return f"{name} ({year}) [Kitsu]"
        if r.get("media_type") == "tv":
            name = r.get("name", "")
            year = (r.get("first_air_date", "") or "")[:4]
        else:
            name = r.get("title", "")
            year = (r.get("release_date", "") or "")[:4]
        return f"{name} ({year}) [{r.get('media_type', '')}]"

    def _build_info_from_result(self, result: dict, det: dict) -> MediaInfo:
        """Construye el MediaInfo a partir de un resultado crudo, según qué
        cliente lo produjo (self._results_kind) -- usado tanto al
        previsualizar (_preview_result) como al asignar
        (_assign_selected_result)."""
        if self._results_kind == "openlibrary":
            return self.openlibrary_client.build_book_info(result)
        if self._results_kind == "book":
            return self.book_client.build_book_info(result)
        if self._results_kind == "comic":
            return self.comicvine.build_comic_info(result, episode=det.get("episode"))
        if self._results_kind == "mangadex":
            return self.mangadex_client.build_manga_info(result, episode=det.get("episode"))
        if self._results_kind == "anilist":
            return self.anilist_client.build_manga_info(result, episode=det.get("episode"))
        if self._results_kind == "kitsu":
            return self.kitsu_client.build_manga_info(result, episode=det.get("episode"))
        return self.tmdb.build_media_info(result, season=det.get("season"), episode=det.get("episode"))

    def _assign_to_selection_worker(self, result, targets, also_upload: bool = False):
        """Aplica *result* a cada archivo de *targets* (varios marcados con
        Ctrl/Shift+clic) -- cada uno conserva su propio episodio/número de
        capítulo ya detectado de su nombre (entry.detected), solo se
        comparte la serie/título/portada. Omite (sin aplicar a la fuerza)
        los archivos cuyo tipo no coincide con el del resultado elegido
        (p.ej. un vídeo mezclado por error en una selección de cómics).

        also_upload=True encola (_upload_one) cada archivo que sí se
        asignó -- en orden, desde el hilo principal (ver _finish más
        abajo): _upload_one ya sabe si debe empezar la subida al momento
        o encolarla si otra ya está en marcha, así que llamarlo varias
        veces seguidas para varios archivos ya reparte correctamente entre
        "empieza este" y "el resto a la cola", sin lógica nueva."""
        kind = self._results_kind
        ok, skipped = 0, 0
        ok_entries = []
        for entry in targets:
            matches = (
                (kind in ("comic", "mangadex", "anilist", "kitsu") and entry.is_comic) or
                (kind in ("openlibrary", "book") and entry.is_book and not entry.is_comic) or
                (kind == "tmdb" and not entry.is_book)
            )
            if not matches:
                skipped += 1
                continue
            info = self._build_info_from_result(result, entry.detected)
            entry.media_info = info
            entry.new_name   = self._build_name(info, entry.ext)
            entry.status     = "listo"
            entry.error_msg  = ""
            entry._stale_checked = False
            self._mark_auto_processed(entry.path, "identificado_manual", entry.new_name)
            ok += 1
            ok_entries.append(entry)
            self.after(0, lambda e=entry: self._update_row(e))

        def _finish(ok=ok, skipped=skipped):
            msg = f"Asignado a {ok} archivo(s)"
            if skipped:
                msg += f" -- {skipped} omitido(s) por tipo distinto"
            self._set_status(msg, SUCCESS_COLOR if ok else WARNING_COLOR)
            if self._selected_entry in targets:
                self._update_detail(self._selected_entry)
            if also_upload:
                for e in ok_entries:
                    self._upload_one(e)
        self.after(0, _finish)

    def _rename_worker(self, entries):
        # "Renombrar archivos en origen" en Ajustes -- si está desactivado,
        # el botón "Renombrar" no debe tocar el disco para nada (igual que
        # ya hace AutoWatcher): el nombre calculado se queda listo para la
        # subida (con nombre limpio, si "Renombrar en destino" está
        # activado), pero el archivo original no se mueve ni se renombra.
        if not self.config_data.get("rename_local", True):
            for entry in entries:
                self.after(0, lambda e=entry: self._update_row(e))
            self.after(0, lambda: self._set_status(
                "Renombrado en origen desactivado en Ajustes -- se mantiene el nombre original en disco",
                WARNING_COLOR))
            return

        for entry in entries:
            ok, msg = rename_file(entry.path, entry.new_name)
            if not ok and msg.startswith("Ya existe:") and not self._rename_overwrite_all:
                # El destino ya existe en LOCAL (no en el servidor FTP — eso
                # es el otro diálogo, _OverwriteDialog para subidas). El
                # usuario está presente y ha pedido esto explícitamente
                # (botón "Renombrar"), así que aquí sí tiene sentido
                # preguntar en vez de fallar sin más, a diferencia del modo
                # automático desatendido.
                answer = [None]
                ev = threading.Event()
                def _ask(fn=entry.new_name, ans=answer, e=ev):
                    dlg = self._make_overwrite_dialog(fn, title="El archivo ya existe en local",
                        message="Ya existe un archivo con ese nombre en la misma carpeta:")
                    ans[0] = dlg.result
                    e.set()
                self.after(0, _ask)
                ev.wait()
                if answer[0] == "all":
                    self._rename_overwrite_all = True
                    ok, msg = rename_file(entry.path, entry.new_name, force_overwrite=True)
                elif answer[0] == "overwrite":
                    ok, msg = rename_file(entry.path, entry.new_name, force_overwrite=True)
                else:   # "skip" (o cerrado sin elegir)
                    entry.status    = "omitido"
                    entry.error_msg = "Omitido: ya existía un archivo local con ese nombre"
                    self.after(0, lambda e=entry: self._update_row(e))
                    continue
            elif not ok and msg.startswith("Ya existe:") and self._rename_overwrite_all:
                ok, msg = rename_file(entry.path, entry.new_name, force_overwrite=True)

            if ok:
                self._mark_auto_processed(entry.path, "renombrado", entry.new_name)
                entry.path   = msg
                entry.status = "renombrado"
            else:
                entry.status    = "error"
                entry.error_msg = msg
            self.after(0, lambda e=entry: self._update_row(e))
        self.after(0, self._sort_files_by_episode)
        self.after(0, lambda: self._set_status("Renombrado completado", SUCCESS_COLOR))

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

    def _enqueue_or_start(self, entries: list):
        """Encola si ya hay subida en curso, si no inicia nueva tanda."""
        if self._upload_running:
            added = 0
            for e in entries:
                if e not in self._upload_queue and e.status in ("listo", "renombrado"):
                    self._reserve_manual_entries([e])
                    self._upload_queue.append(e)
                    e.ftp_progress = 0.0
                    e.ftp_speed = 0.0
                    e.ftp_status = "En espera"
                    e.status = "en_cola"
                    self._update_row(e)
                    added += 1
            if added:
                self.after(0, self._refresh_ftp_columns)
                self._set_status(f"Encolados {added} archivo(s) — se subirán al terminar la tanda actual", PENDING_COLOR)
            else:
                self._set_status("Ya en cola o no está en listo", WARNING_COLOR)
            return
        self._start_ftp_upload(entries)

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

    def _on_header_upload_clicked(self):
        n = len(self._current_selection())
        if n > 1:
            self._upload_selected_ftp()
        else:
            self._upload_all_ftp()

    def _effective_search_provider(self, entry) -> str:
        """Proveedor que se usaría de verdad ahora mismo: el del selector."""
        if self._search_provider_override == "auto":
            return "tmdb"
        return self._search_provider_override

    @staticmethod
    def _search_source_name(entry) -> str:
        """Nombre del servicio que identificaría *entry* -- ComicVine para
        cómics, OpenLibrary (proveedor principal, con Google Books de apoyo
        automático -- ver core/book_identify.py) para ebooks de texto, TMDB
        para el resto (o si entry es None, p.ej. sin nada seleccionado
        todavía). Usado para que las etiquetas de "Buscar..." reflejen el
        servicio real en vez de decir siempre "TMDB" también para
        libros/cómics (ver _search_book_entry, que despacha a uno u otro
        según el tipo)."""
        if entry is None:
            return "TMDB"
        if entry.is_comic:
            return "ComicVine"
        if entry.is_book:
            return "OpenLibrary"
        return "TMDB"

    def _set_book_comic_type(self, entry, is_comic: bool):
        """Cambia a mano si *entry* se trata como libro de texto (OpenLibrary/
        Google Books) o cómic/manga (ComicVine) -- la extensión sola no basta
        para decidirlo (un .pdf/.epub/.cbz/.cbr puede ser cualquiera de los dos
        en la práctica, p.ej. un cómic escaneado en PDF), así que es una
        elección del usuario por archivo (ver menú contextual en
        _show_row_menu), no una detección automática. Por defecto
        (is_comic_file en core/renamer.py) .pdf/.epub/.mobi/.azw3 se tratan
        como libro y .cbz/.cbr como cómic -- esto solo anula esa
        clasificación por defecto para ESTE archivo.

        Recalcula entry.detected (is_comic cambia si detect_episode busca
        el patrón "#NN" del número de emisión, ver core/api_client.py) y
        limpia cualquier identificación previa -- viniera de OpenLibrary,
        Google Books o de ComicVine, ya no vale para el servicio nuevo --
        antes de relanzar la búsqueda con el servicio correcto."""
        if not entry.is_book or entry.is_comic == is_comic:
            return
        entry.is_comic = is_comic
        entry.detected = detect_episode(entry.name, is_book=entry.is_book, is_comic=entry.is_comic,
                                         folder_hint=Path(entry.path).parent.name)
        entry.media_info = None
        entry.new_name   = ""
        entry.status     = "pendiente"
        entry.confidence = 0
        entry.error_msg  = ""
        self._update_row(entry)
        if self._selected_entry is entry:
            self._reset_search_panel(entry)
            self._update_detail(entry)
        self._search_new_entries([entry])

    def _detail_meta_for(self, info: MediaInfo, vote_average: float = 0) -> str:
        """Línea de categoría del panel de detalle de Archivos -- tipo · año ·
        nota · géneros, mismas piezas que la línea "meta" del panel lateral
        de Recomendado. Los géneros se resuelven de genre_ids contra la caché
        de géneros TMDB (_genres_cache, cargada en Ajustes); si la caché
        todavía no está (p.ej. el usuario nunca abrió Ajustes) se muestra la
        línea sin géneros, no se bloquea nada esperando."""
        tipo = {"tv": "📺 Serie", "movie": "🎬 Película", "libro": "📚 Libro"}.get(
            info.media_type, "🎬 Película")
        meta = " · ".join(x for x in [
            tipo,
            info.year or "",
            f"⭐ {vote_average:.1f}" if vote_average else "",
        ] if x)
        genres = self._genre_names_for(info.media_type, info.genre_ids)
        if genres:
            meta = (meta + " · " if meta else "") + ", ".join(genres)
        return meta

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

    def _rename_selected(self):
        entry = self._selected_entry
        if not entry or not entry.new_name:
            self._set_status("Selecciona un archivo con nombre detectado", WARNING_COLOR)
            return
        if not self.config_data.get("rename_local", True):
            # Mismo criterio que _rename_worker -- no tocar el disco si
            # está desactivado en Ajustes, el nombre calculado se queda
            # listo igualmente para la subida.
            self._set_status(
                "Renombrado en origen desactivado en Ajustes -- se mantiene el nombre original en disco",
                WARNING_COLOR)
            return
        ok, msg = rename_file(entry.path, entry.new_name)
        if ok:
            entry.path   = msg
            entry.status = "renombrado"
            self._set_status(f"Renombrado: {entry.new_name}", SUCCESS_COLOR)
        else:
            entry.status    = "error"
            entry.error_msg = msg
            self._set_status(f"Error: {msg}", ERROR_COLOR)
        self._update_row(entry)

    def _upload_all_ftp(self):
        ready = [e for e in self.files if e.status in ("listo", "renombrado") and e.media_info]
        if not ready:
            self._set_status("No hay archivos listos para subir", WARNING_COLOR)
            return
        self._enqueue_or_start(ready)

    def _upload_selected_ftp(self):
        # Soporta multi-selección (Ctrl/Shift) igual que Asignar
        sel = self._current_selection()
        targets = [e for e in sel if e.media_info and e.status in ("listo", "renombrado")]
        if not targets:
            # Fallback al ancla si la selección no tiene listos pero el ancla sí
            entry = self._selected_entry
            if entry and entry.media_info and entry.status in ("listo", "renombrado"):
                targets = [entry]
        if not targets:
            # Mensaje más informativo: cuántos tenían media_info y en qué estado estaban
            n_con_info = sum(1 for e in sel if e.media_info)
            estados = {}
            for e in sel:
                estados[e.status] = estados.get(e.status, 0) + 1
            detalle = ", ".join(f"{k}:{v}" for k, v in estados.items()) if estados else "sin selección"
            if n_con_info == 0:
                self._set_status("Ningún archivo seleccionado tiene información TMDB asignada — usa Asignar primero", WARNING_COLOR)
            else:
                self._set_status(f"Ningún seleccionado en estado listo/renombrado ({detalle}) — revisa Asignar", WARNING_COLOR)
            return
        self._enqueue_or_start(targets)

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
        self._update_row(entry)
        self._refresh_ftp_columns()

    def _session_path(self) -> Path:
        return _appdata_dir() / "session.json"

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

    def _provider_search(self, query: str, entry, override):
        """Búsqueda manual del panel de detalle: (tipo_de_resultados,
        resultados). *override* es el proveedor forzado con el selector
        ("tmdb"/"openlibrary"/"google_books"/"comicvine"/"mangadex"/"anilist"/
        "kitsu") o None: cómics -> ComicVine; libros -> OpenLibrary con Google
        Books de respaldo; si no, TMDB. Llamar desde un hilo."""
        is_comic = bool(entry and entry.is_comic)
        is_book_only = bool(entry and entry.is_book and not entry.is_comic)
        # Lista simple: TMDB/OpenLibrary/GoogleBooks/ComicVine/MangaDex/AniList/Kitsu
        if override == "tmdb":
            kind = "tmdb"
            results = self.tmdb.search_multi(query)
        elif override == "openlibrary":
            kind = "openlibrary"
            results = self.openlibrary_client.search_volumes(query)
        elif override == "google_books":
            kind = "book"
            results = self.book_client.search_volumes(query)
        elif override == "comicvine":
            kind = "comic"
            results = self.comicvine.search_volumes(query)
        elif override == "mangadex":
            kind = "mangadex"
            results = self.mangadex_client.search_volumes(query)
        elif override == "anilist":
            kind = "anilist"
            results = self.anilist_client.search_volumes(query)
        elif override == "kitsu":
            kind = "kitsu"
            results = self.kitsu_client.search_volumes(query)
        elif is_comic:
            kind = "comic"
            results = self.comicvine.search_volumes(query)
        elif is_book_only:
            # OpenLibrary primero (sin key, más fiable ahora mismo que
            # Google Books, ver core/book_identify.py) -- Google Books de
            # apoyo si no encuentra nada o falla.
            kind = "openlibrary"
            try:
                results = self.openlibrary_client.search_volumes(query)
            except Exception:
                results = []
            if not results:
                kind = "book"
                results = self.book_client.search_volumes(query)
        else:
            kind = "tmdb"
            results = self.tmdb.search_multi(query)
        return kind, results

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

    @staticmethod
    def _fmt_free_space(free: int) -> str:
        # Coma como separador decimal (es-ES), no punto.
        for unit, divisor in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2)):
            if free >= divisor:
                return f"{free / divisor:.1f} {unit}".replace(".", ",")
        return f"{free} B"

    def _disks_from_categories(self) -> dict:
        """Un disco por cada primer segmento de ruta distinto entre todas
        las categorías FTP configuradas (p.ej. "datos" en "/datos/peliculas/",
        "datos2" en "/datos2/series/") -- así, si varias categorías comparten
        disco (Series y SeriesPeques bajo /datos2/), no se consulta ni se
        muestra dos veces. Devuelve {nombre_disco: root_representativo}."""
        cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []})
        roots = [c.get("root", "") for c in cats.get("tv", []) + cats.get("movie", [])]
        disks = {}
        for root in roots:
            segments = [s for s in (root or "").replace("\\", "/").split("/") if s]
            if not segments:
                continue
            disks.setdefault(segments[0], root)
        return disks
