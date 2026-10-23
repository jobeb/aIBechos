"""
Archivos, parte de la subida: la cola (manual y del modo automático), la
subida de cada archivo por FTP/SFTP con su progreso y reintentos, el
espacio libre y en qué carpeta del servidor va cada cosa (categorías,
carpeta de la serie ya existente). Mixin de FilesCoreMixin (ver
core/app_files_core.py, que documenta los ganchos de interfaz que usa).
"""

import threading
import time as _time
from pathlib import Path

from core.applog import get_logger
from core.fmt import fmt_size as _fmt_size
from core.ftp_categories import choose_category
from core.ftp_client import _ftp_safe
from core.series_match import best_match, series_similarity
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class FilesUploadMixin:
    @staticmethod
    def _unqueue_status(entry, prev_status: str) -> str:
        """Estado al omitir una subida (X u Omitir en un diálogo): nunca un
        estado transitorio de cola. Restaurar "en_cola" o
        "esperando_confirmacion" dejaba la fila muerta -- ni el ▲ (solo
        activo si status != "en_cola") ni Subir (solo "listo"/"renombrado")
        la aceptaban, y al reiniciar seguía igual (real: Andrómeda 2x05
        tras cerrar el diálogo de sustituir con la X). Vuelve a un estado
        accionable; el resto de estados se respeta tal cual."""
        if prev_status in ("en_cola", "esperando_confirmacion", "subiendo"):
            return "listo" if getattr(entry, "media_info", None) else "pendiente"
        return prev_status

    @staticmethod
    def _wait_dialog(ev: threading.Event, what: str, timeout: float = 30.0) -> bool:
        """Espera la respuesta de un diálogo de la GUI sin colgarse
        eternamente si esta no llega (hilo GUI atascado, factoría que
        devuelve None...): True si contestó. Con False el llamador aplica
        su valor seguro (lo mismo que cerrar con la X)."""
        ok = ev.wait(timeout)
        if not ok:
            _log.warning("Diálogo %s sin respuesta en %ds: se suelta como cerrado",
                         what, int(timeout))
        return ok

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
        self._upload_duplicate_replace_all = False
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
            # Marcas por entrada de la tanda anterior (diálogos ya contestados,
            # tamaños remotos viejos): si no, la segunda tanda no repregunta
            # "resto antiguo"/duplicados y calcula stalled con datos rancios.
            entry._stale_checked = False
            entry._duplicate_checked = False
            entry._last_upload_remote_size = 0
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
                            self._wait_dialog(ev, "series-match")
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
        # tiene sentido si la tanda EMPEZÓ con más de un archivo (tamaño
        # inicial, no la cola viva: esta crece con encolados en caliente
        # y "restos" recogidos a mitad de tanda -- con un solo ▲ salían
        # las 4 opciones). Igual para el diálogo de sobrescribir de abajo.
        # Sin _upload_batch_size (vía automática) se mantiene lo de antes.
        is_batch = (getattr(self, "_upload_batch_size", 0) or len(self._upload_queue)) > 1

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
                self._wait_dialog(ev, "stale-upload")
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
            from core.duplicate_detect import find_duplicate
            existing_files = ftp_conn.list_files_checked(remote_dir)
            if existing_files is None:
                # Listado fallido: [] habría pasado por "sin duplicados" y
                # marcado la entrada como comprobada para siempre. Se falla
                # esta subida (reintentable) sin marcarla.
                return False, "No se pudo listar la carpeta del servidor para comprobar duplicados"
            entry._duplicate_checked = True
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
                self._wait_dialog(ev, "overwrite")
                if answer[0] is None:
                    answer[0] = "skip"   # como cerrar con la X: jamás sobreescribir por defecto
                if answer[0] == "all":
                    self._upload_overwrite_all = True
                    force_overwrite = True
                elif answer[0] == "skip_all":
                    # "Omitir todos": los demás workers de la tanda que
                    # lleguen aquí ven el flag y se omiten sin preguntar.
                    self._upload_skip_all = True
                    entry.status = self._unqueue_status(entry, prev_status)
                    self.after(0, lambda e=entry: self._update_row(e))
                    self._save_skip_history(entry, remote_file, local_size,
                                            "Ya existía en el servidor (omitido a mano)")
                    return True, "omitido"
                elif answer[0] == "skip":
                    # Omitir o cerrar el diálogo (por defecto también "skip"):
                    # vuelve a un estado accionable, no al transitorio de
                    # cola -- es un "ahora no", reintentable más adelante.
                    entry.status = self._unqueue_status(entry, prev_status)
                    self.after(0, lambda e=entry: self._update_row(e))
                    self._save_skip_history(entry, remote_file, local_size,
                                            "Ya existía en el servidor (omitido a mano)")
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
                self._save_skip_history(entry, remote_file, local_size,
                                        "Ya existía en el servidor (\"Omitir todos\")")
                return True, "omitido"
            else:
                # El archivo con el nombre exacto NO está completo: si además
                # de la comprobación de "ya existe" de arriba se encontró un
                # duplicado real (el mismo contenido con otro nombre) hay que
                # preguntar igualmente si se sube o no.
                if dup and self._upload_duplicate_replace_all:
                    # "Sustituir todos" ya contestado en esta tanda.
                    entry._replace_duplicate = f"{remote_dir.rstrip('/')}/{dup}"
                elif dup and not self._upload_duplicate_ignore_all:
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
                            overwrite_label="Subir y conservar los dos",
                            all_label="Subir todas y conservar los dos",
                            replace_label="Sustituir",
                            replace_all_label="Sustituir todos",
                            note=("Sustituir: se sube el nuevo y, solo si la subida "
                                  "termina bien, se borra el de arriba."),
                            close_result="skip",
                            show_all_button=is_batch)
                        ans[0] = dlg.result
                        e.set()
                    self.after(0, _ask)
                    self._wait_dialog(ev, "duplicate")
                    if answer[0] in ("replace", "replace_all"):
                        # Se borra DESPUÉS de subir bien (ver
                        # _finish_duplicate_replacement): con un fallo a
                        # medias el viejo sigue ahí. Va en la entrada para
                        # sobrevivir a los reintentos (_duplicate_checked
                        # impide volver a detectarlo).
                        entry._replace_duplicate = f"{remote_dir.rstrip('/')}/{dup}"
                        if answer[0] == "replace_all":
                            self._upload_duplicate_replace_all = True
                    elif answer[0] == "all":
                        self._upload_duplicate_ignore_all = True
                    elif answer[0] != "overwrite":   # "skip" (Omitir o cerrado con la X)
                        entry.status = self._unqueue_status(entry, prev_status)
                        self.after(0, lambda e=entry: self._update_row(e))
                        self._save_skip_history(entry, remote_file, local_size,
                                                f"Ya había otro archivo con el mismo contenido: {dup}")
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
                    self._wait_dialog(ev, "stalled")
                    if answer[0] == "all":
                        self._upload_overwrite_all = True
                        force_overwrite = True
                    elif answer[0] == "overwrite":
                        force_overwrite = True
                    else:   # "skip" (Omitir o cerrado con la X)
                        entry.status = self._unqueue_status(entry, prev_status)
                        self.after(0, lambda e=entry: self._update_row(e))
                        self._save_skip_history(entry, remote_file, local_size,
                                                "La subida no avanzaba en el servidor (omitida a mano)")
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

        # Igual que AutoWatcher: si lo que se sube es algo pedido desde la
        # web, el worker de solicitudes publica este % (y no da la
        # solicitud por hecha a media subida). Antes solo lo hacía
        # AutoWatcher, y al subirlo a mano la web se quedaba en el último
        # % de descarga.
        from core import upload_progress as _up
        _mi = entry.media_info
        _up_key = (getattr(_mi, "media_type", ""), getattr(_mi, "tmdb_id", 0),
                   getattr(_mi, "season", None), getattr(_mi, "episode", None))

        def progress(sent, total_b, spd, e=entry):
            e.ftp_progress = sent / total_b if total_b > 0 else 0
            e.ftp_speed    = spd
            _up.report(*_up_key, e.ftp_progress)
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
            _up.discard(*_up_key)

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
            self._finish_duplicate_replacement(entry, remote_file)
            self._apply_manual_post_process_action(entry)
        elif msg == "cancelado":
            entry.status = "listo"
            self._ftp_row_set(entry, "Cancelado", entry.ftp_progress, 0)
            self._save_skip_history(entry, remote_file, local_size, "Subida cancelada a mano",
                                    status="cancelado")
            _log.info("Subida: cancelada por el usuario %r", remote_filename)
        elif msg == "saltado":
            entry.status = "listo"
            self._ftp_row_set(entry, "Saltado", 0, 0)
            self._save_skip_history(entry, remote_file, local_size, "Saltada a mano durante la subida")
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

    def _save_skip_history(self, entry, remote_file: str, size, reason: str,
                           status: str = "omitido"):
        """Apunta en el Historial una subida que no se hizo (omitida o
        cancelada), con su motivo -- antes solo constaban las correctas y
        los errores."""
        try:
            self._save_history_entry(entry.name, remote_file, status, size or 0,
                                     error_msg=reason, local_path=entry.path)
        except Exception:
            _log.warning("Historial: no se pudo apuntar %r como %s", entry.name, status, exc_info=True)

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
            self._upload_batch_size = 0
            self._upload_queue = []
            self._upload_slot_of = {}
            self._upload_skip_events = []
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
                        self._upload_batch_size = len(self._upload_queue)
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

        # Lo no procesado vuelve a estado accionable (cancelada a medias o
        # tanda incompleta): "en_cola" sin tanda viva es fila muerta.
        for e in list(self._upload_queue or []):
            try:
                if getattr(e, "status", "") == "en_cola":
                    e.status = self._unqueue_status(e, "en_cola")
                    self.after(0, lambda en=e: self._update_row(en))
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
        self._upload_batch_size     = 0
        self._upload_queue          = []
        self._upload_slot_of        = {}
        self._upload_skip_events    = []
        cancelled = self._upload_cancel.is_set()
        self.after(0, lambda: self._set_status(
            "Subida cancelada" if cancelled else "Subida completada",
            WARNING_COLOR if cancelled else SUCCESS_COLOR))
        self.after(0, self._restore_ftp_row_buttons)

    def _upload_one(self, entry):
        """Añade el archivo a la cola. Si hay subida activa lo encola; si no, la inicia."""
        if self._upload_running:
            if entry not in self._upload_queue:
                self._reserve_manual_entries([entry])
                self._upload_queue.append(entry)
                self._upload_batch_size = len(self._upload_queue)
                entry.ftp_progress = 0.0
                entry.ftp_speed    = 0.0
                entry.ftp_status   = "En espera"
                entry.status       = "en_cola"
                self._update_row(entry)
                self.after(0, self._refresh_ftp_columns)
                self._set_status(f"Encolado: {entry.name[:50]}", PENDING_COLOR)
            return
        self._start_ftp_upload([entry])

    def _finish_duplicate_replacement(self, entry, uploaded_remote: str):
        """Tras subir bien un archivo con "Sustituir" en el diálogo de
        duplicado: borra el viejo (mismo episodio/película con otro nombre,
        p.ej. otra extensión .mp4 -> .mkv). Antes solo se podía "Subir de
        todas formas" y quedaban los dos en el servidor. Si el borrado
        falla, lo nuevo ya está subido: se avisa para borrarlo a mano."""
        old = getattr(entry, "_replace_duplicate", None)
        if not old:
            return
        try:
            del entry._replace_duplicate
        except AttributeError:
            pass
        if old == uploaded_remote:
            return
        try:
            ok_del, msg_del = self._delete_remote_file(old)
        except Exception as e:
            ok_del, msg_del = False, str(e)
        old_name = old.rsplit("/", 1)[-1]
        if ok_del:
            _log.info("Sustituido: borrado %s, en servidor %s", old, uploaded_remote)
            self.after(0, lambda n=old_name: self._set_status(f"Sustituido: borrado {n}", SUCCESS_COLOR))
        else:
            _log.warning("Sustitución subida pero no se pudo borrar %s (%s): bórralo a mano",
                         old, msg_del)
            self.after(0, lambda n=old_name, m=msg_del: self._set_status(
                f"Subido, pero no se pudo borrar {n}: {m}", ERROR_COLOR))

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

    def _enqueue_or_start(self, entries: list):
        """Encola si ya hay subida en curso, si no inicia nueva tanda."""
        if self._upload_running:
            added = 0
            for e in entries:
                if e not in self._upload_queue and e.status in ("listo", "renombrado"):
                    self._reserve_manual_entries([e])
                    self._upload_queue.append(e)
                    self._upload_batch_size = len(self._upload_queue)
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

    def _on_header_upload_clicked(self):
        n = len(self._current_selection())
        if n > 1:
            self._upload_selected_ftp()
        else:
            self._upload_all_ftp()

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
