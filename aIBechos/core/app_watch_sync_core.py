"""
"Sincronizar visionado" sin interfaz: compara lo visto por cada pareja de
usuarios emparejados Plex<->Jellyfin, aplica las marcas que faltan, guarda el
historial y lanza la sincronización programada (hilo de fondo, cada minuto
comprueba la hora configurada). Mixin que hereda QtAppCore
(gui_qt/core_host.py).

Gancho de interfaz: _on_watch_sync_ran() tras una sincronización programada.
"""

import json
from pathlib import Path
import threading
import time as _time

from core.appdirs import app_data_dir as _appdata_dir
from core.applog import get_logger

_log = get_logger("aIBechos.gui", "app.log")


class WatchSyncCoreMixin:

    def _watch_sync_collect_actions(self, mappings, status_cb=None):
        """Lee el estado de visionado de ambas plataformas para
        *mappings* y calcula las acciones pendientes
        (core.watch_sync.diff_watched_items). Pura I/O, pensada para
        ejecutarse en CUALQUIER hilo (nunca toca widgets directamente) --
        tanto el botón manual (_run_watch_sync) como la sincronización
        programada (_run_scheduled_watch_sync) la comparten, para no
        duplicar esta lógica en dos sitios que podrían acabar
        divergiendo. status_cb(texto), opcional, se llama con
        actualizaciones de progreso -- quien lo pase decide si lo
        reenvía a la UI vía self.after() (el programador automático no
        pasa ninguno, no hay UI que actualizar). Devuelve (actions,
        failed_users)."""
        from core.watch_sync import WatchedItem, diff_watched_items
        from core.media_server_refresh import (
            get_plex_user_token, get_plex_movie_watched, get_plex_episode_watched,
            get_jellyfin_movie_watched, get_jellyfin_episode_watched,
            get_plex_series, get_jellyfin_series,
        )
        from concurrent.futures import ThreadPoolExecutor

        plex_host = self.config_data.get("plex_host", "")
        plex_owner_token = self.config_data.get("plex_token", "")
        jellyfin_host = self.config_data.get("jellyfin_host", "")
        jellyfin_key = self.config_data.get("jellyfin_api_key", "")

        all_items = []
        failed_users = []
        # Peliculas + listado de series: rapido (un unico listado por
        # plataforma, no una llamada por titulo) -- se hace por usuario,
        # sin necesitar reparto especial.
        per_mapping_shows = []   # [(mapping, plex_token, [(show, jf_show), ...]), ...]

        for m in mappings:
            plex_token = get_plex_user_token(plex_host, plex_owner_token, m["plex_user_id"])
            if plex_token is None:
                failed_users.append(m)
                continue

            plex_movies = get_plex_movie_watched(plex_host, plex_token) or {}
            jf_movies = get_jellyfin_movie_watched(
                jellyfin_host, jellyfin_key, m["jellyfin_user_id"]) or {}
            for tmdb_id in set(plex_movies) | set(jf_movies):
                p = plex_movies.get(tmdb_id, {})
                j = jf_movies.get(tmdb_id, {})
                name = p.get("name") or j.get("name") or f"(película {tmdb_id})"
                all_items.append(WatchedItem(
                    media_type="movie", tmdb_id=tmdb_id, name=name,
                    plex_watched=p.get("watched", False), jellyfin_watched=j.get("watched", False),
                    plex_ref=p.get("ref"), jellyfin_ref=j.get("ref"),
                    plex_user_id=m["plex_user_id"], jellyfin_user_id=m["jellyfin_user_id"]))

            plex_shows = get_plex_series(plex_host, plex_token) or []
            jf_shows = get_jellyfin_series(jellyfin_host, jellyfin_key) or []
            jf_shows_by_tmdb = {s["tmdb_id"]: s for s in jf_shows if s["tmdb_id"] is not None}
            matched = [(show, jf_shows_by_tmdb[show["tmdb_id"]]) for show in plex_shows
                      if show["tmdb_id"] is not None and show["tmdb_id"] in jf_shows_by_tmdb]
            per_mapping_shows.append((m, plex_token, matched))

        # Episodios: la parte lenta (una llamada por serie a cada
        # plataforma) -- repartida entre hasta 8 hilos SIEMPRE, sin
        # importar cuantos usuarios haya (antes solo se paralelizaba por
        # usuario, así que con 1 solo usuario no paralelizaba nada en
        # absoluto). Progreso visible cada pocas series para que no
        # parezca colgada durante los varios minutos que puede tardar.
        work_items = [(m, plex_token, show, jf_show)
                     for m, plex_token, shows in per_mapping_shows
                     for show, jf_show in shows]
        total = len(work_items)
        done = [0]

        def _fetch_episodes(work_item):
            m, plex_token, show, jf_show = work_item
            plex_eps = get_plex_episode_watched(plex_host, plex_token, show["rating_key"]) or {}
            jf_eps = get_jellyfin_episode_watched(
                jellyfin_host, jellyfin_key, m["jellyfin_user_id"], jf_show["id"]) or {}
            items = []
            for se in set(plex_eps) | set(jf_eps):
                p = plex_eps.get(se, {})
                j = jf_eps.get(se, {})
                items.append(WatchedItem(
                    media_type="episode", tmdb_id=show["tmdb_id"], name=show["name"],
                    season=se[0], episode=se[1],
                    plex_watched=p.get("watched", False), jellyfin_watched=j.get("watched", False),
                    plex_ref=p.get("ref"), jellyfin_ref=j.get("ref"),
                    plex_user_id=m["plex_user_id"], jellyfin_user_id=m["jellyfin_user_id"]))
            done[0] += 1
            if status_cb and (done[0] % 10 == 0 or done[0] == total):
                status_cb(f"Leyendo episodios: serie {done[0]}/{total}...")
            return items

        if work_items:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for items in pool.map(_fetch_episodes, work_items):
                    all_items.extend(items)

        return diff_watched_items(all_items), failed_users

    def _watch_sync_apply(self, actions, status_cb=None):
        """Escribe *actions* en Plex/Jellyfin y actualiza
        watch_sync_last_run_ts. Pura I/O, pensada para ejecutarse en
        CUALQUIER hilo -- compartida entre el botón manual
        (_confirm_watch_sync_preview) y la sincronización programada
        (_run_scheduled_watch_sync). status_cb(texto), opcional, mismo
        contrato que en _watch_sync_collect_actions. Devuelve (ok, fail)."""
        from core.media_server_refresh import get_plex_user_token, mark_plex_watched, mark_jellyfin_watched

        plex_host = self.config_data.get("plex_host", "")
        plex_owner_token = self.config_data.get("plex_token", "")
        jellyfin_host = self.config_data.get("jellyfin_host", "")
        jellyfin_key = self.config_data.get("jellyfin_api_key", "")

        # Un token por CADA usuario de Plex que aparezca entre las
        # acciones pendientes (no uno por accion) -- se re-deriva aqui,
        # nunca se reutiliza el que se pidio durante la lectura (ver el
        # plan: los tokens por usuario nunca se cachean).
        plex_user_ids = {a.item.plex_user_id for a in actions
                         if a.target == "plex" and a.item.plex_user_id}
        plex_tokens = {uid: get_plex_user_token(plex_host, plex_owner_token, uid)
                      for uid in plex_user_ids}

        # Para el historial persistente (ver _save_watch_sync_history_entry):
        # los WatchedItem solo llevan el id de usuario, no el nombre --
        # se resuelve aquí una vez contra el emparejamiento actual, en vez
        # de por cada acción.
        mappings = self.config_data.get("watch_sync_user_mappings", [])
        plex_name_by_id = {m["plex_user_id"]: m["plex_user_name"] for m in mappings}
        jf_name_by_id = {m["jellyfin_user_id"]: m["jellyfin_user_name"] for m in mappings}

        ok, fail = 0, 0
        total = len(actions)
        for i, action in enumerate(actions, 1):
            item = action.item
            if action.target == "jellyfin":
                success = bool(item.jellyfin_user_id and item.jellyfin_ref) and mark_jellyfin_watched(
                    jellyfin_host, jellyfin_key, item.jellyfin_user_id, item.jellyfin_ref)
            else:
                token = plex_tokens.get(item.plex_user_id)
                success = bool(token and item.plex_ref) and mark_plex_watched(
                    plex_host, token, item.plex_ref)
            if success:
                ok += 1
            else:
                fail += 1
                _log.warning("Sincronizar visionado: fallo al marcar '%s' en %s (usuario plex=%s, jellyfin=%s)",
                            item.name, action.target, item.plex_user_id, item.jellyfin_user_id)

            person = (plex_name_by_id.get(item.plex_user_id)
                     or jf_name_by_id.get(item.jellyfin_user_id) or "?")
            self._save_watch_sync_history_entry(action.target, item, "ok" if success else "error", person)

            if status_cb and (i % 10 == 0 or i == total):
                status_cb(f"Sincronizando: {i}/{total} ({ok} aplicados, {fail} fallidos)...")

        self.config_data.set("watch_sync_last_run_ts", _time.time())
        self.config_data.save()
        return ok, fail

    def _watch_sync_history_path(self) -> Path:
        return _appdata_dir() / "watch_sync_history.json"

    def _load_watch_sync_history(self) -> list:
        try:
            p = self._watch_sync_history_path()
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _save_watch_sync_history_entry(self, target: str, item, status: str, person: str):
        with self._history_lock:
            history = self._load_watch_sync_history()
            history.append({
                "ts":         _time.time(),
                "target":     target,       # "plex" | "jellyfin" -- dónde se escribió
                "media_type": item.media_type,
                "name":       item.name,
                "season":     item.season,
                "episode":    item.episode,
                "person":     person,
                "status":     status,       # "ok" | "error"
            })
            if len(history) > 500:
                history = history[-500:]
            try:
                self._watch_sync_history_path().write_text(
                    json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            self._watch_sync_history_dirty = True

    def _run_scheduled_watch_sync(self):
        """Sincronización automática programada (ver
        _start_watch_sync_scheduler/_check_watch_sync_schedule) -- a
        diferencia del botón manual, aplica DIRECTAMENTE sin mostrar
        vista previa ni pedir confirmación: decisión explícita del
        usuario al activar la programación en Configuración → Cliente,
        sabiendo que renuncia a la revisión humana previa que tiene el
        resto de esta función. Se ejecuta ya en el hilo del programador
        (ver _watch_sync_scheduler_loop), no lanza otro hilo aparte."""
        mappings = self.config_data.get("watch_sync_user_mappings", [])
        if not mappings:
            _log.info("Sincronización programada: omitida, no hay usuarios emparejados")
            return
        _log.info("Sincronización programada: iniciando (%d usuario(s) emparejado(s))", len(mappings))
        try:
            actions, failed_users = self._watch_sync_collect_actions(mappings)
            ok, fail = self._watch_sync_apply(actions) if actions else (0, 0)
        except Exception:
            _log.exception("Sincronización programada: fallo inesperado")
            return
        _log.info("Sincronización programada: completada -- %d aplicado(s), %d fallido(s), "
                  "%d usuario(s) no verificado(s)", ok, fail, len(failed_users))
        if not actions:
            # Nada que aplicar -- watch_sync_apply no se llamó, así que
            # watch_sync_last_run_ts tampoco se actualizó; se hace aquí
            # para que "ya se sincronizó hoy" siga siendo cierto y el
            # programador no reintente en el siguiente minuto.
            self.config_data.set("watch_sync_last_run_ts", _time.time())
            self.config_data.save()
        self.after(0, lambda: self._send_notification(
            "aIBechos — Sincronización de visionado",
            f"{ok} cambio(s) aplicados" + (f", {fail} fallido(s)" if fail else "")))
        self.after(0, self._on_watch_sync_ran)
        self.after(0, self._refresh_watch_sync_history_view)

    def _start_watch_sync_scheduler(self):
        """Hilo en segundo plano que comprueba cada minuto si toca la
        sincronización programada (Configuración → Cliente →
        Sincronizar visionado) -- mismo patrón que AutoWatcher (hilo
        daemon + threading.Event().wait(), nunca time.sleep, para poder
        pararlo al instante si hiciera falta). A diferencia de
        AutoWatcher (que solo arranca si se inicia minimizado o al pulsar
        "Auto"), este se arranca SIEMPRE al abrir la app -- una
        sincronización programada tiene que poder saltar sin importar
        cómo se abrió la ventana esta vez."""
        stop_event = threading.Event()
        self._watch_sync_scheduler_stop = stop_event

        def loop():
            while not stop_event.is_set():
                try:
                    self._check_watch_sync_schedule()
                except Exception:
                    _log.exception("Programador de sincronización: error inesperado")
                stop_event.wait(60)

        threading.Thread(target=loop, daemon=True, name="WatchSyncScheduler").start()

    def _check_watch_sync_schedule(self):
        """Se llama una vez por minuto desde _start_watch_sync_scheduler.
        Todo se lee en caliente de config_data (nunca cacheado), así que
        activar/editar la hora desde Configuración tiene efecto de
        inmediato, sin reiniciar la app."""
        if not self.config_data.get("watch_sync_schedule_enabled", False):
            return
        schedule_time = self.config_data.get("watch_sync_schedule_time", "")
        if not schedule_time or _time.strftime("%H:%M") != schedule_time:
            return
        last_run = self.config_data.get("watch_sync_last_run_ts", 0)
        if last_run and _time.strftime("%Y-%m-%d", _time.localtime(last_run)) == _time.strftime("%Y-%m-%d"):
            return   # ya se sincronizó hoy (manual o programada) -- no repetir dentro del mismo minuto/día
        self._run_scheduled_watch_sync()
