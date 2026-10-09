"""
Lógica sin interfaz de la pestaña "Recomendado" (películas y series que
recomendar según lo que se ve en el servidor): escaneo, caché local y
compartida por FTP, descartar una recomendación y marcar como "ya en el
servidor" lo recién subido.

Mixin que hereda QtAppCore (gui_qt/core_host.py). Ganchos de interfaz: _render_movies_table(reset_page=...),
_update_movies_status_text(), _refresh_movies_genre_filter_options(); estado:
_movies_results, _movies_selected_tmdb_id, _movies_visible.
"""

import threading
import time as _time

from core import shared_data
from core.api_client import TMDBClient
from core.applog import get_logger
from core.status_colors import PENDING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class MoviesCoreMixin:
    # Constante de clase (antes en App, gui/app.py)
    _MISSING_MOVIES_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_missing_movies_from_ftp


    def _missing_movies_remote_path(self) -> str:
        """Ruta remota de la caché compartida de "Películas" (ver
        core/missing_movies_cache.py) -- archivo propio dentro de la misma
        carpeta compartida, mismo motivo que
        _missing_episodes_remote_path. La caché de películas era personal
        de cada instalación por decisión de diseño (el cruce con el
        servidor de medios local no tenía sentido compartirlo); ahora el
        flag "in_server" SÍ se comparte, para que un cliente sepa que otra
        instalación del mismo servidor ya subió una película sin tener que
        esperar a que su propio Jellyfin/Plex la reindexe -- ver
        _push_missing_movies_to_ftp/_sync_missing_movies_from_ftp."""
        return self._shared_data_path(shared_data.filename("peliculas"))

    def _push_missing_movies_to_ftp(self):
        """Comparte la caché ACTUAL de "Películas" (ver
        core/missing_movies_cache.py) -- llamado tras cualquier cambio
        local que la deje al día (una película subida, un escaneo que
        detecta que ya está en el servidor). A diferencia de episodios,
        aquí no se reemplaza la caché remota entera: se hace MERGE con lo
        que ya hubiera publicado otro cliente, conservando los datos de
        cada película (título, cartel, watch...) de la versión más completa
        y combinando el flag "in_server" con OR (si cualquiera de los dos
        clientes dice que la película ya está en su servidor, queda
        marcada como en servidor -- así una instalación cuya biblioteca
        todavía no ha reindexado la película no la "desmarca" para las
        demás)."""
        remote_path = self._missing_movies_remote_path()
        if not remote_path:
            return

        def worker():
            from core.missing_movies_cache import load_cache, merge_movies_cache
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
                        "Películas: no se pudo conectar al FTP para compartir la caché (%s)", _msg)
                    return
                remote_cache = {}
                raw = own_ftp.download_bytes(remote_path)
                if raw:
                    try:
                        parsed = _json.loads(raw.decode("utf-8"))
                        if isinstance(parsed, dict):
                            remote_cache = parsed
                    except ValueError:
                        remote_cache = {}
                payload = merge_movies_cache(load_cache(), remote_cache)
                data = _json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, _up_msg = own_ftp.upload_bytes(data, remote_path)
                if up_ok:
                    _log.info("Películas: caché compartida con %d película(s)",
                              len([k for k in payload if k != "_meta"]))
                else:
                    _log.warning("Películas: no se pudo subir la caché compartida (%s)", _up_msg)
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_missing_movies(self, remote_cache: dict):
        """Aplica una caché de "Películas" ya sincronizada (de
        _sync_missing_movies_from_ftp) -- propaga sobre el mirror local el
        flag "in_server=True" de lo que haya publicado otro cliente del
        mismo servidor (si otro cliente ya subió una película, se marca
        como en servidor aquí también, aunque este Jellyfin/Plex aún no la
        haya reindexado), la guarda, y reconstruye self._movies_results con
        la MISMA función que ya usa el arranque (_rows_from_movies_cache).
        NO añade ni borra entradas (ver apply_remote_in_server): un
        descarte local con 🚫 no debe reaparecer por el solo hecho de que
        otro cliente la tenga compartida. Solo si el merge trajo algo
        nuevo de verdad y la pestaña Películas está visible se repinta la
        tabla."""
        from core.missing_movies_cache import load_cache, save_cache, apply_remote_in_server
        previous = load_cache()
        merged, changed = apply_remote_in_server(previous, remote_cache)
        if not changed:
            return
        try:
            save_cache(merged)
        except Exception:
            _log.warning("Películas: no se pudo guardar el mirror local tras sincronizar",
                         exc_info=True)
            return
        # Refrescar _movies_results SIEMPRE (aunque la pestaña no esté
        # visible ahora): el sync de arranque/volver a la app puede
        # aplicarse antes de que el usuario abra la pestaña, y así al
        # entrar ya muestra el flag en servidor actualizado. El repintado
        # de la tabla sí se condiciona a que la pestaña esté visible.
        self._movies_results = self._rows_from_movies_cache()
        self._refresh_movies_genre_filter_options()
        if getattr(self, "_movies_visible", False):
            self._render_movies_table(reset_page=False)
            self._update_movies_status_text()

    def _sync_missing_movies_from_ftp(self):
        """Refresca el mirror local de "Películas" desde el FTP en segundo
        plano -- mismo patrón y mismo freno que
        _sync_missing_episodes_from_ftp. Se llama al entrar en la pestaña
        Películas y también al abrir la app / volver a ella (ver
        _on_root_focus_in), para que un cliente vea qué películas ya han
        subido los demás sin tener que salir y entrar."""
        now = _time.time()
        if now - self._last_missing_movies_sync_ts < self._MISSING_MOVIES_SYNC_MIN_INTERVAL:
            return
        self._last_missing_movies_sync_ts = now

        remote_path = self._missing_movies_remote_path()
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
                    remote_cache = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    return
                if not isinstance(remote_cache, dict):
                    return
                self.after(0, lambda: self._apply_synced_missing_movies(remote_cache))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _rows_from_movies_cache(self) -> list:
        """Al abrir la vista, mostrar lo que ya se sabía del último escaneo
        (persistido en missing_movies_cache.json) en vez de una tabla vacía
        hasta que el usuario pulse "🔍 Recomendar" a mano -- mismo patrón que
        _load_missing_episodes_from_cache. Devuelve filas con el MISMO
        formato que build_movie_rows (tmdb_id, media_type, title, year,
        release_date, overview, poster_url, genre_ids, vote_average,
        popularity, list, in_server, más "genres" para el detalle). Las
        claves de la caché son "{media_type}:{tmdb_id}" (las de versiones
        antiguas, numéricas, ya las migra normalize_cache al cargar)."""
        from core.missing_movies_cache import load_cache, parse_cache_key
        cache = load_cache()
        results = []
        for key, entry in cache.items():
            parsed = parse_cache_key(key)
            if parsed is None:
                continue   # "_meta" y cualquier otra clave no válida
            media_type, tmdb_id = parsed
            results.append({
                "tmdb_id": tmdb_id, "media_type": media_type,
                "title": entry.get("title", ""),
                "year": entry.get("year", ""), "release_date": entry.get("release_date", ""),
                "overview": entry.get("overview", ""), "poster_url": entry.get("poster_url"),
                "genre_ids": list(entry.get("genre_ids", []) or []),
                "genres": entry.get("genres", []) or [],
                "vote_average": entry.get("vote_average", 0),
                "popularity": entry.get("popularity", 0),
                "list": entry.get("list", ""), "in_server": entry.get("in_server", False),
                "watch": dict(entry.get("watch", {}) or {}),
                "certification": entry.get("certification", ""),
                "original_language": entry.get("original_language", ""),
                "origin_country": list(entry.get("origin_country", []) or []),
            })
        return results

    def _scan_missing_movies(self, progress_cb=None, cancel_event=None, force_full=False) -> list:
        """Pide las listas de TMDB (películas: tendencias, populares,
        próximos estrenos, en emisión; series: tendencias, populares, en
        emisión), las cruza con lo que YA está en Jellyfin/Plex (por
        tmdb_id, cruzando películas con películas y series con series) y
        consulta a TMDB la disponibilidad fuera del cine de cada película
        candidata (watch providers, para el filtro "Solo disponibles en
        plataformas") -- devuelve la lista de filas de recomendación (ver
        core.missing_movies.build_movie_rows), guardando el resultado en
        missing_movies_cache.json para que la pestaña muestre las
        recomendaciones aunque se abra sin conexión.

        force_full fuerza a re-consultar también la disponibilidad de las
        películas que ya la tenían en la caché (el botón "Reescaneo
        completo"); sin él, solo se consulta la de las películas nuevas o
        sin datos guardados (la parte lenta es una llamada TMDB por
        película, y las listas no cambian cada día). Las series NO
        consultan watch providers (el filtro de plataformas es solo de
        películas)."""
        from core.missing_movies import build_movie_rows
        from core.missing_movies_cache import (load_cache, save_cache, cache_key)
        from core.media_server_refresh import (get_jellyfin_movies, get_plex_movies,
                                               get_jellyfin_series, get_plex_series)

        def _report(cur, total, name):
            if progress_cb:
                progress_cb(cur, total, name)

        # tmdb_ids de lo que YA está en el servidor (solo los que traigan el
        # ID emparejado; los demás no se pueden cruzar). Las películas y las
        # series viven en espacios de IDs separados en TMDB, así que el cruce
        # es por tipo: una película y una serie pueden compartir el mismo
        # número de tmdb_id sin ser la misma obra.
        server_movie_ids = set()
        server_series_ids = set()
        if self.config_data.get("jellyfin_enabled"):
            for m in (get_jellyfin_movies(
                    self.config_data.get("jellyfin_host", ""),
                    self.config_data.get("jellyfin_api_key", "")) or []):
                if m.get("tmdb_id"):
                    server_movie_ids.add(m["tmdb_id"])
            for s in (get_jellyfin_series(
                    self.config_data.get("jellyfin_host", ""),
                    self.config_data.get("jellyfin_api_key", "")) or []):
                if s.get("tmdb_id"):
                    server_series_ids.add(s["tmdb_id"])
        if self.config_data.get("plex_enabled"):
            for m in (get_plex_movies(
                    self.config_data.get("plex_host", ""),
                    self.config_data.get("plex_token", "")) or []):
                if m.get("tmdb_id"):
                    server_movie_ids.add(m["tmdb_id"])
            for s in (get_plex_series(
                    self.config_data.get("plex_host", ""),
                    self.config_data.get("plex_token", "")) or []):
                if s.get("tmdb_id"):
                    server_series_ids.add(s["tmdb_id"])

        # Cliente TMDB propio del escaneo (no self.tmdb) para usar la API key
        # ACTUAL de la configuración, por si cambió sin reiniciar.
        client = TMDBClient(self.config_data.get("tmdb_api_key", ""))
        # Listas por separado: las series usan las mismas claves internas que
        # las películas ("trending"/"popular" + "on_the_air"), así que si se
        # guardaran en el mismo dict las series sobrescribirían las películas
        # (y las series de trending/popular saldrían DOS veces, una en cada
        # cruce). build_movie_rows recibe cada tipo por separado.
        movie_lists = {}
        series_lists = {}
        # 10 páginas fijas por lista (~20 películas por página de TMDB) --
        # se eliminó el selector "Páginas por lista" y este valor quedó fijo.
        pages = 10
        steps = [
            ("trending", "Tendencias TMDB", client.get_trending_movies),
            ("popular", "Populares", client.get_popular_movies),
            ("upcoming", "Próximos estrenos", client.get_upcoming_movies),
            ("now_playing", "En emisión", client.get_now_playing_movies),
        ]
        for list_key, label, fetch in steps:
            if cancel_event and cancel_event.is_set():
                break   # cancelado -- devolver lo recopilado hasta ahora
            _report(0, 1, label)
            movies = []
            for page in range(1, pages + 1):
                if cancel_event and cancel_event.is_set():
                    break
                try:
                    page_movies = fetch(page=page)
                except Exception:
                    _log.warning("Recomendado: fallo al pedir la lista '%s' (pág %d) de TMDB -- se sigue con el resto",
                                 label, page)
                    page_movies = []
                movies.extend(page_movies)
                # TMDB devuelve 20 por página; si la página sale incompleta
                # es la última de la lista, no tiene sentido seguir pidiendo.
                if len(page_movies) < 20:
                    break
            movie_lists[list_key] = movies

        # Listas de series: las 3 que tienen sentido para recomendar cosas
        # nuevas (tendencias, populares, en emisión). Ni "próximos estrenos"
        # (de películas) ni "mejor valoradas" -- son series en curso o
        # recientes, que es lo que tiene sentido recomendar.
        series_steps = [
            ("trending", "Tendencias TMDB (series)", client.get_trending_tv),
            ("popular", "Populares (series)", client.get_popular_tv),
            ("on_the_air", "En emisión (series)", client.get_on_the_air_tv),
        ]
        for list_key, label, fetch in series_steps:
            if cancel_event and cancel_event.is_set():
                break
            _report(0, 1, label)
            shows = []
            for page in range(1, pages + 1):
                if cancel_event and cancel_event.is_set():
                    break
                try:
                    page_shows = fetch(page=page)
                except Exception:
                    _log.warning("Recomendado: fallo al pedir la lista de series '%s' (pág %d) de TMDB -- se sigue con el resto",
                                 label, page)
                    page_shows = []
                shows.extend(page_shows)
                if len(page_shows) < 20:
                    break
            series_lists[list_key] = shows

        # Filas de películas y de series -- build_movie_rows deduce el tipo de
        # cada resultado (media_type); el cruce contra el servidor se hace por
        # tipo (los tmdb_ids de películas y series no comparten espacio).
        movie_rows = build_movie_rows(movie_lists, server_movie_ids)
        series_rows = build_movie_rows(series_lists, server_series_ids)
        rows = movie_rows + series_rows

        # Nombres de género para el detalle -- una sola llamada por tipo, no
        # una por película/serie.
        genres_by_id = {}
        for media_type in ("movie", "tv"):
            try:
                for gid, gname in {g.get("id"): g.get("name", "") for g in client.get_genres(media_type)}.items():
                    genres_by_id[gid] = gname
            except Exception:
                _log.warning("Recomendado: no se pudieron pedir los géneros de %s de TMDB", media_type)

        # Disponibilidad fuera del cine (watch providers) para el filtro
        # "Solo disponibles en plataformas" -- SOLO películas (las series no
        # pasan por ese filtro). Incremental por caché, igual que el resto de
        # la app: las películas que ya tienen "watch" en
        # missing_movies_cache.json se reutilizan tal cual (una llamada
        # TMDB por película, y las listas no cambian cada día); solo se
        # consultan las nuevas/sin datos. force_full las re-consulta todas.
        # La barra de progreso cubre solo esta parte (la lenta); las listas
        # y los géneros son pocas llamadas y ya se reportaron con total=1.
        cache = dict(load_cache())
        watch_by_id = {}
        titles_by_id = {r["tmdb_id"]: r["title"] for r in movie_rows}
        missing_watch = []
        for r in movie_rows:
            cached_watch = (cache.get(cache_key("movie", r["tmdb_id"])) or {}).get("watch")
            if cached_watch and not force_full:
                watch_by_id[r["tmdb_id"]] = cached_watch
            else:
                missing_watch.append(r["tmdb_id"])
        total_watch = len(missing_watch)
        for i, tid in enumerate(missing_watch, start=1):
            if cancel_event and cancel_event.is_set():
                break   # cancelado -- devolver lo consultado hasta ahora
            _report(i, total_watch, f"Disponibilidad de '{titles_by_id.get(tid, tid)}'...")
            try:
                watch_by_id[tid] = client.get_movie_watch_providers(tid)
            except Exception:
                _log.warning("Recomendado: fallo al pedir la disponibilidad de tmdb_id=%s -- se sigue con el resto", tid)
                watch_by_id[tid] = {}

        rows = build_movie_rows(movie_lists, server_movie_ids, watch_by_id=watch_by_id) + series_rows
        # "genres" (nombres en el idioma configurado) se resuelven AQUÍ, una
        # sola vez por tipo, y viajan en las filas igual que en la caché --
        # el filtro por género de la barra de filtros los lee de ahí (ver
        # _movies_visible_rows), sin consultas extra por fila.
        for r in rows:
            r["genres"] = [genres_by_id.get(gid, "") for gid in r["genre_ids"]]

        cache = dict(load_cache())
        for r in rows:
            key = cache_key(r["media_type"], r["tmdb_id"])
            cached_entry = cache.get(key) or {}
            cache[key] = {
                "media_type": r["media_type"],
                "title": r["title"], "year": r["year"], "release_date": r["release_date"],
                "overview": r["overview"], "poster_url": r["poster_url"],
                "genres": [genres_by_id.get(gid, "") for gid in r["genre_ids"]],
                "genre_ids": r["genre_ids"], "vote_average": r["vote_average"],
                "popularity": r["popularity"], "list": r["list"],
                # in_server: si el cruce local ya lo detecta, True; si no,
                # se conserva lo que ya hubiera (la app lo subió hace un
                # momento, o otro cliente del mismo servidor lo marcó vía
                # FTP) -- una obra subida no debe reaparecer en la lista
                # solo porque este Jellyfin/Plex todavía no la ha reindexado.
                "in_server": r["in_server"] or cached_entry.get("in_server", False),
                "watch": r["watch"],
                # Origen (original_language / origin_country) para el
                # interruptor "Ocultar asiáticas" -- viene en las filas de
                # build_movie_rows; la caché vieja sin estos campos no pasa
                # el filtro (sin dato no se descarta).
                "original_language": r.get("original_language", ""),
                "origin_country": list(r.get("origin_country", []) or []),
                # La clasificación por edad se consulta on-demand al abrir la
                # ficha (_load_movie_certification) y se conserva aquí; un
                # reescaneo no debe borrarla aunque esta pasada no la pida.
                "certification": cached_entry.get("certification", ""),
            }
        cache["_meta"] = {"last_scan_ts": _time.time()}
        save_cache(cache)
        return rows

    def _dismiss_missing_movie(self, r: dict):
        """Botón "🚫" de una fila: quita esta recomendación de la caché en
        disco (para que no reaparezca al reiniciar) y de la tabla, sin
        esperar a un nuevo escaneo."""
        from core.missing_movies_cache import (load_cache, save_cache,
                                               remove_movie_from_cache, cache_key)
        media_type = r.get("media_type", "movie")
        cache = load_cache()
        if remove_movie_from_cache(cache, r["tmdb_id"], media_type):
            save_cache(cache)
        self._movies_results = [row for row in self._movies_results
                                if not (row.get("tmdb_id") == r["tmdb_id"]
                                        and row.get("media_type", "movie") == media_type)]
        if cache_key(media_type, r["tmdb_id"]) == self._movies_selected_tmdb_id:
            self._movies_selected_tmdb_id = None
        self._refresh_movies_genre_filter_options()
        self._render_movies_table(reset_page=False)
        self._update_movies_status_text()
        self._set_status(f"Recomendación quitada: {r['title']}", PENDING_COLOR)

    def _remove_uploaded_movie_from_movies_list(self, media_info) -> None:
        """Tras subir una película (automático o manual), si esa película
        concreta aparecía en la pestaña "Recomendado" como recomendada, se
        marca como en servidor en la caché y se quita de la tabla -- mismo
        criterio que _remove_uploaded_episode_from_missing_list para
        capítulos: la propia app acaba de subirla, no hace falta volver a
        preguntarle a Jellyfin/Plex y esperar a que reindexen la
        biblioteca. Además comparte el flag "in_server" por FTP (ver
        _push_missing_movies_to_ftp) para que el resto de clientes del
        mismo servidor también la quiten de sus listas. Debe llamarse
        desde el hilo de la GUI (los sitios en un hilo de subida ya lo
        agendan con self.after)."""
        if media_info is None or media_info.media_type != "movie":
            return
        tmdb_id = media_info.tmdb_id
        if not tmdb_id:
            return

        from core.missing_movies_cache import (load_cache, save_cache,
                                               mark_movie_in_server)
        cache = load_cache()
        changed = mark_movie_in_server(cache, tmdb_id)
        if changed:
            save_cache(cache)
            self._push_missing_movies_to_ftp()

        # Marcar la fila en memoria como en servidor -- con el filtro
        # "Ocultar ya en el servidor" (activo por defecto) desaparece de la
        # tabla; si el usuario lo apaga, la ve como "✓ En servidor" con el
        # botón de descarga deshabilitado, no borrada de la lista.
        for row in self._movies_results:
            if row.get("media_type", "movie") == "movie" and row.get("tmdb_id") == tmdb_id:
                row["in_server"] = True
        if getattr(self, "_movies_visible", False):
            self._render_movies_table(reset_page=False)
            self._update_movies_status_text()
