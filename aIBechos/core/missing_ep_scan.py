"""
Escaneo de "Episodios que faltan": cruza las series de Jellyfin/Plex con TMDB,
corrige con lo que hay de verdad en el FTP y guarda/comparte la caché.

Sin interfaz -- es una clase mixin que heredan QtAppCore y el servicio de
escaneo de la pestaña (gui_qt/missing_episodes/scan_service.py); quien la
hereda debe aportar:

    config_data                       Config
    tmdb                              TMDBClient
    _ftp_dir_cache                    dict raíz FTP -> carpetas (caché de sesión)
    _new_ftp_client()                 cliente FTP/SFTP nuevo, sin conectar
    _push_missing_episodes_to_ftp()   compartir la caché tras guardarla
    _refresh_server_audio(source, server_id, tmdb_id)
    _scan_notify(texto, color)        aviso de estado, llamable desde un hilo

_ai_verdict_from_cache_entry y _known_series_names_from_cache ya los da la
propia mixin (delegan en core/).
"""

from __future__ import annotations

import time as _time

from core import missing_ep_rows as _mer
from core.api_client import detect_episode
from core.applog import get_logger
from core.ftp_client import _ftp_safe, files_by_top_level_folder
from core.series_match import series_similarity, sibling_blocks_folder

_log = get_logger("aIBechos.gui", "app.log")

_WARNING_COLOR = "#f39c12"

# Umbral de parecido nombre-de-carpeta/serie para dar un episodio por
# presente en el FTP (ver _match_ftp_present) -- más laxo que el de subida
# porque el riesgo no es el mismo: aquí un falso positivo solo hace que un
# hueco no se reporte (se cree que ya tienes un episodio), nunca mueve ni
# sube ningún archivo a la carpeta equivocada. Medido contra los dos casos
# reales que motivaron cada lado del ajuste: "Prodigiosa: Las aventuras de
# Ladybug" vs "Miraculous las aventuras de Ladybug" (la MISMA serie en dos
# carpetas) da 0.80 y debe unirse; "Arcadia" vs "Los 3 de Adabo: Cuentos de
# Arcadia" (series DISTINTAS que llegaron a fusionarse) da 0.56 y debe
# seguir separado.
_FTP_PRESENT_MIN_RATIO = 0.75


