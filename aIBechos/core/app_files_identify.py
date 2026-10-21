"""
Archivos, parte de la identificación: buscar cada archivo (TMDB, libros,
cómics/manga, con apoyo de IA), el proveedor elegido a mano, asignar un
resultado a uno o varios archivos y renombrar en disco. Mixin de
FilesCoreMixin (ver core/app_files_core.py, que documenta los ganchos de
interfaz que usa).
"""

import difflib
import threading
from pathlib import Path

from core.api_client import MediaInfo, detect_episode
from core.applog import get_logger
from core.mangadex_client import MangaDexClient
from core.renamer import build_name_for_media_info, rename_file
from core.status_colors import ERROR_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class FilesIdentifyMixin:
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
        from core.ai_title_fallback import guess_title_via_ai, resolve_model
        stem = entry.name[:-len(entry.ext)] if entry.ext else entry.name
        ai_result = guess_title_via_ai(stem, api_key, model=resolve_model(self.config_data))
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
            ai_api_key=self.config_data.get("ai_api_key", ""),
            ai_model=(self.config_data.get("ai_model", "") or "").strip())
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

        self._rename_overwrite_all = False   # cada tanda decide sus "todos"
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
                self._wait_dialog(ev, "rename-local")
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

    def _effective_search_provider(self, entry) -> str:
        """Proveedor que se usaría de verdad ahora mismo: el del selector,
        o la cadena automática (cómic→ComicVine, libro→OpenLibrary, si no
        TMDB) con "auto"."""
        override = self._search_provider_override
        if override is not None and override != "auto":
            return override
        if entry is not None and getattr(entry, "is_comic", False):
            return "comicvine"
        if entry is not None and getattr(entry, "is_book", False):
            return "openlibrary"
        return "tmdb"

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

    def _provider_search(self, query: str, entry, override):
        """Búsqueda manual del panel de detalle: (tipo_de_resultados,
        resultados). *override* es el proveedor forzado con el selector
        ("tmdb"/"openlibrary"/"google_books"/"comicvine"/"mangadex"/"anilist"/
        "kitsu"); None o "auto" usa la cadena automática: cómics ->
        ComicVine; libros -> OpenLibrary con Google Books de respaldo; si
        no, TMDB. Llamar desde un hilo."""
        is_comic = bool(entry and entry.is_comic)
        is_book_only = bool(entry and entry.is_book and not entry.is_comic)
        if override is None or override == "auto":
            override = "comicvine" if is_comic else "openlibrary" if is_book_only else "tmdb"
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