class MissingEpScanMixin:

    @staticmethod
    def _merged_dub_cut(cutoff_a, cutoff_b, season, cutoff_c=None, cutoff_d=None):
        return _mer.merged_dub_cut(cutoff_a, cutoff_b, season, cutoff_c, cutoff_d)

    def _compute_dub_updates(self, pending: list, known_cache: dict, cancel_event=None,
                             progress_cb=None) -> dict:
        """Comprueba el doblaje castellano de los episodios *pending*
        ((tmdb_id, nombre, temporada, ep), ver core.missing_ep_rows.
        dub_pending_checks) -- SIN TMDB (falsos positivos de texto
        traducido). Fuentes, una vez por serie y con frescura propia cada
        una (core/eldoblaje.cutoff_is_fresh): eldoblaje.com + wiki,
        Streaming Availability, Crunchyroll (audio es-ES, anime) y RTVE Play.
        Los cortes mandan para sus temporadas: ep <= corte -> doblado; una
        temporada que ninguno cubre se queda sin dato. Devuelve {tmdb_id_str:
        entrada} para core.missing_ep_rows.merge_dub_updates. *known_cache*
        es solo lectura. Llamar desde un hilo de trabajo."""
        from core.eldoblaje import cutoff_is_fresh
        now = _time.time()
        updates = {}
        total = len(pending)
        for i, (tmdb_id, name, season, ep) in enumerate(pending):
            if cancel_event is not None and cancel_event.is_set():
                break
            if progress_cb:
                progress_cb(i + 1, total, name)
            key = str(tmdb_id)
            entry = updates.get(key)
            if entry is None:
                old_entry = known_cache.get(key, {})
                entry = dict(old_entry)
                entry["episodes"] = dict(entry.get("episodes", {}))
                entry.setdefault("spanish_available", None)
                entry["checked_at"] = now   # ver core.spanish_dub_cache.is_stale
                updates[key] = entry
                eld = entry.get("eldoblaje") or {}
                want_eld = not cutoff_is_fresh(eld, now)
                st = entry.get("streaming") or {}
                want_st = not cutoff_is_fresh(st, now)
                cr = entry.get("crunchyroll") or {}
                want_cr = not cutoff_is_fresh(cr, now)
                rt = entry.get("rtve") or {}
                want_rt = not cutoff_is_fresh(rt, now)
                if want_eld or want_st or want_cr or want_rt:
                    fresh = self._fetch_dub_cutoff(
                        tmdb_id, name, pending,
                        want_eldoblaje=want_eld, want_streaming=want_st,
                        want_crunchyroll=want_cr, want_rtve=want_rt)
                    if want_eld and "eldoblaje" in fresh:
                        entry["eldoblaje"] = fresh["eldoblaje"]
                    if want_st and "streaming" in fresh:
                        entry["streaming"] = fresh["streaming"]
                    if want_cr and "crunchyroll" in fresh:
                        entry["crunchyroll"] = fresh["crunchyroll"]
                    if want_rt and "rtve" in fresh:
                        entry["rtve"] = fresh["rtve"]
            ep_key = f"{season}x{ep:02d}"
            cut = self._merged_dub_cut(
                (entry.get("eldoblaje") or {}).get("cutoff") or {},
                (entry.get("streaming") or {}).get("cutoff") or {},
                season,
                (entry.get("crunchyroll") or {}).get("cutoff") or {},
                (entry.get("rtve") or {}).get("cutoff") or {})
            if cut is not None:
                entry["episodes"][ep_key] = ep <= cut
        return updates

    @staticmethod
    def _ai_verdict_from_cache_entry(entry: dict):
        return _mer.ai_verdict_from_cache_entry(entry)

    @staticmethod
    def _known_series_names_from_cache() -> set:
        from core.amule_download import known_series_names_from_cache
        return known_series_names_from_cache()

    def _scan_missing_episodes(self, progress_cb=None, cancel_event=None, force_full=False,
                               on_result_cb=None) -> list:
        """Recorre las series de Plex/Jellyfin que tengan activados, y para
        cada una compara la lista completa de episodios de TMDB con lo que
        de verdad hay -- devuelve una lista de dicts (uno por serie con
        algún hueco): {"tmdb_id", "name", "source", "missing", "summary",
        "ignored"}, pensada para alimentar la tabla de _MissingEpisodesTab.

        Incremental por caché (core/missing_episodes_cache.py): el primer
        escaneo de una serie es caro (una llamada a TMDB por temporada), así
        que en los siguientes solo se repite ese trabajo si TMDB indica que
        ha salido un episodio nuevo desde la última vez (comparando
        last_episode_to_air) -- o si la última vez le faltaban episodios,
        por si mientras tanto se rellenó el hueco a mano; sin ninguna de las
        dos cosas, la serie se salta entera. Las series que ya no existen
        en el servidor se quitan de la caché (por si se borraron).
        force_full=True ignora la caché y repite el trabajo completo para
        todas -- pensado para el botón "Reescaneo completo".
        on_result_cb(row), si se pasa, se llama cada vez que se añade una
        fila a resultados (según se van encontrando, no al final) -- lo
        usa "Reescaneo completo" para que la tabla se vaya rellenando en
        vivo en vez de esperar a tener todo el resultado.

        Para reescanear UNA sola serie (botón por fila) esta función NO se
        usa -- ver _rescan_single_series_worker: incluso limitando el
        bucle principal a una sola serie, esta función sigue pidiendo
        ANTES la lista completa de shows y las estadísticas de uso de todo
        el catálogo (get_jellyfin_usage_stats/get_plex_usage_stats), que
        con una biblioteca real tardaron 66s/26s por sí solas -- inútil
        para el hueco de una sola serie."""
        from core.media_server_refresh import (get_jellyfin_series, get_jellyfin_episodes,
                                                get_plex_series, get_plex_episodes,
                                                get_jellyfin_usage_stats, get_plex_usage_stats,
                                                parse_media_date)
        from core.missing_episodes import (find_missing_episodes, format_missing_summary,
                                           apply_season_split_filter, find_unknown_seasons,
                                           looks_like_absolute_numbering, remap_absolute_episodes,
                                           normalize_tmdb_season_numbering)
        from core.missing_episodes_cache import load_cache, save_cache
        from core.cleanup_candidates import merge_usage_entries

        cache = dict(load_cache())

        # Si la misma serie está en Jellyfin Y en Plex (bibliotecas
        # espejadas, algo común), aparecía en las dos listas y se procesaba
        # dos veces -- una fila duplicada (o triplicada, si además pasaba
        # algo raro con la caché) por serie. Cada tmdb_id se queda con la
        # primera fuente en la que aparece; el orden Jellyfin->Plex es
        # arbitrario pero determinista.
        shows = []
        seen_tmdb_ids = set()

        def _add_shows(source, shows_list):
            for s in shows_list or []:
                tmdb_id = s.get("tmdb_id")
                try:
                    norm = int(tmdb_id) if tmdb_id else None
                except (TypeError, ValueError):
                    norm = tmdb_id
                if norm and norm in seen_tmdb_ids:
                    continue
                if norm:
                    seen_tmdb_ids.add(norm)
                shows.append((source, s))

        if self.config_data.get("jellyfin_enabled"):
            _add_shows("jellyfin", get_jellyfin_series(
                self.config_data.get("jellyfin_host", ""), self.config_data.get("jellyfin_api_key", "")))
        if self.config_data.get("plex_enabled"):
            _add_shows("plex", get_plex_series(
                self.config_data.get("plex_host", ""), self.config_data.get("plex_token", "")))

        # Datos de visionado (veces reproducida, última vez) para la
        # puntuación de tendencia -- una sola llamada por servidor (igual
        # que "Liberar espacio"), correlacionado por tmdb_id: a diferencia
        # de Liberar espacio (que solo tiene nombres de carpeta FTP y
        # necesita reconciliar por nombre), esta lista de series ya trae
        # el tmdb_id emparejado, así que no hace falta esa indirección.
        usage_by_tmdb_id = {}

        def _merge_usage_by_tmdb(entry):
            tid = entry.get("tmdb_id")
            if not tid:
                return
            existing = usage_by_tmdb_id.get(tid)
            usage_by_tmdb_id[tid] = merge_usage_entries(existing, entry) if existing else entry

        if self.config_data.get("jellyfin_enabled"):
            for entry in (get_jellyfin_usage_stats(
                    self.config_data.get("jellyfin_host", ""), self.config_data.get("jellyfin_api_key", ""),
                    username=self.config_data.get("jellyfin_username", "")) or {}).values():
                _merge_usage_by_tmdb(entry)
        if self.config_data.get("plex_enabled"):
            for entry in (get_plex_usage_stats(
                    self.config_data.get("plex_host", ""), self.config_data.get("plex_token", "")) or {}).values():
                _merge_usage_by_tmdb(entry)

        # Quitar de la caché las series que ya no están en el servidor
        # (se borraron, o se desactivó esa fuente) -- si no, un hueco viejo
        # de una serie eliminada seguiría apareciendo para siempre.
        current_keys = {str(s.get("tmdb_id")) for _, s in shows if s.get("tmdb_id")}
        for stale_key in [k for k in cache if k not in current_keys and k != "_meta"]:
            del cache[stale_key]

        def _row(tmdb_id, name, source, missing, ignored, expected=None, unknown_seasons=None,
                 present_season_counts=None, server_id=None, folder_name=None, ai_verdict=None):
            # missing aquí es SOLO para mostrar -- la caché en disco (más
            # abajo, cache[key] = {...}) guarda el hueco SIN filtrar, ver
            # apply_season_split_filter.
            missing, split_seasons = apply_season_split_filter(missing, expected or {})
            # Para la puntuación de tendencia (ver core/trending.py) --
            # parse_media_date() ya es idempotente tanto si la entrada viene
            # de una sola fuente (fecha cruda, string ISO8601 o epoch de
            # Plex) como fusionada de las dos (ya convertida a epoch por
            # merge_usage_entries), así que se puede llamar siempre igual.
            usage = usage_by_tmdb_id.get(tmdb_id) or {}
            return {
                "tmdb_id": tmdb_id, "name": name, "source": source, "server_id": server_id,
                "missing": missing, "summary": format_missing_summary(name, missing),
                "ignored": ignored, "episode_titles": {}, "split_seasons": split_seasons,
                "unknown_seasons": unknown_seasons or set(),
                "ignored_seasons": set(), "ignored_episodes": {},
                "play_count": usage.get("play_count", 0),
                "last_played_ts": parse_media_date(usage.get("last_played")),
                # Lista real de episodios que debería tener cada temporada
                # según TMDB -- la usa _cross_check_results_with_ftp para
                # recalcular el hueco tras cruzar con el FTP, sin tener que
                # asumir que los números son consecutivos desde el 1.
                "expected_episodes": {s: sorted(eps) for s, eps in (expected or {}).items()},
                # Recuentos por temporada (no las listas de episodios enteras)
                # -- lo que se manda a Groq para el veredicto por lotes, ver
                # core/missing_episodes_ai.py.
                "tmdb_season_counts": {s: len(eps) for s, eps in (expected or {}).items()},
                "server_season_counts": dict(present_season_counts or {}),
                "ai_verdict": ai_verdict,   # {"veredicto", "motivo"} tras preguntar a la IA (persistido, ver _persist_ai_verdicts)
                "absolute_numbering": False,   # se sobreescribe fuera si se detecta (ver looks_like_absolute_numbering)
                # Nombre REAL de la carpeta en el servidor de medios (ver
                # get_jellyfin_series) -- puede no parecerse al nombre
                # mostrado (traducido). Solo lo trae Jellyfin por ahora
                # (Plex necesitaría una llamada aparte por serie); None si
                # no se pudo determinar. Usado para encontrar la carpeta
                # de verdad al borrar (ver _resolve_missing_ep_series_path)
                # sin depender solo del parecido difuso de nombres.
                "folder_name": folder_name,
            }

        results = []
        total = len(shows)
        for i, (source, show) in enumerate(shows):
            if cancel_event and cancel_event.is_set():
                break
            if progress_cb:
                progress_cb(i + 1, total, show.get("name", ""))
            tmdb_id = show.get("tmdb_id")
            if not tmdb_id:
                continue   # esa serie no tiene el ID de TMDB emparejado -- no hay con que comparar
            key = str(tmdb_id)
            cached = cache.get(key)
            ignored = bool(cached and cached.get("ignored", False))
            # "Ignorar capítulo"/"Ignorar temporada" (ver
            # _toggle_missing_ep_season_ignore/_episode_ignore) tienen que
            # sobrevivir a un reescaneo igual que "ignored" (serie entera) --
            # se leen de la caché ANTERIOR y se vuelven a escribir tal cual
            # más abajo, el escaneo en sí no decide nada sobre esto.
            cached_ignored_seasons = set((cached or {}).get("ignored_seasons") or [])
            cached_ignored_episodes = {int(s): set(eps) for s, eps in
                                       (cached or {}).get("ignored_episodes", {}).items()}

            try:
                details = self.tmdb.get_tv_details(tmdb_id)
            except Exception:
                # Sin conexión con TMDB ahora mismo -- si había un hueco
                # conocido de un escaneo anterior, se sigue mostrando en
                # vez de perderlo por un fallo puntual de red.
                if cached and cached.get("missing"):
                    cached_expected = {int(k): v for k, v in cached.get("expected", {}).items()}
                    cached_counts = {int(k): v for k, v in cached.get("present_season_counts", {}).items()}
                    row = _row(tmdb_id, show.get("name", ""), source,
                              {int(k): v for k, v in cached["missing"].items()}, ignored,
                              expected=cached_expected,
                              unknown_seasons=set(cached.get("unknown_seasons", [])),
                              present_season_counts=cached_counts,
                              folder_name=show.get("folder_name"),
                              ai_verdict=self._ai_verdict_from_cache_entry(cached))
                    row["episode_titles"] = {int(s): {int(e): t for e, t in eps.items()}
                                             for s, eps in cached.get("episode_titles", {}).items()}
                    row["absolute_numbering"] = cached.get("absolute_numbering", False)
                    row["first_air_date"] = cached.get("first_air_date", "")
                    row["season_air_dates"] = {int(s): d for s, d in
                                               cached.get("season_air_dates", {}).items()}
                    row["episode_air_dates"] = {int(s): {int(e): d for e, d in eps.items()}
                                                for s, eps in cached.get("episode_air_dates", {}).items()}
                    row["ignored_seasons"] = cached_ignored_seasons
                    row["ignored_episodes"] = cached_ignored_episodes
                    results.append(row)
                    if on_result_cb:
                        on_result_cb(row)
                continue

            # Fecha de estreno de la SERIE -- ya viene de fábrica en la
            # misma respuesta de get_tv_details() que ya se pide arriba
            # para last_episode_to_air/seasons, no hace falta ninguna
            # llamada aparte (ver core/api_client.py::get_tv_details).
            first_air_date = details.get("first_air_date", "") or ""

            last_episode_id = (details.get("last_episode_to_air") or {}).get("id")
            had_gaps_before = bool(cached and cached.get("missing"))
            needs_full_recheck = (force_full or not cached
                                  or cached.get("last_episode_id") != last_episode_id)

            if not needs_full_recheck and not had_gaps_before:
                continue   # sin episodios nuevos en TMDB y ya estaba completa -- nada que comprobar

            # Presencia real: se re-consulta si hay novedades en TMDB o si
            # la última vez le faltaban episodios (por si se rellenó a mano).
            if source == "jellyfin":
                present = get_jellyfin_episodes(self.config_data.get("jellyfin_host", ""),
                                                  self.config_data.get("jellyfin_api_key", ""), show["id"])
            else:
                present = get_plex_episodes(self.config_data.get("plex_host", ""),
                                              self.config_data.get("plex_token", ""), show["rating_key"])
            # Audio real del servidor para los presentes (ver
            # core/server_audio.py): con el interruptor de doblaje guarda
            # True/False por episodio en spanish_dub_cache.
            self._refresh_server_audio(source, show["id"] if source == "jellyfin" else show["rating_key"],
                                       tmdb_id)
            # None = falló la consulta (sin red, servidor caído...) -- eso sí
            # se salta, no hay dato fiable. Un set() VACÍO es una respuesta
            # válida ("Jellyfin/Plex no tiene indexado nada de esta serie")
            # y tiene que seguir adelante -- si no, un fallo de indexado
            # como el de Desencanto (Jellyfin decía "0 episodios" con la
            # carpeta llena en el FTP) nunca llegaba ni a compararse con
            # TMDB, y mucho menos a cruzarse con el FTP después.
            if present is None:
                continue

            if needs_full_recheck:
                expected = {}
                episode_titles = {}
                # season.get("air_date"): fecha de estreno de la TEMPORADA,
                # ya viene dentro de details["seasons"] (misma respuesta de
                # get_tv_details() de más arriba) -- no hace falta pedirla
                # aparte. episode["air_date"] sí necesitó tocar
                # get_season_episodes() (ver core/api_client.py) porque esa
                # función descartaba el campo antes de devolver la lista.
                season_air_dates = {}
                episode_air_dates = {}
                for season in details.get("seasons", []):
                    n = season.get("season_number", 0)
                    if n <= 0:   # temporada 0 = especiales, no cuenta como hueco
                        continue
                    try:
                        eps = self.tmdb.get_season_episodes(tmdb_id, n)
                    except Exception:
                        continue
                    nums = [e["episode_number"] for e in eps if e.get("episode_number")]
                    if nums:
                        expected[n] = nums
                        episode_titles[n] = {e["episode_number"]: e.get("name", "") for e in eps
                                             if e.get("episode_number")}
                        season_air_dates[n] = season.get("air_date", "") or ""
                        episode_air_dates[n] = {e["episode_number"]: e.get("air_date", "") for e in eps
                                                if e.get("episode_number")}
            else:
                expected = {int(k): v for k, v in cached["expected"].items()}
                episode_titles = {int(s): {int(e): t for e, t in eps.items()}
                                  for s, eps in cached.get("episode_titles", {}).items()}
                season_air_dates = {int(s): d for s, d in cached.get("season_air_dates", {}).items()}
                episode_air_dates = {int(s): {int(e): d for e, d in eps.items()}
                                     for s, eps in cached.get("episode_air_dates", {}).items()}
                if "episode_number_offsets" not in (cached or {}):
                    # Caché de antes de la normalización: lo guardado puede
                    # estar en numeración TMDB continua (caso HxH) -- se
                    # renumera igual que recién traído (vacío si ya
                    # empezaba en 1). Abajo se vuelve a guardar ya
                    # normalizado con su marcador.
                    expected, episode_titles, episode_air_dates, ep_offsets = \
                        normalize_tmdb_season_numbering(
                            expected, episode_titles, episode_air_dates)
                else:
                    ep_offsets = {int(s): o
                                  for s, o in (cached.get("episode_number_offsets") or {}).items()}

            # Anime de muchos episodios (Naruto Shippuden y similares) suele
            # organizarse con numeración absoluta (episodio 262 de corrido)
            # en vez de reiniciar por temporada -- comparado tal cual contra
            # TMDB (que sí numera por temporada) da un falso "falta todo".
            # Si se detecta el patrón, se convierte antes de comparar.
            # Y al revés: TMDB a veces numera sin reiniciar en cada temporada
            # (real: Hunter x Hunter 2011, T2: 63-136, T3: 137-148) mientras
            # el servidor y los releases reinician en 1 -- comparado tal cual
            # daría cientos de falsos "faltan". Se normaliza a numeración por
            # temporada (vacío para series normales) antes de comparar;
            # títulos y fechas van indexados por esos mismos números y se
            # renumeran igual.
            expected, episode_titles, episode_air_dates, ep_offsets = \
                normalize_tmdb_season_numbering(expected, episode_titles, episode_air_dates)
            if any(ep_offsets.values()):
                _log.info("Numeración TMDB continua en '%s': desplazamientos %s -- "
                          "se compara en numeración por temporada", show.get("name", ""),
                          {s: o for s, o in ep_offsets.items() if o})

            season_episode_counts = {s: len(eps) for s, eps in expected.items()}
            absolute_numbering = looks_like_absolute_numbering(season_episode_counts, present)
            present_for_compare = remap_absolute_episodes(season_episode_counts, present) \
                if absolute_numbering else present

            missing = find_missing_episodes(expected, present_for_compare)
            unknown_seasons = find_unknown_seasons(present_for_compare, expected.keys())
            present_season_counts = {}
            for season, _ep in present_for_compare:
                present_season_counts[season] = present_season_counts.get(season, 0) + 1
            usage = usage_by_tmdb_id.get(tmdb_id) or {}
            cache[key] = {
                "name": show.get("name", ""),
                "source": source,
                "server_id": show.get("id") or show.get("rating_key"),
                "folder_name": show.get("folder_name"),
                "last_episode_id": last_episode_id,
                "expected": {str(k): v for k, v in expected.items()},
                "episode_number_offsets": {str(s): o for s, o in ep_offsets.items()},
                "episode_titles": {str(s): {str(e): t for e, t in eps.items()}
                                   for s, eps in episode_titles.items()},
                "missing": {str(k): v for k, v in missing.items()},
                "unknown_seasons": sorted(unknown_seasons),
                "present_season_counts": {str(k): v for k, v in present_season_counts.items()},
                "absolute_numbering": absolute_numbering,
                "first_air_date": first_air_date,
                "season_air_dates": {str(s): d for s, d in season_air_dates.items()},
                "episode_air_dates": {str(s): {str(e): d for e, d in eps.items()}
                                      for s, eps in episode_air_dates.items()},
                "ignored_seasons": sorted(cached_ignored_seasons),
                "ignored_episodes": {str(s): sorted(eps) for s, eps in cached_ignored_episodes.items()},
                "ignored": ignored,
                "play_count": usage.get("play_count", 0),
                "last_played_ts": parse_media_date(usage.get("last_played")),
                # Se conserva el veredicto de la IA de la entrada anterior
                # (si había) -- series con un hueco permanente (p.ej.
                # Bleach, sin doblaje castellano más allá del 1x109) pasan
                # needs_full_recheck=False pero had_gaps_before=True en
                # CASI todos los escaneos normales, así que sin esto el
                # veredicto se perdía en el primer "Comprobar" después de
                # preguntarle a la IA, no solo en un "Reescaneo completo"
                # -- justo lo contrario de "tiene que ser persistente".
                "ai_verdict": (cached or {}).get("ai_verdict"),
            }
            # Se muestra la fila si faltan episodios O si hay temporadas en
            # el servidor que TMDB no conoce -- esto último puede pasar
            # aunque TMDB no reporte ningún hueco (si sus datos para esta
            # serie están incompletos, "falta" lo mismo no significa nada).
            if missing or unknown_seasons:
                row = _row(tmdb_id, show.get("name", ""), source, missing, ignored,
                          expected=expected, unknown_seasons=unknown_seasons,
                          present_season_counts=present_season_counts,
                          server_id=show.get("id") or show.get("rating_key"),
                          folder_name=show.get("folder_name"),
                          ai_verdict=self._ai_verdict_from_cache_entry(cached) if cached else None)
                row["episode_titles"] = episode_titles
                row["absolute_numbering"] = absolute_numbering
                row["episode_number_offsets"] = {s: o for s, o in ep_offsets.items() if o}
                row["first_air_date"] = first_air_date
                row["season_air_dates"] = season_air_dates
                row["episode_air_dates"] = episode_air_dates
                row["ignored_seasons"] = cached_ignored_seasons
                row["ignored_episodes"] = cached_ignored_episodes
                results.append(row)
                if on_result_cb:
                    on_result_cb(row)

        if results and (cancel_event is None or not cancel_event.is_set()):
            # _cross_check_results_with_ftp muta cada fila IN PLACE
            # (r["missing"] = ...) y solo al final filtra las que se
            # quedaron sin ningún hueco real de su valor de retorno -- por
            # eso se guarda la lista de ANTES del cruce (pre_cross_check,
            # mismos objetos dict, no una copia) para poder propagar la
            # corrección a `cache` también para esas, no solo a las que
            # siguen apareciendo en pantalla.
            pre_cross_check = results
            results = self._cross_check_results_with_ftp(pre_cross_check, cancel_event=cancel_event,
                                                          progress_cb=progress_cb,
                                                          all_show_names={s.get("name", "") for _, s in shows
                                                                          if s.get("name", "")})
            # Sin esto, la corrección del cruce FTP (p.ej. Dragon Ball GT:
            # 3 episodios que sí estaban en el servidor pero Jellyfin no
            # los tenía bien indexados) solo vivía en esta sesión --
            # `cache[key]["missing"]` seguía con el hueco de ANTES del
            # cruce (se escribió más arriba, en el bucle principal, antes
            # de cruzar con el FTP), así que al guardar se persistía el
            # dato viejo y el hueco falso volvía a aparecer tal cual en el
            # siguiente arranque, aunque ya se hubiera visto corregido.
            for r in pre_cross_check:
                key = str(r["tmdb_id"])
                if key in cache:
                    cache[key]["missing"] = {str(k): v for k, v in r["missing"].items()}
                    cache[key]["unknown_seasons"] = sorted(r.get("unknown_seasons") or [])
                    cache[key]["present_season_counts"] = {
                        str(k): v for k, v in (r.get("server_season_counts") or {}).items()}

        import time as _time
        cache["_meta"] = {"last_scan_ts": _time.time(), "scanned_by": self.config_data.get("app_user_name", "")}
        save_cache(cache)
        # Compartir con el resto de clientes del mismo servidor -- ver
        # _push_missing_episodes_to_ftp, para que no tengan que repetir
        # este mismo escaneo (puede tardar minutos).
        self._push_missing_episodes_to_ftp()
        return results

    def _build_ftp_episode_index(self, ftp_conn, cancel_event=None, progress_cb=None) -> dict:
        """Listado COMPLETO (de una sola vez, no serie por serie) de todas
        las categorías de TV configuradas en el FTP -- se llama solo si el
        escaneo por Jellyfin/Plex ya encontró algún hueco, para no pagar
        este coste cuando no hace falta. Devuelve {root: {nombre_carpeta:
        {(temporada, episodio), ...}}}, sacando temporada/episodio del
        propio nombre de archivo (detect_episode), no de cómo se llamen
        las subcarpetas de temporada.
        cancel_event: se comprueba entre carpeta y carpeta durante el
        listado de respaldo (ver más abajo) -- es la fase más lenta de
        todo el escaneo (con un servidor cuyo LIST -R falla, decenas de
        listados individuales seguidos), y antes de este parámetro
        "Cancelar" no tenía forma de interrumpirla: el hilo tenía que
        terminar las ~360 carpetas sí o sí antes de que el botón
        surtiera efecto.
        progress_cb(current, total, nombre): mismo contrato que en
        _scan_missing_episodes, reutilizado aquí para ESTA fase (el
        respaldo carpeta-a-carpeta) -- sin esto, la barra de progreso se
        quedaba clavada al 100% (el recuento de series ya había terminado)
        mientras esta fase, la más lenta con diferencia con una raíz
        grande, seguía trabajando en silencio varios minutos más -- el
        usuario veía el escaneo "parado" y no tenía forma de saber que en
        realidad seguía cruzando datos con el FTP, ni que la lista que
        estaba mirando todavía podía cambiar cuando terminara de verdad."""
        cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}).get("tv", [])
        index = {}
        for cat in cats:
            if cancel_event and cancel_event.is_set():
                break
            root = cat.get("root", "")
            if not root or root in index:
                continue
            # Se pide SIEMPRE en fresco (sin usar self._ftp_dir_cache) --
            # visto de verdad: un corte de red puntual al listar puede
            # devolver 0 carpetas para una raíz que normalmente tiene
            # cientos, y si eso se cachea, el cruce entero se queda
            # confiando en ese vacío para siempre en esa sesión, "confir-
            # mando" que ninguna serie está en el FTP. Aquí la fiabilidad
            # importa más que ahorrarse una consulta (esta función solo se
            # llama una vez por escaneo).
            # Dos listados, no uno -- mismo motivo que
            # _find_category_with_existing_folder (un único NLST a esta
            # raíz a veces vuelve incompleto sin dar ningún error, visto de
            # verdad con "(Des)encanto"): un escaneo completo real dejó
            # fuera "Los Vengadores: Los Súper Héroes más poderosos de la
            # Tierra" de show_folders con un solo intento, así que su
            # carpeta nunca llegaba a procesarse aunque LIST -R sí trajera
            # sus archivos -- _match_ftp_present no encontraba ninguna
            # carpeta con esa confianza y el cruce se saltaba en silencio
            # para esa serie entera, dejando el hueco falso que Jellyfin
            # había reportado. "Reescanear esta serie" no tenía este fallo
            # porque ya usa la unión de dos intentos para su propia serie.
            first_folders = set(ftp_conn.list_dirs(root))
            second_folders = set(ftp_conn.list_dirs(root))
            if first_folders != second_folders:
                _log.warning("Cruce FTP: listado de '%s' inconsistente entre dos intentos seguidos "
                            "(%d vs %d carpetas) -- usando la unión de ambos",
                            root, len(first_folders), len(second_folders))
            show_folders = list(first_folders | second_folders)
            if show_folders:
                self._ftp_dir_cache[root] = show_folders   # sí se aprovecha para otras partes de la app
                _log.info("Cruce FTP: raíz '%s' -> %d carpeta(s) de serie: %s",
                          root, len(show_folders), show_folders)
            else:
                _log.warning("Cruce FTP: raíz '%s' devolvió 0 carpetas -- sospechoso "
                             "(¿corte de red al listar?), no se usa para este cruce", root)
            index[root] = {}

            # "LIST -R" trae TODOS los archivos de la categoría en una
            # sola petición (si el servidor lo soporta, ver
            # FTPClient.list_tree_recursive) -- evita un
            # list_files_recursive por cada serie, que es lo que hacía
            # lento este cruce en categorías con muchas series.
            # Igual que show_folders arriba: dos peticiones, no una, y
            # unión de los archivos por carpeta -- un escaneo completo real
            # dio "falta Bleach 1x08" con el archivo presente de verdad en
            # el servidor: la carpeta de Bleach SÍ se resolvía por LIST -R
            # (así que nunca caía en el respaldo carpeta-a-carpeta de más
            # abajo, que sí es fiable), pero esa respuesta concreta venía
            # incompleta DENTRO de esa carpeta -- el aviso de "respuesta
            # cortada" de más abajo solo detecta carpetas enteras que
            # faltan, no archivos sueltos que faltan dentro de una carpeta
            # que sí se resolvió. Un segundo LIST -R independiente rara vez
            # se corta exactamente en el mismo punto, así que la unión
            # recupera el archivo que faltaba en cualquiera de los dos.
            files_by_folder = None
            tree1 = ftp_conn.list_tree_recursive(root)
            tree2 = ftp_conn.list_tree_recursive(root)
            if tree1 is not None or tree2 is not None:
                by_folder_1 = files_by_top_level_folder(tree1, root) if tree1 is not None else {}
                by_folder_2 = files_by_top_level_folder(tree2, root) if tree2 is not None else {}
                files_by_folder = {}
                for folder in set(by_folder_1) | set(by_folder_2):
                    set_1 = set(by_folder_1.get(folder, []))
                    set_2 = set(by_folder_2.get(folder, []))
                    files_by_folder[folder] = sorted(set_1 | set_2)
                    # Comparado como conjuntos, no como listas -- el orden en
                    # que el servidor devuelve los archivos puede variar entre
                    # dos peticiones sin que eso sea una respuesta cortada de
                    # verdad, y comparar listas habría avisado de un "hueco"
                    # falso en cada carpeta solo por el orden.
                    if folder in by_folder_1 and folder in by_folder_2 and set_1 != set_2:
                        _log.warning("Cruce FTP: '%s/%s' -- LIST -R inconsistente entre dos intentos "
                                    "seguidos (%d vs %d archivo(s)) -- usando la unión de ambos",
                                    root, folder, len(set_1), len(set_2))
                _log.info("Cruce FTP: LIST -R soportado en '%s' -- %d carpeta(s) resueltas de una vez",
                          root, len(files_by_folder))
                if show_folders and len(files_by_folder) < len(show_folders) * 0.5:
                    # Visto de verdad con la categoría "series/" (368+ carpetas):
                    # el servidor a veces corta la respuesta de "LIST -R" muy
                    # pronto (llegó a resolver 1 sola carpeta de 368) sin dar
                    # ningún error -- una respuesta tan incompleta no es de
                    # fiar, aunque no haya forma de saber cuál es la carpeta
                    # que de verdad se cortó hasta comprobarlas una a una abajo.
                    _log.warning("Cruce FTP: '%s' -- solo %d/%d carpeta(s) en la respuesta de LIST -R, "
                                 "sospechoso de respuesta cortada", root, len(files_by_folder), len(show_folders))

            def _process_folder(folder, files):
                """Analiza y registra UNA carpeta -- separado para poder
                llamarse tan pronto como se tenga el listado de cada una
                (ver más abajo), en vez de esperar a tener las ~360 antes
                de escribir la primera línea en el log. Antes de
                paralelizar el respaldo (ver más abajo) esto se hacía en
                un único bucle, así que el log siempre iba avisando serie
                a serie según se procesaban -- separarlo en dos pasadas
                (listar todo, LUEGO analizar/loguear todo) dejó al log
                completamente mudo durante todo el respaldo, dando la
                sensación de que el escaneo se había colgado cuando en
                realidad seguía vivo, solo que en silencio."""
                present = set()
                unparsed = []
                for fname in files:
                    det = detect_episode(fname)
                    if det.get("season") is not None and det.get("episode") is not None:
                        present.add((det["season"], det["episode"]))
                        # Episodio doble empaquetado en el mismo archivo
                        # (p.ej. "7x21-7x22") -- ver detect_episode.
                        for extra_ep in det.get("extra_episodes", []):
                            present.add((det["season"], extra_ep))
                    else:
                        unparsed.append(fname)
                index[root][folder] = present
                _log.info("Cruce FTP: '%s/%s' -> %d archivo(s), %d episodio(s) reconocidos%s",
                          root, folder, len(files), len(present),
                          f", {len(unparsed)} sin temporada/episodio reconocible: {unparsed[:5]}"
                          if unparsed else "")

            for folder, files in (files_by_folder or {}).items():
                if folder in show_folders:
                    _process_folder(folder, files)

            # Carpetas que "LIST -R" no resolvió (ausentes de files_by_folder,
            # o directamente sin soporte -- files_by_folder es None): NO se
            # interpretan como "carpeta vacía" (daría huecos falsos que el
            # propio cruce FTP debería evitar, ver docstring de esta función),
            # se comprueban una a una, más lento pero fiable. Con una sola
            # conexión y un servidor cuyo LIST -R se corta sistemáticamente
            # (visto de verdad con "series/", 360 carpetas: SIEMPRE solo 1/360
            # resuelta, ni una vez de casualidad) esto podía tardar decenas de
            # minutos -- "Reescaneo completo" daba la sensación de no terminar
            # nunca. Mismo número de conexiones en paralelo que ya usa la
            # subida (ftp_parallel, 1-5): cada una con su propia conexión FTP,
            # nunca comparten ftp_conn (igual que las subidas paralelas).
            fallback_folders = [f for f in show_folders
                                if files_by_folder is None or f not in files_by_folder]
            if fallback_folders:
                parallel = max(1, min(5, int(self.config_data.get("ftp_parallel", 1))))
                if parallel > 1 and len(fallback_folders) > 1:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _list_folder_files(folder):
                        worker_ftp = self._new_ftp_client()
                        ok, _ = worker_ftp.connect(
                            self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
                            self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
                            self.config_data.get("ftp_use_tls", False))
                        if not ok:
                            return folder, []
                        try:
                            # Dos listados, no uno, misma unión que el resto
                            # de esta función -- una raíz real (368+
                            # carpetas) resultó SIEMPRE sin soporte de
                            # verdad para LIST -R (atascada en 1/368 en 33
                            # escaneos seguidos, nunca de casualidad, ver el
                            # aviso de "respuesta cortada" más arriba), así
                            # que TODAS sus carpetas caen aquí en cada
                            # escaneo -- este respaldo no es un caso raro
                            # para esa categoría, es el camino normal, y un
                            # listado cortado aquí (mismo NLST/LIST que ya
                            # falla en otros sitios de este mismo servidor)
                            # dejaba huecos falsos (Bleach 1x08, presente de
                            # verdad en el servidor).
                            path = f"{root.rstrip('/')}/{folder}"
                            files = list(set(worker_ftp.list_files_recursive(path, max_depth=2))
                                        | set(worker_ftp.list_files_recursive(path, max_depth=2)))
                            return folder, files
                        finally:
                            worker_ftp.disconnect()

                    _log.info("Cruce FTP: '%s' -- %d carpeta(s) sin resolver por LIST -R, "
                              "listando en paralelo (%d conexiones)",
                              root, len(fallback_folders), parallel)
                    with ThreadPoolExecutor(max_workers=parallel) as executor:
                        # as_completed (no executor.map): loguea cada carpeta
                        # en cuanto termina SU listado, no en el orden en que
                        # se enviaron -- con map(), una sola carpeta lenta
                        # bloquea el aviso de las demás que ya habían
                        # terminado, dejando el mismo silencio que se quiere
                        # evitar aquí.
                        futures = [executor.submit(_list_folder_files, f) for f in fallback_folders]
                        done = 0
                        for future in as_completed(futures):
                            folder, files = future.result()
                            _process_folder(folder, files)
                            done += 1
                            if progress_cb:
                                progress_cb(done, len(fallback_folders), f"cruzando con el FTP: {folder}")
                            if cancel_event and cancel_event.is_set():
                                # No se puede matar a media petición un
                                # listado FTP ya en marcha en otro hilo, pero
                                # sí evitar que los que aún no habían
                                # arrancado se pongan a la cola -- deja de
                                # esperar más resultados en cuanto se pide
                                # cancelar, en vez de esperar a que
                                # terminen los ~360.
                                for f in futures:
                                    f.cancel()
                                break
                else:
                    for i, folder in enumerate(fallback_folders, 1):
                        if cancel_event and cancel_event.is_set():
                            break
                        path = f"{root.rstrip('/')}/{folder}"
                        files = list(set(ftp_conn.list_files_recursive(path, max_depth=2))
                                    | set(ftp_conn.list_files_recursive(path, max_depth=2)))
                        _process_folder(folder, files)
                        if progress_cb:
                            progress_cb(i, len(fallback_folders), f"cruzando con el FTP: {folder}")
        return index

    def _cross_check_results_with_ftp(self, results: list, cancel_event=None, progress_cb=None,
                                        all_show_names=None) -> list:
        """Para las series que YA salieron con algún hueco (según Jellyfin/
        Plex), comprueba también el listado real del FTP -- por si el
        propio servidor de medios falló al indexar algo que sí está físi-
        camente en el servidor (visto con Desencanto: Jellyfin decía "0
        episodios" con la carpeta llena). Si de verdad no hay conexión FTP
        configurada o falla, se devuelven los resultados tal cual, sin
        romper el escaneo por esto. cancel_event/progress_cb: ver
        _build_ftp_episode_index, que es donde de verdad se comprueba (esta
        fase es la más lenta de todo el escaneo, así que es la que más
        falta hacía tanto que "Cancelar" pudiera interrumpirla como que la
        barra de progreso no se quedara clavada al 100% mientras seguía
        trabajando en silencio)."""
        from core.missing_episodes import find_missing_episodes, find_unknown_seasons, format_missing_summary

        if not self.config_data.get("ftp_host", ""):
            _log.info("Cruce FTP: omitido, sin servidor FTP configurado")
            return results
        # Conexión propia, NUNCA self.ftp -- este cruce puede tardar bastante
        # (listado recursivo de varias raíces) y self.ftp lo usan a la vez
        # AutoWatcher y otras partes de la GUI; compartirla aquí bloquearía
        # esas subidas durante todo el escaneo, o peor, cruzaría respuestas
        # entre hilos (ver el candado _ftp_cmd_lock en las demás llamadas).
        own_ftp = self._new_ftp_client()
        try:
            own_ftp.connect(
                self.config_data.get("ftp_host", ""),
                int(self.config_data.get("ftp_port", 21)),
                self.config_data.get("ftp_user", ""),
                self.config_data.get("ftp_password", ""),
                self.config_data.get("ftp_use_tls", False))
            if not own_ftp.is_connected():
                _log.warning("Cruce FTP: omitido, no se pudo conectar al servidor")
                return results
            ftp_index = self._build_ftp_episode_index(own_ftp, cancel_event=cancel_event, progress_cb=progress_cb)
        except Exception as e:
            _log.warning("Cruce FTP: fallo inesperado, se deja el resultado tal cual: %s", e)
            return results   # sin FTP no se puede cruzar -- se deja tal cual
        finally:
            own_ftp.disconnect()

        if ftp_index and not any(ftp_index.values()):
            # Todas las raíces devolvieron 0 carpetas -- casi seguro un
            # fallo de listado puntual, no que el FTP esté genuinamente
            # vacío. Seguir adelante reportaría "no encontrado en el FTP"
            # para TODAS las series por igual, dando una falsa sensación
            # de confianza. Mejor dejar el resultado de Jellyfin/Plex tal
            # cual que "confirmar" con datos que no son de fiar.
            _log.warning("Cruce FTP: todas las raíces devolvieron 0 carpetas, se descarta este cruce "
                         "por sospechoso y se deja el resultado tal cual")
            return results

        for r in results:
            new_expected = r.get("expected_episodes")
            if not new_expected:
                _log.info("Cruce FTP: '%s' sin lista completa de episodios en caché, no se recalcula", r["name"])
                continue   # sin la lista completa de episodios de TMDB no se puede recalcular el hueco
            ftp_present = self._match_ftp_present(ftp_index, r["name"], r.get("folder_name"),
                                                  known_year=self._missing_ep_known_year(r),
                                                  all_show_names=all_show_names)
            if ftp_present is None:
                _log.info("Cruce FTP: '%s' no se encontró en ninguna carpeta del FTP con confianza suficiente",
                          r["name"])
                continue   # esta serie no se encontró en ninguna carpeta del FTP
            _log.info("Cruce FTP: '%s' -> %d episodio(s) encontrados en el FTP (hueco antes: %s)",
                      r["name"], len(ftp_present), r["missing"])

            # Unión: lo que ya decía Jellyfin/Plex (reconstruido a partir de
            # expected - missing) + lo que hay de verdad en el FTP.
            already_present = {(season, ep) for season, eps in new_expected.items()
                               for ep in eps if ep not in set(r["missing"].get(season, []))}
            combined_present = already_present | ftp_present

            new_missing = find_missing_episodes(new_expected, combined_present)
            r["missing"] = new_missing
            r["summary"] = format_missing_summary(r["name"], new_missing)
            r["unknown_seasons"] = find_unknown_seasons(combined_present, new_expected.keys())
            present_counts = {}
            for season, _ep in combined_present:
                present_counts[season] = present_counts.get(season, 0) + 1
            r["server_season_counts"] = present_counts
        return [r for r in results if r["missing"] or r["unknown_seasons"]]

    def _cross_check_single_result_with_ftp(self, result: dict) -> dict:
        """Igual que _cross_check_results_with_ftp, pero para UNA sola
        serie -- usado por el reescaneo individual (ver
        _rescan_single_missing_ep_series). _build_ftp_episode_index lista
        TODAS las carpetas de TODAS las categorías configuradas (necesario
        para un reescaneo completo, pero es la fase más lenta de todo el
        escaneo, y pagarla entera por una sola serie la hacía tardar igual
        que un reescaneo completo). Aquí se localiza directamente la
        carpeta de ESTA serie (_find_category_with_existing_folder, mismo
        método que ya usa el botón de borrar) y solo se lista esa."""
        import types
        from core.missing_episodes import find_missing_episodes, find_unknown_seasons, format_missing_summary

        if not self.config_data.get("ftp_host", ""):
            return result
        new_expected = result.get("expected_episodes")
        if not new_expected:
            return result

        own_ftp = self._new_ftp_client()
        try:
            own_ftp.connect(
                self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
                self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
                self.config_data.get("ftp_use_tls", False))
            if not own_ftp.is_connected():
                _log.warning("Cruce FTP (individual): omitido, no se pudo conectar al servidor")
                return result
            info = types.SimpleNamespace(title=result["name"], media_type="tv",
                                         folder_name=result.get("folder_name"))
            # Localizar TODAS las carpetas de la serie, no solo una: una
            # misma serie puede estar repartida en varias carpetas del
            # servidor (caso real: "Prodigiosa Las aventuras de Ladybug"
            # con solo la T6 y "Miraculous las aventuras de Ladybug" con
            # las T1-T5 -- el título en castellano y el original). El cruce
            # completo ya las une (_match_ftp_present), pero este cruce
            # individual usaba _find_category_with_existing_folder, que
            # devuelve UNA sola carpeta, y los episodios de las demás
            # salían como "te faltan" estando en el servidor -- el
            # autocompletado los re-descargaba una y otra vez (caso real
            # visto en los logs: Ladybug entera re-descargada en bucle).
            # Se reutiliza el mismo emparejamiento de _match_ftp_present
            # sobre los NOMBRES de carpeta (barato) y se listan solo las
            # carpetas que casan.
            cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}).get("tv", [])

            def dir_lookup(root):
                if root not in self._ftp_dir_cache:
                    # Dos listados, no uno, misma unión que el cruce completo.
                    first = set(own_ftp.list_dirs(root))
                    second = set(own_ftp.list_dirs(root))
                    if first != second:
                        _log.warning("Listado de '%s' inconsistente entre dos intentos seguidos "
                                    "(%d vs %d carpetas) -- usando la unión de ambos",
                                    root, len(first), len(second))
                    self._ftp_dir_cache[root] = list(first | second)
                return self._ftp_dir_cache[root]

            shallow_index = {}
            for cat in cats:
                root = cat.get("root", "")
                if not root or root in shallow_index:
                    continue
                shallow_index[root] = {name: set() for name in dir_lookup(root)}

            matched_folders, _best, _ratio = self._match_ftp_folders(
                shallow_index, result["name"], result.get("folder_name"),
                known_year=self._missing_ep_known_year(result),
                all_show_names=self._known_series_names_from_cache())
            if not matched_folders:
                _log.info("Cruce FTP (individual): '%s' no se encontró en ninguna categoría del FTP",
                          result["name"])
                return result
            files = set()
            for root, folder_name in matched_folders:
                # Dos listados por carpeta, no uno, misma unión que el cruce
                # completo (ver _build_ftp_episode_index) -- barato aquí
                # porque son UNA O DOS carpetas, no cientos, así que vale la
                # pena pagarlo siempre en vez de arriesgarse a un listado
                # cortado que deje fuera un episodio suelto.
                path = f"{root.rstrip('/')}/{folder_name}"
                files |= set(own_ftp.list_files_recursive(path, max_depth=2))
                files |= set(own_ftp.list_files_recursive(path, max_depth=2))
            files = list(files)
        except Exception as e:
            _log.warning("Cruce FTP (individual): fallo inesperado para '%s': %s", result["name"], e)
            return result
        finally:
            own_ftp.disconnect()

        ftp_present = set()
        for fname in files:
            det = detect_episode(fname)
            if det.get("season") is not None and det.get("episode") is not None:
                ftp_present.add((det["season"], det["episode"]))
                # Episodio doble empaquetado en el mismo archivo (p.ej.
                # "7x21-7x22") -- ver detect_episode.
                for extra_ep in det.get("extra_episodes", []):
                    ftp_present.add((det["season"], extra_ep))
        _log.info("Cruce FTP (individual): '%s' -> %d episodio(s) encontrados en %d carpeta(s) (%s) (hueco antes: %s)",
                  result["name"], len(ftp_present), len(matched_folders),
                  ", ".join(sorted(folder for _root, folder in matched_folders)), result["missing"])

        already_present = {(season, ep) for season, eps in new_expected.items()
                           for ep in eps if ep not in set(result["missing"].get(season, []))}
        combined_present = already_present | ftp_present
        new_missing = find_missing_episodes(new_expected, combined_present)
        result["missing"] = new_missing
        result["summary"] = format_missing_summary(result["name"], new_missing)
        result["unknown_seasons"] = find_unknown_seasons(combined_present, new_expected.keys())
        present_counts = {}
        for season, _ep in combined_present:
            present_counts[season] = present_counts.get(season, 0) + 1
        result["server_season_counts"] = present_counts
        return result

    @staticmethod
    def _match_ftp_folders(ftp_index: dict, show_name: str, known_folder_name: str = None,
                           known_year: str = None, all_show_names=None):
        """Devuelve (lista de (root, nombre_de_carpeta), mejor_candidato,
        mejor_ratio) de TODAS las carpetas del índice FTP que corresponden
        a *show_name* con la confianza de _match_ftp_present (nombre real
        ya conocido si se pasa known_folder_name -- ver
        get_jellyfin_series::folder_name, para series cuyo nombre mostrado
        está traducido y no se parece en nada al de su carpeta real
        ("Acusado" vs "Accused") --, si no, nombre exacto tras sanear,
        desempate por año si la carpeta real lo lleva -- ver
        core.series_match.best_match_with_year --, o ratio >=
        _FTP_PRESENT_MIN_RATIO). Lista VACÍA si no hay ninguna
        coincidencia de esa confianza. Separado de _match_ftp_present para
        que el cruce individual (_cross_check_single_result_with_ftp)
        pueda localizar TODAS las carpetas de una serie repartida en
        varias del servidor (caso real de Ladybug) listando solo los
        nombres de carpeta (barato) en vez de construir el índice completo
        de episodios."""
        sanitized_desired = _ftp_safe(show_name)
        matched = []          # (root, nombre de carpeta)
        best_candidate, best_ratio = None, 0.0

        for root, folders in ftp_index.items():
            year_candidate = None
            if known_year:
                from core.series_match import best_match_with_year
                year_candidate, _yr_ratio = best_match_with_year(
                    show_name, list(folders.keys()), known_year)

            for folder_name in folders:
                if known_folder_name and folder_name.lower() == known_folder_name.lower():
                    matched.append((root, folder_name))   # el nombre REAL que dio el servidor de medios
                    continue
                if folder_name == sanitized_desired:
                    matched.append((root, folder_name))   # nombre exacto tras sanear
                    continue
                if folder_name == year_candidate:
                    matched.append((root, folder_name))   # desempatado por año (remake vs original)
                    continue
                ratio = series_similarity(show_name, folder_name)
                if ratio >= _FTP_PRESENT_MIN_RATIO:
                    if all_show_names and sibling_blocks_folder(show_name, folder_name, all_show_names):
                        # La carpeta es EXACTAMENTE la de otra serie conocida
                        # (caso real: "Dragon Ball Daima" absorbía "Dragon
                        # Ball", 0.90 en modo laxo por prefijo literal, y
                        # mostraba sus T2-T9 como temporadas en el servidor
                        # de una serie de una sola temporada) -- el parecido
                        # no basta para reclamar carpeta ajena con dueño.
                        _log.info("Cruce FTP: '%s' no reclama la carpeta '%s' "
                                  "(es la carpeta exacta de otra serie conocida)",
                                  show_name, folder_name)
                    else:
                        matched.append((root, folder_name))
                elif ratio > best_ratio:
                    best_candidate, best_ratio = folder_name, ratio

        if not matched and best_candidate:
            _log.info("Cruce FTP: '%s' -- candidato más parecido '%s' con ratio %.2f "
                      "(hace falta >= %.2f para darlo por la misma serie)",
                      show_name, best_candidate, best_ratio, _FTP_PRESENT_MIN_RATIO)
        return matched, best_candidate, best_ratio

    @staticmethod
    def _match_ftp_present(ftp_index: dict, show_name: str, known_folder_name: str = None,
                           known_year: str = None, all_show_names=None):
        """Busca *show_name* entre todas las carpetas de todas las
        categorías del índice FTP (misma confianza que
        _find_category_with_existing_folder: nombre real ya conocido si
        se pasa known_folder_name -- ver get_jellyfin_series::folder_name,
        para series cuyo nombre mostrado está traducido y no se parece en
        nada al de su carpeta real ("Acusado" vs "Accused") --, si no,
        nombre exacto tras sanear, o ratio >= 0.90; known_year, ver
        core.series_match.best_match_with_year, para cuando la carpeta
        real lleva el año de estreno para distinguir un remake del
        original y show_name no lo trae). Devuelve el set de episodios
        encontrados en esa carpeta, o None si no hay ninguna coincidencia
        de esa confianza.

        *all_show_names* (nombres de TODAS las series conocidas, no solo
        la que se cruza) activa el guardián de series hermanas: una carpeta
        reclamada solo por parecido no se une si es exactamente la de otra
        serie conocida (ver sibling_blocks_folder). Sin él, None por
        defecto, el comportamiento es el de antes."""
        matched, best_candidate, best_ratio = MissingEpScanMixin._match_ftp_folders(
            ftp_index, show_name, known_folder_name, known_year,
            all_show_names=all_show_names)

        if not matched:
            if best_candidate:
                _log.info("Cruce FTP: '%s' -- candidato más parecido '%s' con ratio %.2f "
                          "(hace falta >= %.2f para darlo por la misma serie)",
                          show_name, best_candidate, best_ratio, _FTP_PRESENT_MIN_RATIO)
            return None

        # Unión de TODAS las carpetas que casan, no solo la primera: una
        # misma serie puede estar repartida en varias carpetas del servidor
        # (caso real: "Prodigiosa Las aventuras de Ladybug" con solo la T6
        # y "Miraculous las aventuras de Ladybug" con las T1-T5 -- el
        # título en castellano y el original, cada uno con su carpeta).
        # Antes esta función devolvía la PRIMERA que casaba y las demás ni
        # se miraban, así que los 129 episodios de la otra carpeta salían
        # como "te faltan" estando ahí al lado.
        present = set()
        for root, folder_name in matched:
            present |= ftp_index[root][folder_name]
        if len(matched) > 1:
            _log.info("Cruce FTP: '%s' -> %d carpeta(s) unidas (%s), %d episodio(s) en total",
                      show_name, len(matched),
                      ", ".join(sorted(folder for _root, folder in matched)), len(present))
        return present

    @staticmethod
    def _missing_ep_known_year(r: dict):
        """Año de estreno de *r* (de first_air_date, "AAAA-MM-DD") como
        texto de 4 dígitos, o None si no se conoce -- para
        find_existing_category_folder/_match_ftp_present, ver
        core.series_match.best_match_with_year."""
        year = (r.get("first_air_date") or "")[:4]
        return year if year.isdigit() else None

    def _rescan_single_series_worker(self, r: dict) -> tuple:
        """Hilo de fondo de _rescan_single_missing_ep_series -- misma
        lógica que el bucle principal de _scan_missing_episodes para UNA
        serie (recomprobación siempre forzada, como un hueco visto antes),
        más el cruce con el FTP acotado a su propia carpeta (ver
        _cross_check_single_result_with_ftp). Devuelve (resultados,
        motivo_de_borrado) -- resultados es una lista con 0 o 1 filas,
        mismo contrato que ya esperaba _finish_single_missing_ep_rescan;
        motivo_de_borrado es None salvo cuando resultados == [] porque la
        serie ya no existe en el servidor de medios (ver más abajo), para
        poder mostrar un mensaje que diga POR QUÉ se quitó en vez del
        genérico "ya no tiene huecos"."""
        from core.media_server_refresh import (get_jellyfin_episodes, get_plex_episodes,
                                               get_jellyfin_series_item, get_plex_series_item,
                                               ITEM_NOT_FOUND)
        from core.missing_episodes import (find_missing_episodes, format_missing_summary,
                                           apply_season_split_filter, find_unknown_seasons,
                                           looks_like_absolute_numbering, remap_absolute_episodes,
                                           normalize_tmdb_season_numbering)
        from core.missing_episodes_cache import load_cache, save_cache, remove_series_from_cache

        old_tmdb_id = r["tmdb_id"]
        tmdb_id = old_tmdb_id
        name = r["name"]
        source = r.get("source")
        server_id = r.get("server_id")
        folder_name = r.get("folder_name")
        # "Ignorar capítulo"/"Ignorar temporada" (ver
        # _toggle_missing_ep_season_ignore/_episode_ignore) tienen que
        # sobrevivir a reescanear esta serie sola, igual que "ignored"
        # (serie entera, línea de abajo).
        ignored_seasons = set(r.get("ignored_seasons") or [])
        ignored_episodes = {s: set(eps) for s, eps in (r.get("ignored_episodes") or {}).items()}
        if not source or not server_id:
            # Fila de antes de que estos campos se guardaran en caché --
            # sin ellos no hay atajo posible, hace falta un reescaneo
            # completo (que sí vuelve a traer server_id) para poder
            # reescanear esta serie sola la próxima vez.
            self._scan_notify(
                f"\"{name}\" necesita un reescaneo completo antes de poder reescanearse sola", _WARNING_COLOR)
            return [r], None

        # Releer la ficha ACTUAL de esta serie en el servidor (no solo sus
        # episodios) -- si el usuario corrigió a mano la identificación en
        # Jellyfin/Plex (cambió a qué ficha de TMDB apunta), la fila
        # todavía tenía el tmdb_id VIEJO guardado en caché de la última
        # vez que se pidió la biblioteca entera, y "reescanear esta serie"
        # seguiría comparando huecos contra la ficha equivocada aunque el
        # servidor ya estuviera corregido.
        if source == "jellyfin":
            item = get_jellyfin_series_item(self.config_data.get("jellyfin_host", ""),
                                            self.config_data.get("jellyfin_api_key", ""), server_id)
        else:
            item = get_plex_series_item(self.config_data.get("plex_host", ""),
                                        self.config_data.get("plex_token", ""), server_id)
        if item is ITEM_NOT_FOUND:
            # A diferencia de item is None (fallo de red/timeout, podría
            # ser algo temporal -- se deja la fila tal cual, ver más
            # abajo), esto es una respuesta confirmada del servidor: esa
            # serie ya no existe en su biblioteca (se borró). No tiene
            # sentido seguir vigilando huecos de algo que ya no está, así
            # que se quita de la caché aquí mismo -- igual que ya hace un
            # reescaneo completo con las series que desaparecen (ver
            # _scan_missing_episodes).
            cache = dict(load_cache())
            remove_series_from_cache(cache, old_tmdb_id)
            save_cache(cache)
            self._push_missing_episodes_to_ftp()
            server_label = "Jellyfin" if source == "jellyfin" else "Plex"
            return [], f"ya no está en {server_label}"
        if item and item.get("tmdb_id"):
            if item["tmdb_id"] != old_tmdb_id:
                _log.info("Reescaneo individual: '%s' cambió de tmdb_id %s -> %s (identificación "
                          "corregida en el servidor)", item.get("name") or name, old_tmdb_id, item["tmdb_id"])
            tmdb_id = item["tmdb_id"]
            name = item.get("name") or name
            folder_name = item.get("folder_name", folder_name)   # Plex no lo trae, se conserva el de antes

        details = self.tmdb.get_tv_details(tmdb_id)
        first_air_date = details.get("first_air_date", "") or ""
        # Fuente de verdad: FTP con un único listado (no Jellyfin/Plex, que aún cree que los borrados siguen ahí)
        present = None
        try:
            from core.ftp_categories import choose_category
            from core.api_client import MediaInfo
            dummy = MediaInfo(title=name, media_type="tv", tmdb_id=tmdb_id, season=1, episode=1)
            # Localizar carpeta(s) de la serie en FTP y listar una sola vez (todas las que casan, como Prodigiosa en 2 carpetas)
            own_ftp_single = self._new_ftp_client()
            try:
                own_ftp_single.connect(
                    self.config_data.get("ftp_host", ""), int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""), self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if own_ftp_single.is_connected():
                    cats = self.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}).get("tv", [])
                    present = set()
                    from core.series_match import series_similarity, normalize_series_name, sibling_blocks_folder
                    norm_target = normalize_series_name(name)
                    _sibling_names = self._known_series_names_from_cache() | {name}
                    for cat in cats:
                        root = cat.get("root", "")
                        if not root:
                            continue
                        try:
                            dirs = own_ftp_single.list_dirs(root) or []
                        except Exception:
                            continue
                        for d in dirs:
                            # sibling_blocks_folder: la carpeta exacta de otra
                            # serie conocida no se reclama por parecido (caso
                            # Dragon Ball Daima / Dragon Ball) -- ver
                            # _match_ftp_folders, mismo guardián.
                            if (series_similarity(norm_target, normalize_series_name(d), strict=False) >= 0.85 or norm_target in normalize_series_name(d) or normalize_series_name(d) in norm_target) and not sibling_blocks_folder(name, d, _sibling_names):
                                series_path = f"{root.rstrip('/')}/{d}"
                                try:
                                    subdirs = own_ftp_single.list_dirs(series_path) or []
                                except Exception:
                                    subdirs = []
                                candidates = [series_path] + [f"{series_path.rstrip('/')}/{sd}" for sd in subdirs if sd.lower().startswith("temporada")]
                                for p in candidates:
                                    try:
                                        files = own_ftp_single.list_files(p) or []
                                        for fn in files:
                                            from core.api_client import detect_episode as _det
                                            det = _det(fn)
                                            if det and det.get("season") and det.get("episode"):
                                                present.add((det["season"], det["episode"]))
                                    except Exception:
                                        continue
                    if not present:
                        # No se encontró nada en FTP para esta serie → vacía (todo falta) o None si FTP falló
                        present = set()
                else:
                    present = None
            finally:
                try:
                    own_ftp_single.disconnect()
                except Exception:
                    pass
        except Exception:
            present = None
        if present is None:
            # Fallback a Jellyfin/Plex solo si FTP no respondió (evita falsos huecos por FTP caído)
            if source == "jellyfin":
                present = get_jellyfin_episodes(self.config_data.get("jellyfin_host", ""),
                                                self.config_data.get("jellyfin_api_key", ""), server_id)
            else:
                present = get_plex_episodes(self.config_data.get("plex_host", ""),
                                            self.config_data.get("plex_token", ""), server_id)
            if present is not None:
                # El fallback trae presencia del servidor: aprovechar para
                # leer también sus pistas de audio reales (ver
                # core/server_audio.py) -- True/False por episodio presente
                # en spanish_dub_cache, solo con el interruptor de doblaje.
                self._refresh_server_audio(source, server_id, tmdb_id)
        if present is None:
            return [r], None   # sin dato fiable ahora mismo -- se deja la fila tal cual

        expected = {}
        episode_titles = {}
        season_air_dates = {}
        episode_air_dates = {}
        for season in details.get("seasons", []):
            n = season.get("season_number", 0)
            if n <= 0:   # temporada 0 = especiales, no cuenta como hueco
                continue
            try:
                eps = self.tmdb.get_season_episodes(tmdb_id, n)
            except Exception:
                continue
            nums = [e["episode_number"] for e in eps if e.get("episode_number")]
            if nums:
                expected[n] = nums
                episode_titles[n] = {e["episode_number"]: e.get("name", "") for e in eps
                                     if e.get("episode_number")}
                season_air_dates[n] = season.get("air_date", "") or ""
                episode_air_dates[n] = {e["episode_number"]: e.get("air_date", "") for e in eps
                                        if e.get("episode_number")}

        # TMDB a veces numera sin reiniciar en cada temporada (real: Hunter
        # x Hunter 2011, T2: 63-136, T3: 137-148) mientras el servidor y los
        # releases reinician en 1: comparado tal cual daría cientos de falsos
        # "faltan". Se normaliza a numeración por temporada (vacío para
        # series normales) antes de comparar -- títulos y fechas van
        # indexados por esos mismos números y se renumeran igual.
        expected, episode_titles, episode_air_dates, ep_offsets = \
            normalize_tmdb_season_numbering(expected, episode_titles, episode_air_dates)
        if any(ep_offsets.values()):
            _log.info("Numeración TMDB continua en '%s': desplazamientos %s -- "
                      "se compara en numeración por temporada", name,
                      {s: o for s, o in ep_offsets.items() if o})

        season_episode_counts = {s: len(eps) for s, eps in expected.items()}
        absolute_numbering = looks_like_absolute_numbering(season_episode_counts, present)
        present_for_compare = remap_absolute_episodes(season_episode_counts, present) \
            if absolute_numbering else present

        missing = find_missing_episodes(expected, present_for_compare)
        unknown_seasons = find_unknown_seasons(present_for_compare, expected.keys())
        present_season_counts = {}
        for season, _ep in present_for_compare:
            present_season_counts[season] = present_season_counts.get(season, 0) + 1

        cache = dict(load_cache())
        if tmdb_id != old_tmdb_id:
            cache.pop(str(old_tmdb_id), None)   # identificación corregida -- no dejar la entrada vieja huérfana
        cache_entry = {
            "name": name, "source": source, "server_id": server_id,
            "folder_name": folder_name,
            "last_episode_id": (details.get("last_episode_to_air") or {}).get("id"),
            "expected": {str(k): v for k, v in expected.items()},
            "episode_number_offsets": {str(s): o for s, o in ep_offsets.items()},
            "episode_titles": {str(s): {str(e): t for e, t in eps.items()}
                               for s, eps in episode_titles.items()},
            "missing": {str(k): v for k, v in missing.items()},
            "unknown_seasons": sorted(unknown_seasons),
            "present_season_counts": {str(k): v for k, v in present_season_counts.items()},
            "absolute_numbering": absolute_numbering,
            "first_air_date": first_air_date,
            "season_air_dates": {str(s): d for s, d in season_air_dates.items()},
            "episode_air_dates": {str(s): {str(e): d for e, d in eps.items()}
                                  for s, eps in episode_air_dates.items()},
            "ignored_seasons": sorted(ignored_seasons),
            "ignored_episodes": {str(s): sorted(eps) for s, eps in ignored_episodes.items()},
            "ignored": r.get("ignored", False),
            "play_count": r.get("play_count", 0),
            "last_played_ts": r.get("last_played_ts"),
            # Se conserva el veredicto de la IA que ya tenía esta fila --
            # salvo que la identificación se acabara de corregir (tmdb_id
            # distinto), en cuyo caso el veredicto viejo era sobre OTRA
            # ficha y no debe arrastrarse.
            "ai_verdict": r.get("ai_verdict") if tmdb_id == old_tmdb_id else None,
        }
        cache[str(tmdb_id)] = cache_entry

        if not (missing or unknown_seasons):
            save_cache(cache)
            self._push_missing_episodes_to_ftp()
            return [], None   # ya no le falta nada -- se quita de la lista

        # missing aquí es SOLO para mostrar -- cache_entry["missing"] se
        # corrige más abajo, tras el cruce con el FTP, antes de guardar.
        missing, split_seasons = apply_season_split_filter(missing, expected)
        new_row = {
            "tmdb_id": tmdb_id, "name": name, "source": source, "server_id": server_id,
            "missing": missing, "summary": format_missing_summary(name, missing),
            "ignored": r.get("ignored", False), "episode_titles": episode_titles,
            "split_seasons": split_seasons,
            "unknown_seasons": unknown_seasons,
            "play_count": r.get("play_count", 0), "last_played_ts": r.get("last_played_ts"),
            "expected_episodes": {s: sorted(eps) for s, eps in expected.items()},
            "episode_number_offsets": {s: o for s, o in ep_offsets.items() if o},
            "tmdb_season_counts": {s: len(eps) for s, eps in expected.items()},
            "server_season_counts": present_season_counts,
            "ai_verdict": r.get("ai_verdict") if tmdb_id == old_tmdb_id else None,
            "absolute_numbering": absolute_numbering,
            "folder_name": folder_name,
            "first_air_date": first_air_date,
            "season_air_dates": season_air_dates,
            "episode_air_dates": episode_air_dates,
            "ignored_seasons": ignored_seasons,
            "ignored_episodes": ignored_episodes,
        }
        new_row = self._cross_check_single_result_with_ftp(new_row)
        # Propagar la corrección del cruce FTP a la caché de disco -- antes
        # se guardaba (arriba) con el hueco de ANTES del cruce, así que una
        # corrección real (ej. Dragon Ball GT: 3 episodios que sí estaban
        # en el servidor) solo vivía en esta sesión y volvía a aparecer tal
        # cual en el próximo arranque, mismo bug que en _scan_missing_episodes.
        cache_entry["missing"] = {str(k): v for k, v in new_row["missing"].items()}
        cache_entry["unknown_seasons"] = sorted(new_row.get("unknown_seasons") or [])
        cache_entry["present_season_counts"] = {
            str(k): v for k, v in (new_row.get("server_season_counts") or {}).items()}
        save_cache(cache)
        self._push_missing_episodes_to_ftp()

        if not (new_row["missing"] or new_row["unknown_seasons"]):
            return [], None
        return [new_row], None

    def _fetch_dub_cutoff(self, tmdb_id: int, name: str, pending: list,
                          want_eldoblaje: bool = True, want_streaming: bool = True,
                          want_crunchyroll: bool = True,
                          want_rtve: bool = True) -> dict:
        """{"eldoblaje": {...}, "streaming": {...}, "crunchyroll": {...},
        "rtve": {...}} con el corte de doblaje fresco para una serie (cada
        uno {"cutoff", "checked_at", "source"}): eldoblaje.com hasta 3
        candidatos (con reintento de cuenta absoluta vía tamaños TMDB),
        Streaming Availability por audio ESP en España si hay key (ver
        core/streaming_availability.py), Crunchyroll por audio es-ES
        anónimo (ver core/crunchyroll_client.py -- solo da corte en anime,
        para no-anime devuelve {} barato), RTVE Play por idioma "es" de
        sus vídeos (ver core/rtve_client.py -- sobre todo producción
        española; fuera de ella devuelve {} barato), wiki de doblaje como
        respaldo si todas dan {}. Solo se consulta lo pedido (want_*):
        cada fuente tiene su propia frescura (ver
        core/eldoblaje.cutoff_is_fresh: 30 días con datos, 3 sin ellos)."""
        import time as _t
        now = _t.time()
        out = {}
        series_seasons = sorted({s for (t, _n, s, _e) in pending if t == tmdb_id})
        sizes: dict = {}
        if want_eldoblaje:
            out["eldoblaje"] = {"cutoff": {}, "checked_at": now, "source": "none"}
            try:
                from core.eldoblaje import search_series, get_dub_summary, \
                    parse_dub_cutoff, has_absolute_dub_count
                for cand in (search_series(name) or [])[:3]:
                    summary = get_dub_summary(cand["id"])
                    if not summary:
                        continue
                    cutoff = parse_dub_cutoff(summary, series_seasons)
                    if not cutoff and has_absolute_dub_count(summary):
                        if not sizes:
                            sizes = self._dub_season_sizes(tmdb_id, series_seasons)
                        if sizes:
                            cutoff = parse_dub_cutoff(summary, series_seasons, sizes)
                    if cutoff:
                        out["eldoblaje"] = {"cutoff": cutoff, "checked_at": now,
                                            "source": "eldoblaje"}
                        break
            except Exception:
                pass
        if want_streaming:
            out["streaming"] = {"cutoff": {}, "checked_at": now, "source": "none"}
            try:
                sa_key = (self.config_data.get("streaming_availability_key", "") or "").strip()
                if sa_key:
                    from core.streaming_availability import cutoff_for_series
                    if not sizes:
                        sizes = self._dub_season_sizes(tmdb_id, series_seasons)
                    cutoff = cutoff_for_series(sa_key, tmdb_id, sizes) if sizes else {}
                    if cutoff:
                        out["streaming"] = {"cutoff": cutoff, "checked_at": now,
                                            "source": "streaming_availability"}
            except Exception:
                pass
        if want_crunchyroll:
            out["crunchyroll"] = {"cutoff": {}, "checked_at": now, "source": "none"}
            try:
                from core.crunchyroll_client import CrunchyrollClient
                if getattr(self, "_crunchyroll_client", None) is None:
                    self._crunchyroll_client = CrunchyrollClient()
                cutoff = self._crunchyroll_client.cutoff_for_series(name, series_seasons)
                if cutoff:
                    out["crunchyroll"] = {"cutoff": cutoff, "checked_at": now,
                                          "source": "crunchyroll"}
            except Exception:
                pass
        if want_rtve:
            out["rtve"] = {"cutoff": {}, "checked_at": now, "source": "none"}
            try:
                from core.rtve_client import RTVEClient
                if getattr(self, "_rtve_client", None) is None:
                    self._rtve_client = RTVEClient()
                cutoff = self._rtve_client.cutoff_for_series(name, series_seasons)
                if cutoff:
                    out["rtve"] = {"cutoff": cutoff, "checked_at": now,
                                   "source": "rtve"}
            except Exception:
                pass
        if (want_eldoblaje and out.get("eldoblaje", {}).get("cutoff")) or \
           (want_streaming and out.get("streaming", {}).get("cutoff")) or \
           (want_crunchyroll and out.get("crunchyroll", {}).get("cutoff")) or \
           (want_rtve and out.get("rtve", {}).get("cutoff")):
            return out
        if want_eldoblaje:
            try:
                from core.doblaje_wiki import cutoff_for_series as wiki_cutoff
                cutoff = wiki_cutoff(name, series_seasons)
                if cutoff:
                    out["eldoblaje"] = {"cutoff": cutoff, "checked_at": now,
                                        "source": "wiki"}
            except Exception:
                pass
        return out

    def _dub_season_sizes(self, tmdb_id: int, seasons: list) -> dict:
        """{temporada: nº_episodios} vía TMDB -- solo recuentos para validar
        mapeos posicionales y repartir cuentas absolutas, nunca veredicto
        de doblaje (TMDB no sabe de audio)."""
        sizes = {}
        for s in seasons:
            try:
                sizes[s] = len(self.tmdb.get_season_episodes(tmdb_id, s))
            except Exception:
                pass
        return sizes
