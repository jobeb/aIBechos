"""
Autocompletado de series (el rayo ⚡ de "Episodios que faltan"): activar/
desactivar por serie (con propiedad compartida entre equipos), la pasada
periódica que busca en aMule y descarga los capítulos que faltan, reintentos
con espera creciente, "desatasco" de descargas paradas y "Forzar búsqueda".

Mixin sin interfaz que hereda QtAppCore (gui_qt/core_host.py). Además de lo de core/app_files_core.py, el anfitrión aporta:
  _hide_no_dub_enabled()            interruptor "Ocultar sin doblaje ES"
  _warn_dialog(titulo, texto)       aviso modal (hilo de la interfaz)
  _make_confirm_dialog(...)         confirmación -> objeto con .result
  _forget_auto_countdown(tmdb_id)   quitar la cuenta atrás de la serie
  _refresh_missing_ep_auto_button(tmdb_id), _render_missing_episodes_table(...)
y el estado: _missing_ep_results, _spanish_dub_cache, _auto_series_shared,
_amule_ec_lock, _missing_ep_auto_busy, _missing_ep_auto_worker.
"""

import threading
import time as _time

from core import auto_complete_state, shared_data
from core.amule_search import build_amule_query
from core.api_client import detect_episode
from core.applog import get_logger
from core.ec_client import EcClient
from core.series_match import normalize_series_name
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.gui", "app.log")


class AutoCompleteMixin:
    # Constantes de clase (antes en App, gui/app.py)
    _AUTO_SERIES_SYNC_MIN_INTERVAL = 20   # segundos, ver _sync_auto_series_from_ftp
    _AUTO_COMPLETE_INTERVAL = 30 * 60
    _AUTO_COMPLETE_STARTUP_DELAY = 45
    _AUTO_RETRY_BASE_S = auto_complete_state.RETRY_BASE_S
    _AUTO_RETRY_FACTOR = auto_complete_state.RETRY_FACTOR
    _AUTO_RETRY_MAX_S = auto_complete_state.RETRY_MAX_S

    @staticmethod
    def _fmt_mark_ts(ts) -> str:
        """epoch -> "DD/MM/AAAA HH:MM" para tooltips de quién/cuándo, o ""
        si no hay fecha válida (marcas de antes de guardarla)."""
        try:
            ts = int(ts or 0)
        except (TypeError, ValueError):
            return ""
        if ts <= 0:
            return ""
        import datetime as _dt
        try:
            return _dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
        except (OSError, OverflowError, ValueError):
            return ""

    def _transfer_auto_owners(self, old_owner: str, new_owner: str):
        """La propiedad compartida del rayo viaja con "Tu nombre" igual
        que las reservas (ver _resolve_app_user_name_change y
        core/auto_series.py)."""
        from core.auto_series import transfer_auto_owner, save_local_cache as _save_auto_series_cache
        self._auto_series_shared = transfer_auto_owner(
            self._auto_series_shared, old_owner, new_owner)
        _save_auto_series_cache(self._auto_series_shared)
        self._push_auto_series_to_ftp(
            lambda data: transfer_auto_owner(data, old_owner, new_owner))

    def _remove_all_auto_owners(self, owner: str):
        """Quita la propiedad compartida del rayo de *owner* (opción
        "desproteger" al cambiar de nombre) -- el rayo LOCAL de este
        equipo no se toca, solo la atribución compartida."""
        from core.auto_series import remove_all_auto_by_owner, save_local_cache as _save_auto_series_cache
        self._auto_series_shared = remove_all_auto_by_owner(self._auto_series_shared, owner)
        _save_auto_series_cache(self._auto_series_shared)
        self._push_auto_series_to_ftp(
            lambda data: remove_all_auto_by_owner(data, owner))

    def _auto_series_remote_path(self) -> str:
        """Ruta remota del JSON de rayos ⚡ compartidos (quién tiene cada
        serie en auto y desde cuándo, ver core/auto_series.py) -- archivo
        propio dentro de la misma carpeta compartida, mismo motivo que
        _reservations_remote_path. OJO: lo compartido es solo informativo;
        lo que descarga cada equipo lo decide su lista local
        (missing_ep_auto_complete), que no viaja por FTP."""
        return self._shared_data_path(shared_data.filename("auto_series"))

    def _apply_synced_auto_series(self, merged: dict, on_done=None):
        from core.auto_series import save_local_cache as _save_auto_series_cache
        self._auto_series_shared = merged
        _save_auto_series_cache(self._auto_series_shared)
        if on_done:
            on_done()
        # Sin refresco de vistas a propósito: el color del rayo lo manda
        # la lista LOCAL (que no cambia con esto) y los tooltips se
        # reevalúan solos al pasar el cursor (ver attach_tooltip).

    def _sync_auto_series_from_ftp(self):
        """Refresca el mirror local de rayos compartidos desde el FTP en
        segundo plano -- mismo motivo y patrón que
        _sync_favorites_from_ftp, incluido el freno de
        _AUTO_SERIES_SYNC_MIN_INTERVAL. Se llama al abrir las pestañas con
        botón ⚡ (Archivos, Episodios que faltan, Recomendados)."""
        now = _time.time()
        if now - self._last_auto_series_sync_ts < self._AUTO_SERIES_SYNC_MIN_INTERVAL:
            return
        self._last_auto_series_sync_ts = now

        remote_path = self._auto_series_remote_path()
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
                self.after(0, lambda: self._apply_synced_auto_series(remote_data))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _push_auto_series_to_ftp(self, transform, on_done=None):
        """Sube el cambio de *transform* al FTP en segundo plano --
        descarga el JSON remoto fresco, le aplica *transform* (no lo que
        hubiera en memoria) y lo vuelve a subir, para minimizar la carrera
        si otro cliente cambió algo distinto mientras tanto. Mismo patrón
        que _push_reservations_to_ftp."""
        remote_path = self._auto_series_remote_path()
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
                    self.after(0, lambda: self._apply_synced_auto_series(merged, on_done))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _auto_complete_series(self) -> set:
        """Sets de tmdb_id (str) con el autocompletado activo."""
        return set(self.config_data.get("missing_ep_auto_complete") or [])

    def _auto_since_map(self) -> dict:
        """{tmdb_id_str: epoch} de cuándo se activó el rayo ⚡ de cada
        serie (tooltip "activo desde..."). Personal de cada instalación,
        igual que la lista de arriba; las series activadas antes de que
        existiera esta clave no tienen fecha y el tooltip no la muestra."""
        raw = self.config_data.get("missing_ep_auto_since") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _auto_since_ts(self, tmdb_id: int) -> int:
        """Epoch de activación del rayo de *tmdb_id*, 0 si no consta."""
        try:
            return int(self._auto_since_map().get(str(tmdb_id)) or 0)
        except (TypeError, ValueError):
            return 0

    def _is_missing_ep_auto_enabled(self, tmdb_id: int) -> bool:
        return str(tmdb_id) in self._auto_complete_series()

    def _auto_checked_map(self) -> dict:
        """{tmdb_id_str: {season_str: {ep_str: epoch}}} de episodios ya
        resueltos (descarga lanzada, o ya subidos al servidor): no se
        reintentan mientras la marca siga fresca (ver _AUTO_LAUNCHED_GRACE_S).

        El epoch es lo que distingue "lo lancé hace 10 minutos, déjalo en paz"
        de "lo lancé anteayer y sigue sin aparecer, algo salió mal". El formato
        antiguo era {season_str: [ep, ...]} -- una lista sin fecha que además
        anotaba aquí los intentos FALLIDOS; esas entradas se leen igual (ver
        _auto_checked_ts, que las data en 0) y caen al backoff en la primera
        pasada que las toque."""
        return dict(self.config_data.get("missing_ep_auto_checked") or {})

    def _set_auto_checked_episode(self, tmdb_id: int, season: int, episode: int):
        checked = auto_complete_state.set_checked(
            self._auto_checked_map(), tmdb_id, season, episode, int(_time.time()))
        self.config_data.set("missing_ep_auto_checked", checked)

    def _unset_auto_checked_episode(self, tmdb_id: int, season: int, episode: int):
        """Quita un episodio del mapa checked -- se usa cuando la marca ya no
        vale (entrada legacy, o descarga lanzada que nunca llegó al servidor)
        para que el episodio pase al mapa de reintentos con backoff."""
        checked = self._auto_checked_map()
        if auto_complete_state.unset_checked(checked, tmdb_id, season, episode):
            self.config_data.set("missing_ep_auto_checked", checked)

    def _auto_retry_map(self) -> dict:
        """{tmdb_id_str: {season: {ep: {"tries": int, "ts": epoch}}}} de BOCHES
        fallidos (no había candidato en aMule): se reintentan con espera
        creciente (ver _AUTO_RETRY_*). "tries" = nº de intentos ya hechos,
        "ts" = época del último."""
        return dict(self.config_data.get("missing_ep_auto_retries") or {})

    def _set_auto_retry_episode(self, tmdb_id: int, season: int, episode: int):
        retries = self._auto_retry_map()
        eps = retries.setdefault(str(tmdb_id), {}).setdefault(str(season), {})
        prev = eps.get(str(episode)) or {"tries": 0}
        eps[str(episode)] = {"tries": int(prev["tries"]) + 1, "ts": int(_time.time())}
        self.config_data.set("missing_ep_auto_retries", retries)

    def _clear_auto_retry_episode(self, tmdb_id: int, season: int, episode: int):
        retries = self._auto_retry_map()
        entry = retries.get(str(tmdb_id))
        if entry:
            eps = entry.get(str(season))
            if eps:
                eps.pop(str(episode), None)
                if not eps:
                    entry.pop(str(season), None)
            self.config_data.set("missing_ep_auto_retries", retries)

    def _unstuck_map(self) -> dict:
        return dict(self.config_data.get("missing_ep_auto_downloads") or {})

    def _unstuck_get_rec(self, tmdb_id: int, season: int, episode: int) -> dict | None:
        m = self._unstuck_map()
        return ((m.get(str(tmdb_id)) or {}).get(str(season)) or {}).get(str(episode))

    def _unstuck_set_rec(self, tmdb_id: int, season: int, episode: int, rec: dict):
        m = self._unstuck_map()
        m.setdefault(str(tmdb_id), {}).setdefault(str(season), {})[str(episode)] = rec
        self.config_data.set("missing_ep_auto_downloads", m)
        self.config_data.save()

    def _unstuck_clear_rec(self, tmdb_id: int, season: int, episode: int):
        m = self._unstuck_map()
        s = m.get(str(tmdb_id))
        if not s:
            return
        e = s.get(str(season))
        if not e:
            return
        e.pop(str(episode), None)
        if not e:
            s.pop(str(season), None)
        if not s:
            m.pop(str(tmdb_id), None)
        self.config_data.set("missing_ep_auto_downloads", m)
        self.config_data.save()

    def _auto_retry_wait_for(self, tries: int) -> float:
        """Segundos de espera para un episodio con *tries* intentos fallidos
        (backoff exponencial, tope RETRY_MAX_S)."""
        return auto_complete_state.retry_wait_for(tries)

    def _auto_series_next_retry_at(self, tmdb_id: int) -> tuple | None:
        """Calcula cuándo se podrá volver a intentar el primer capítulo de la
        serie que esté en espera de reintento: devuelve (epoch de la próxima
        pasada en que toque, tries del episodio) o None si la serie no tiene
        NINGÚN reintento pendiente ni capítulo que reintentar. Se usa para la
        cuenta atrás del botón ⚡ / detalle de la fila."""
        retries = (self._auto_retry_map().get(str(tmdb_id)) or {})
        now = _time.time()
        best = None
        best_tries = None
        for _season, eps in retries.items():
            for ep, rec in eps.items():
                tries = int(rec.get("tries", 1))
                wait = self._auto_retry_wait_for(tries)
                at = int(rec.get("ts", 0)) + wait
                if at < now:
                    at = now   # ya tocaba: se reintenta en la próxima pasada inmediata
                if best is None or at < best:
                    best = at
                    best_tries = tries
        if best is not None:
            return best, best_tries
        return None

    @staticmethod
    def _auto_countdown_text(at_epoch: float | None) -> str:
        """Texto breve de cuenta atrás para el botón ⚡ / detalle: cuánto
        falta hasta la próxima oportunidad de reintento (la pasada del worker
        que coincide), o "max 30 min" si ya toca (el worker pasa cada 30 min)."""
        if at_epoch is None:
            return ""
        remaining = at_epoch - _time.time()
        if remaining <= 0:
            return "reintento en la próxima pasada (max 30 min)"
        total = int(remaining)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"próximo reintento en {h} h {m} min"
        if m:
            return f"próximo reintento en {m} min {s} s"
        return f"próximo reintento en {s} s"

    def _auto_btn_tooltip(self, tid: int) -> str:
        base = ("Autocompletar ACTIVO: la app busca y descarga sola los capítulos que "
                "faltan de esta serie, también los nuevos que vayan saliendo. Pulsa para "
                "desactivar." if self._is_missing_ep_auto_enabled(tid)
                else "Activar autocompletado: busca y descarga los capítulos que faltan de "
                     "esta serie de forma autónoma y persistente (los nuevos que salgan "
                     "también los añade).")
        if self._is_missing_ep_auto_enabled(tid):
            # El rayo es personal de cada instalación (no viaja por FTP),
            # así que el "quién" es siempre este equipo; el "cuándo" se
            # guarda al activarlo (ver _auto_since_map).
            since = self._fmt_mark_ts(self._auto_since_ts(tid))
            if since:
                base += f"\n\n⚡ Activo desde el {since} en este equipo."
            # Otros equipos con la misma serie en auto (informativo, ver
            # core/auto_series.py): sin contarme a mí.
            from core.auto_series import other_owners
            me = self.config_data.get("app_user_name", "").strip()
            others = other_owners(self._auto_series_shared, "tv", tid, me)
            if others:
                who = ", ".join(
                    f"{o} (desde el {self._fmt_mark_ts(ts)})" if self._fmt_mark_ts(ts) else o
                    for o, ts in sorted(others.items()))
                base += f"\n\n🔁 En auto también en: {who}."
            nxt = self._auto_series_next_retry_at(tid)
            if nxt is not None:
                base += "\n\n⏳ " + self._auto_countdown_text(nxt[0])
        return base

    def _toggle_missing_ep_auto_complete(self, tmdb_id: int):
        """Interruptor del botón "⚡" de una fila: activa/desactiva el
        autocompletado de esa serie y persiste (config personal). Al
        activar, si otro equipo ya la tiene en auto se avisa antes (el
        estado compartido es solo informativo -- ver core/auto_series.py:
        cada equipo sigue descargando por su cuenta)."""
        from core.auto_series import (
            add_auto_owner, remove_auto_owner, other_owners,
            save_local_cache as _save_auto_series_cache)
        key = str(tmdb_id)
        series = self._auto_complete_series()
        new_state = key not in series
        user = self.config_data.get("app_user_name", "").strip()
        name = ""
        row = self._missing_ep_results and next(
            (r for r in self._missing_ep_results if r.get("tmdb_id") == tmdb_id), None)
        if row:
            name = row.get("name", "")
        if not name:
            rec = next((r for r in (getattr(self, "_movies_results", None) or [])
                        if r.get("media_type") == "tv" and r.get("tmdb_id") == tmdb_id), None)
            if rec:
                name = rec.get("title", "")
        if new_state:
            if not user:
                self._warn_dialog(
                    "Falta tu nombre",
                    "Configura \"Tu nombre\" en Ajustes → Conexión FTP antes de activar el rayo, "
                    "así los demás equipos saben quién lo tiene puesto.")
                return
            others = other_owners(self._auto_series_shared, "tv", tmdb_id, user)
            if others:
                who = ", ".join(
                    f"{o} (desde el {self._fmt_mark_ts(ts)})" if self._fmt_mark_ts(ts) else o
                    for o, ts in sorted(others.items()))
                if not self._make_confirm_dialog("Ya está en auto en otro equipo",
                        f"{name or 'Esta serie'} ya la tiene en autocompletado {who}.",
                        "Si la activas también, los dos equipos buscarán y descargarán "
                        "los mismos capítulos por su cuenta.",
                        confirm_text="Activar también", cancel_text="Cancelar").result:
                    return
            series.add(key)
        else:
            series.discard(key)
        self.config_data.set("missing_ep_auto_complete", sorted(series))
        # Fecha de activación para el tooltip (ver _auto_since_map): se
        # guarda al activar y se borra al desactivar, en la misma tacada.
        since = self._auto_since_map()
        now = int(_time.time())
        if new_state:
            since[key] = now
        else:
            since.pop(key, None)
        self.config_data.set("missing_ep_auto_since", since)
        self.config_data.save()
        # Estado compartido (informativo): al activar sumo mi nombre sin
        # pisar a otros dueños; al desactivar solo me quito yo.
        if new_state:
            self._auto_series_shared = add_auto_owner(
                self._auto_series_shared, "tv", tmdb_id, name, user, now)
            _save_auto_series_cache(self._auto_series_shared)
            self._push_auto_series_to_ftp(
                lambda data: add_auto_owner(data, "tv", tmdb_id, name, user, now))
        else:
            self._auto_series_shared = remove_auto_owner(
                self._auto_series_shared, "tv", tmdb_id, user)
            _save_auto_series_cache(self._auto_series_shared)
            self._push_auto_series_to_ftp(
                lambda data: remove_auto_owner(data, "tv", tmdb_id, user))
        self._refresh_missing_ep_auto_button(tmdb_id, new_state)
        if not new_state:
            # Quitar la cuenta atrás del detalle de la serie si estaba puesta
            # (desactivar el autocompletado la deja sin sentido).
            self._forget_auto_countdown(tmdb_id)
            # Si el rayo se quitó enseguida por error (1 s), cancelar
            # cualquier descarga que la pasada inmediata ya hubiera lanzado
            # para esa serie y limpiar checked/reintentos recientes (60 s) —
            # si no, Los Simpson intentaría descargar cientos de capítulos
            # durante horas con backoff.
            try:
                now = _time.time()
                grace = 90  # segundos: clic por error
                # Cancelar partfiles recientes en aMule
                umap = self._unstuck_map().get(str(tmdb_id), {})
                for s_str, eps in list(umap.items()):
                    for ep_str, rec in list(eps.items()):
                        started = float(rec.get("primary_started_ts", 0) or rec.get("started_ts", 0) or 0)
                        if started and (now - started) <= grace:
                            for hk in (rec.get("primary_hash"), rec.get("alt_hash")):
                                if hk:
                                    try:
                                        with self._amule_ec_lock:
                                            ec = EcClient(host=self.config_data.get("amule_host", "localhost"),
                                                          port=self.config_data.get("amule_port", 4712),
                                                          password=self.config_data.get("amule_password", ""),
                                                          timeout=6.0)
                                            ec.connect()
                                            ec.cancel_download(hk)
                                            ec.close()
                                        _log.info("Autocompletado: cancelada descarga reciente %s %sx%s (rayo quitado)", tmdb_id, s_str, ep_str)
                                    except Exception:
                                        pass
                # Limpiar checked recientes (solo los de esta racha por error)
                cmap = self._auto_checked_map()
                changed_c = False
                if str(tmdb_id) in cmap:
                    for s_str, eps in list(cmap[str(tmdb_id)].items()):
                        eps = dict(eps) if isinstance(eps, dict) else {}
                        for ep_str, ts in list(eps.items()):
                            try:
                                if isinstance(ts, (int, float)) and (now - float(ts)) <= grace:
                                    # es dict {season:{ep:epoch}} — borrar solo ese ep
                                    if auto_complete_state.unset_checked(cmap, tmdb_id, int(s_str), int(ep_str)):
                                        changed_c = True
                            except Exception:
                                pass
                    if changed_c:
                        self.config_data.set("missing_ep_auto_checked", cmap)
                # Limpiar reintentos recientes de esta serie (si acababan de fallar)
                rmap = self._auto_retry_map()
                if str(tmdb_id) in rmap:
                    changed_r = False
                    for s_str, eps in list(rmap[str(tmdb_id)].items()):
                        for ep_str, rec2 in list(eps.items()):
                            try:
                                if (now - float(rec2.get("ts", 0))) <= grace:
                                    eps.pop(ep_str, None)
                                    changed_r = True
                            except Exception:
                                pass
                        if not eps:
                            rmap[str(tmdb_id)].pop(s_str, None)
                    if changed_r:
                        if not rmap[str(tmdb_id)]:
                            rmap.pop(str(tmdb_id), None)
                        self.config_data.set("missing_ep_auto_retries", rmap)
                # Limpiar unstuck de la serie
                if str(tmdb_id) in self._unstuck_map():
                    m = self._unstuck_map()
                    m.pop(str(tmdb_id), None)
                    self.config_data.set("missing_ep_auto_downloads", m)
                self.config_data.save()
            except Exception:
                _log.exception("Autocompletado: fallo limpiando serie %s tras quitar rayo", tmdb_id)
        self._set_status(
            f"Autocompletado {'activado' if new_state else 'desactivado'} para "
            f"{name or 'la serie'}",
            SUCCESS_COLOR if new_state else PENDING_COLOR)
        if new_state:
            threading.Thread(target=self._auto_complete_pass, daemon=True).start()

    def _force_search_missing_series(self, tmdb_id: int):
        """Fuerza búsqueda de los capítulos que faltan de una serie con ⚡ activo:
        ignora espera de reintento y grace 24h solo de los capítulos forzados,
        y si hay capítulos descargando activa Unstuck (busca alternativas).
        Si el rayo no está activo para Slime u otra, se permite forzar igualmente
        (avisa) para no bloquear el rescate tras borrar italianos."""
        is_auto = str(tmdb_id) in self._auto_complete_series()
        if not is_auto:
            self._set_status("Rayo no activo para esta serie — forzando igualmente…", WARNING_COLOR)
        else:
            self._set_status(f"Forzando búsqueda para serie {tmdb_id}…", PENDING_COLOR)
        self._set_status(f"Forzando búsqueda para serie {tmdb_id}…", PENDING_COLOR)
        def _worker():
            try:
                # Reusar la pasada individual pero limpiando grace/backoff de los faltantes
                row = next((r for r in self._missing_ep_results if r.get("tmdb_id") == tmdb_id), None)
                if row is None:
                    from core.missing_episodes_cache import load_cache
                    cached = dict(load_cache()).get(str(tmdb_id))
                    if cached:
                        row = {"tmdb_id": tmdb_id, "name": cached.get("name", ""), "source": cached.get("source"),
                               "server_id": cached.get("server_id"), "folder_name": cached.get("folder_name")}
                if row is None:
                    self.after(0, lambda: self._set_status("Serie no encontrada en caché", WARNING_COLOR))
                    return
                # Obtener fresh faltantes (ya cruza FTP)
                try:
                    fresh_results, _ = self._rescan_single_series_worker(row)
                except Exception as e:
                    # El mensaje se captura POR VALOR: al salir del except,
                    # Python borra `e`, y la lambda se ejecuta después.
                    self.after(0, lambda msg=str(e): self._set_status(
                        f"Rescan falló: {msg}", ERROR_COLOR))
                    return
                if not fresh_results:
                    self.after(0, lambda: self._set_status("Serie ya completa, nada que forzar", SUCCESS_COLOR))
                    return
                fresh = fresh_results[0]
                # Limpiar grace/backoff solo de los que faltan (no toda la serie)
                for season, eps in fresh.get("missing", {}).items():
                    for ep in eps:
                        self._unset_auto_checked_episode(tmdb_id, int(season), int(ep))
                        self._clear_auto_retry_episode(tmdb_id, int(season), int(ep))
                        # Resetear last_alt_ts para que Unstuck pueda lanzar alternativa enseguida
                        rec = self._unstuck_get_rec(tmdb_id, int(season), int(ep))
                        if rec:
                            rec.pop("last_alt_ts", None)
                            self._unstuck_set_rec(tmdb_id, int(season), int(ep), rec)
                self.config_data.save()
                # Lanzar pasada forzada (reusa lógica con unstuck ya cableado)
                self._auto_complete_series_single(tmdb_id, force=True)
                self.after(0, lambda: self._set_status(f"Búsqueda forzada lanzada para {fresh.get('name', tmdb_id)}", SUCCESS_COLOR))
            except Exception as e:
                _log.exception("Forzar búsqueda falló para %s", tmdb_id)
                self.after(0, lambda msg=str(e): self._set_status(
                    f"Forzar falló: {msg}", ERROR_COLOR))
        threading.Thread(target=_worker, daemon=True).start()

    def _start_missing_ep_auto_worker(self):
        """Hilo daemon que recorre las series con autocompletado: arranca al
        abrir la app (tras un pequeño retardo) y luego cada 30 min, buscando
        y descargando capítulos nuevos que hayan salido."""
        def _loop():
            _time.sleep(self._AUTO_COMPLETE_STARTUP_DELAY)
            while True:
                try:
                    self._auto_complete_pass()
                except Exception:
                    _log.exception("Autocompletado: fallo en la pasada programada")
                _time.sleep(self._AUTO_COMPLETE_INTERVAL)
        if self._missing_ep_auto_worker is None or not self._missing_ep_auto_worker.is_alive():
            self._missing_ep_auto_worker = threading.Thread(target=_loop, daemon=True)
            self._missing_ep_auto_worker.start()

    def _auto_complete_pass(self):
        """Una pasada de autocompletado: para cada serie activa, revisa los
        capítulos que faltan la mar casilla y descarga los nuevos."""
        series = sorted(self._auto_complete_series(), key=int)
        if not series:
            return
        _log.info("Autocompletado: pasada sobre %d serie(s)", len(series))
        for tmdb_id_str in series:
            # Si el usuario quitó el rayo mientras la pasada ya estaba en curso
            # (p. ej. clic por error en Los Simpson y 1 s después lo quita), no
            # procesar esa serie — evita lanzar cientos de descargas durante horas.
            if tmdb_id_str not in self._auto_complete_series():
                _log.info("Autocompletado: serie %s ya no está en auto, se omite (rayo quitado)", tmdb_id_str)
                continue
            try:
                self._auto_complete_series_single(int(tmdb_id_str))
            except Exception:
                _log.exception("Autocompletado: fallo en serie %s", tmdb_id_str)

    def _auto_complete_series_single(self, tmdb_id: int, force: bool = False):
        """Descarga los capítulos pendientes de UNA serie con autocompletado.
        Primero obtiene la lista fresca de faltantes (rescan de esa serie
        sola, que ya cruza contra el FTP y quita lo que ya está en el
        servidor), después, para cada capítulo que falte y no esté ya
        probado ni ya en el servidor (upload_history), busca en aMule y lo
        descarga. Ver _AUTO_* y el comentario del bloque de arriba."""
        # Guardia anti-duplicados: tanto el hilo programado (cada 30 min)
        # como el arranque inmediato al pulsar el botón "⚡" pueden llamar a
        # esto a la vez para la misma serie; si ya se está procesando, esta
        # llamada se descarta (la otra pasada ya revisará este capítulo).
        busy = getattr(self, "_missing_ep_auto_busy", None)
        if busy is None:
            busy = self._missing_ep_auto_busy = set()
        if tmdb_id in busy:
            return
        busy.add(tmdb_id)
        try:
            self._auto_complete_series_single_impl(tmdb_id, force=force)
        finally:
            busy.discard(tmdb_id)

    def _tmdb_expected_episodes(self, tmdb_id: int) -> dict:
        """Episodios YA EMITIDOS de todas las temporadas de una serie según
        TMDB -- {temporada: [números]} sin incluir especiales (temporada 0).
        Usado por el autocompletado de las series RECOMENDADAS que aún no
        están en el servidor (ver _auto_complete_series_single_impl): como
        no hay servidor con el que cruzar huecos, "faltan" todos los
        emitidos. Misma lógica de temporadas que _rescan_single_series_worker
        pero sin el cruce con lo que ya hay."""
        details = self.tmdb.get_tv_details(tmdb_id)
        expected = {}
        for season in details.get("seasons", []):
            n = season.get("season_number", 0)
            if n <= 0:   # temporada 0 = especiales, no cuenta
                continue
            try:
                eps = self.tmdb.get_season_episodes(tmdb_id, n)
            except Exception:
                continue
            nums = [e["episode_number"] for e in eps if e.get("episode_number")]
            if nums:
                expected[n] = nums
        return expected

    def _auto_complete_series_single_impl(self, tmdb_id: int, force: bool = False):
        # Fila actual si ya está cachada; sin ella no se puede reescanear
        # esa serie sola (se necesita source/server_id).
        row = next((r for r in self._missing_ep_results if r.get("tmdb_id") == tmdb_id), None)
        if row is None:
            from core.missing_episodes_cache import load_cache
            cached = dict(load_cache()).get(str(tmdb_id))
            if not cached:
                row = None
            else:
                row = {
                    "tmdb_id": tmdb_id,
                    "name": cached.get("name", ""),
                    "source": cached.get("source"),
                    "server_id": cached.get("server_id"),
                    "folder_name": cached.get("folder_name"),
                    "ignored": cached.get("ignored", False),
                    "ignored_seasons": set(cached.get("ignored_seasons") or []),
                    "ignored_episodes": {s: set(eps) for s, eps in (cached.get("ignored_episodes") or {}).items()},
                    "episode_titles": {},
                    "play_count": cached.get("play_count", 0),
                    "last_played_ts": cached.get("last_played_ts"),
                    "ai_verdict": cached.get("ai_verdict"),
                }
        if row is not None and not (row.get("source") and row.get("server_id")):
            # Fila de antes de que estos campos se guardaran en caché -- sin
            # ellos no hay cómo reescanear contra el servidor; si además la
            # serie está en la pestaña Recomendado (o es la fila misma), se
            # intenta igualmente como serie nueva más abajo.
            row = None

        if row is not None:
            # Lista fresca de faltantes YA cruzada contra el FTP (los que ya
            # están en el servidor salen de "missing" -- ver _rescan_single_series_worker).
            try:
                fresh_results, _removed = self._rescan_single_series_worker(row)
            except Exception:
                _log.exception("Autocompletado: reescaneo de '%s' (tmdb_id=%s) falló", row["name"], tmdb_id)
                return
            if not fresh_results:
                _log.info("Autocompletado: '%s' ya no tiene huecos", row["name"])
                return
            fresh = fresh_results[0]
            series_name = fresh.get("name", row["name"])
        else:
            # Serie sin fila en el detector de huecos -- puede ser una serie
            # RECOMENDADA de la pestaña "Recomendado" que no está en el
            # servidor todavía. Para ella "lo que falta" es TODO lo emitido
            # según TMDB (no hay servidor con el que cruzar): se construye el
            # "fresh" directamente de TMDB y se sigue el mismo flujo de
            # descarga (checked/backoff/upload_history).
            rec = next((r for r in (getattr(self, "_movies_results", None) or [])
                        if r.get("media_type") == "tv" and r.get("tmdb_id") == tmdb_id), None)
            if rec is None:
                _log.info("Autocompletado: serie %s sin fila (ni en servidor ni recomendada), se omite", tmdb_id)
                return
            try:
                expected = self._tmdb_expected_episodes(tmdb_id)
            except Exception:
                _log.exception("Autocompletado: no se pudieron pedir los episodios de la serie recomendada %s", tmdb_id)
                return
            if not expected:
                _log.info("Autocompletado: '%s' sin episodios emitidos (o sin datos de TMDB)", rec.get("title"))
                return
            series_name = rec.get("title", "")
            # Cruce FTP también para recomendadas: si ya está en el FTP (copiada a mano,
            # o subida previa sin pasar por Jellyfin/Plex) no debe re-descargarse entera.
            # Antes se construía fresh = {missing: expected} sin cruce y X-Men '97
            # se re-descargaba completa aunque ya estuviera en /datos2/series/X-Men '97/.
            tmp = {"name": series_name, "expected_episodes": expected, "missing": dict(expected),
                   "folder_name": rec.get("folder_name"), "tmdb_id": tmdb_id}
            try:
                tmp = self._cross_check_single_result_with_ftp(tmp)
            except Exception:
                _log.exception("Autocompletado: cruce FTP para recomendada '%s' falló, se usa missing sin filtrar", series_name)
            fresh_missing = tmp.get("missing", expected)
            if not fresh_missing:
                _log.info("Autocompletado: '%s' ya completa en FTP, no se descarga", series_name)
                return
            fresh = {"name": series_name, "missing": fresh_missing}

        checked = (self._auto_checked_map().get(str(tmdb_id)) or {})
        retries = (self._auto_retry_map().get(str(tmdb_id)) or {})
        now = _time.time()
        # Pre-cargar cola aMule para status y para decidir si un checked reciente (italiano borrado) debe liberarse ya
        _q_cache_early: dict[str, dict] = {}
        try:
            with self._amule_ec_lock:
                ec_q2 = EcClient(host=self.config_data.get("amule_host", "localhost"),
                                 port=self.config_data.get("amule_port", 4712),
                                 password=self.config_data.get("amule_password", ""),
                                 timeout=5.0)
                ec_q2.connect()
                for it in ec_q2.get_download_queue():
                    _q_cache_early[it["hash_hex"]] = it
                ec_q2.close()
        except Exception:
            _q_cache_early = {}

        # Header de capítulos faltantes que de verdad hay que intentar.
        pending = []
        for season in sorted(fresh.get("missing", {})):
            for ep in sorted(fresh["missing"][season]):
                # El episodio está marcado como resuelto pero SIGUE faltando en
                # el servidor. Hay que distinguir dos casos, y no hacerlo era
                # el bucle de relanzamiento (ver _AUTO_LAUNCHED_GRACE_S): antes
                # se desmarcaba siempre, así que el "checked" que escribe el
                # éxito de la descarga lo borraba la pasada siguiente -- 30 min
                # después -- y se volvía a lanzar la misma descarga.
                # Toda la decisión (¿ya se lanzó y sigue descargándose? ¿falló y
                # aún no toca reintentarlo?) está en core/auto_complete_state.py.
                action, drop_checked = auto_complete_state.decide_episode(
                    checked, retries, season, ep, now)
                if action == auto_complete_state.WAIT_DOWNLOAD:
                    try:
                        rec_ep = self._unstuck_get_rec(tmdb_id, season, ep)
                        in_q = bool(rec_ep and (rec_ep.get("primary_hash") in _q_cache_early or rec_ep.get("alt_hash") in _q_cache_early))
                        if not in_q:
                            # No está en cola -> se borró (ej. Slime italiano) -> forzar reintento con filtro actual
                            drop_checked = True
                            self._unset_auto_checked_episode(tmdb_id, season, ep)
                            action = auto_complete_state.ATTEMPT
                            _log.info("Autocompletado: forzado reintento de %s %dx%02d tras borrado (no en cola)", series_name, season, ep)
                    except Exception:
                        pass
                if drop_checked:
                    self._unset_auto_checked_episode(tmdb_id, season, ep)
                if action == auto_complete_state.ATTEMPT:
                    pending.append((season, ep))

        # Con "Ocultar sin doblaje ES" activo, el autocompletado NO descarga
        # capítulos sin doblaje CONFIRMADO (real: Dragon Ball Daima descargaba
        # capítulos aún sin doblar aunque la tabla los ocultaba). A diferencia
        # de la tabla -- visible por defecto mientras se comprueba -- aquí lo
        # no comprobado bloquea: mejor faltante que V.O.S. descargada sola.
        # Precedencia igual que el aviso manual: IA, si no corte de eldoblaje
        # en caché, si no veredictos por episodio de TMDB (ver
        # core/missing_episodes.py::is_ep_dub_confirmed).
        to_download = list(pending)
        try:
            _hide_no_dub = bool(self._hide_no_dub_enabled())
        except Exception:
            _hide_no_dub = False
        if _hide_no_dub and pending:
            try:
                from core.missing_episodes import is_ep_dub_confirmed
                _ai = ((row or {}).get("ai_verdict") or {}) if "row" in locals() else {}
                _cutoff = self._dub_cutoff_for_series(tmdb_id, _ai)
                _cache_entry = (self._spanish_dub_cache or {}).get(str(tmdb_id), {})
                _dub_eps = _cache_entry.get("episodes") or {}
                _blocked = [(s, e) for (s, e) in pending
                            if not is_ep_dub_confirmed(s, e, _dub_eps, _cutoff)]
                if _blocked:
                    _log.info("Autocompletado: '%s' %d capítulo(s) sin doblaje confirmado, no se descargan (%s)",
                              series_name, len(_blocked),
                              ", ".join(f"{s}x{e:02d}" for s, e in _blocked[:8]))
                to_download = [(s, e) for (s, e) in pending
                               if is_ep_dub_confirmed(s, e, _dub_eps, _cutoff)]
            except Exception:
                _log.exception("Autocompletado: filtro de doblaje falló para '%s', se sigue sin filtrar", series_name)
                to_download = list(pending)

        if not to_download:
            try:
                _msg = (f"{series_name}: {len(pending)} en espera de doblaje ES"
                        if pending else f"{series_name}: sin huecos nuevos")
                self.after(0, lambda m=_msg: self._set_status(m, SUCCESS_COLOR))
            except Exception:
                pass
            return
        try:
            self.after(0, lambda n=series_name, c=len(to_download): self._set_status(f"Buscando {c} capítulo(s) de {n} en aMule…", PENDING_COLOR))
        except Exception:
            pass

        # ── Unstuck: pre-cargar cola aMule una vez por pasada para medir avance ──
        _q_cache: dict[str, dict] = {}
        if self.config_data.get("unstuck_enabled"):
            try:
                with self._amule_ec_lock:
                    ec_q = EcClient(host=self.config_data.get("amule_host", "localhost"),
                                    port=self.config_data.get("amule_port", 4712),
                                    password=self.config_data.get("amule_password", ""),
                                    timeout=6.0)
                    ec_q.connect()
                    for it in ec_q.get_download_queue():
                        _q_cache[it["hash_hex"]] = it
                    ec_q.close()
            except Exception:
                _q_cache = {}

        # Tamaño típico por temporada (se calcula por episodio en el bucle, con caché de 10 min)
        _log.info("Autocompletado: '%s' -> %d capítulo(s) nuevos por descargar",
                  series_name, len(to_download))
        for season, episode in to_download:
            try:
                self.after(0, lambda n=series_name, s=season, e=episode: self._set_status(f"Autocompletado {n} {s}x{int(e):02d}: buscando en aMule…", PENDING_COLOR))
            except Exception:
                pass
            if not force and str(tmdb_id) not in self._auto_complete_series():
                _log.info("Autocompletado: serie %s deshabilitada durante la tanda, abortando", tmdb_id)
                break
            # ── Unstuck: si la descarga primaria está atascada (tiempo+avance) → alternativa
            if self.config_data.get("unstuck_enabled"):
                from core import unstuck as _usk
                rec = self._unstuck_get_rec(tmdb_id, season, episode)
                if rec and rec.get("primary_hash"):
                    # Construir queue_info para should_try_alternative
                    ph = rec.get("primary_hash", "")
                    ah = rec.get("alt_hash", "")
                    q_primary = _q_cache.get(ph) if ph else None
                    q_alt = _q_cache.get(ah) if ah else None
                    # Actualizar bytes si hay cola real
                    now2 = _time.time()
                    if q_primary and q_primary.get("size_done") is not None:
                        rec = _usk.mark_progress(rec, "primary", int(q_primary["size_done"]), now2)
                    if q_alt and q_alt.get("size_done") is not None:
                        rec = _usk.mark_progress(rec, "alt", int(q_alt["size_done"]), now2)
                    # Comprobar si alguna completó al 100% → borrar la otra (bidireccional)
                    # percent 100 o desaparición de cola + episodio ya no en missing se maneja abajo
                    for which in ("primary", "alt"):
                        qi = q_primary if which == "primary" else q_alt
                        if qi and qi.get("percent", 0) >= 99.5:
                            other = "alt" if which == "primary" else "primary"
                            other_hash = rec.get(f"{other}_hash")
                            if other_hash and other_hash in _q_cache:
                                try:
                                    with self._amule_ec_lock:
                                        ec2 = EcClient(host=self.config_data.get("amule_host", "localhost"),
                                                       port=self.config_data.get("amule_port", 4712),
                                                       password=self.config_data.get("amule_password", ""),
                                                       timeout=6.0)
                                        ec2.connect()
                                        ec2.cancel_download(other_hash)
                                        ec2.close()
                                    _log.info("Unstuck: %s completó (%.1f%%), borrada la otra %s para %s %dx%02d",
                                              which, qi["percent"], other, series_name, season, episode)
                                except Exception:
                                    pass
                            # Limpiar rec alt si ya completó primaria (o viceversa)
                            if which == "primary" and rec.get("alt_hash"):
                                rec.pop("alt_hash", None); rec.pop("alt_started_ts", None)
                            if which == "alt" and rec.get("primary_hash"):
                                # alt completó → borrar primaria, promoción a primaria
                                try:
                                    with self._amule_ec_lock:
                                        ec2 = EcClient(host=self.config_data.get("amule_host", "localhost"),
                                                       port=self.config_data.get("amule_port", 4712),
                                                       password=self.config_data.get("amule_password", ""),
                                                       timeout=6.0)
                                        ec2.connect()
                                        ec2.cancel_download(ph)
                                        ec2.close()
                                except Exception:
                                    pass
                                # alt pasa a ser la primaria efectiva
                                rec["primary_hash"] = rec.pop("alt_hash")
                                rec["primary_started_ts"] = rec.pop("alt_started_ts", now2)
                                rec["primary_last_bytes"] = rec.pop("alt_last_bytes", 0)
                                rec["primary_last_progress_ts"] = rec.pop("alt_last_progress_ts", now2)
                            self._unstuck_set_rec(tmdb_id, season, episode, rec)
                            # No lanzar otra descarga este ciclo, ya hay una completa
                            continue
                    # ¿Atascado? → lanzar alternativa con backoff creciente
                    cfg_u = {"unstuck_enabled": True,
                             "unstuck_backoff_base_minutes": self.config_data.get("unstuck_backoff_base_minutes", 30),
                             "unstuck_backoff_max_minutes": self.config_data.get("unstuck_backoff_max_minutes", 480),
                             "unstuck_max_retries": self.config_data.get("unstuck_max_retries", 5),
                             "unstuck_file_ttl_minutes": self.config_data.get("unstuck_file_ttl_minutes", 1440)}
                    qi = {"primary": q_primary, "alt": q_alt}
                    if _usk.should_try_alternative(rec, qi, _time.time(), cfg_u):
                        # Alternativa: mismo template pero search_type distinto (Kad↔Global) para variedad
                        orig_st = self.config_data.get("amule_search_type", "Kad")
                        alt_st = "Global" if (orig_st or "").lower() == "kad" else "Kad"
                        alt_query = _usk.next_alternative_query(series_name, season, episode,
                                                                 self.config_data.get("series_search_patterns", {}) or {})
                        try:
                            _typ_alt = self._typical_size_for_series(series_name, season)
                        except Exception:
                            _typ_alt = None
                        ok2, why2, h2 = self._auto_amule_download_series(alt_query, search_type=alt_st, typical_size=_typ_alt)
                        rec["stuck_tries"] = int(rec.get("stuck_tries", 0) or 0) + 1
                        rec["last_alt_ts"] = _time.time()
                        if ok2 and h2:
                            rec["alt_hash"] = h2
                            rec["alt_started_ts"] = _time.time()
                            rec["alt_last_bytes"] = 0
                            rec["alt_last_progress_ts"] = _time.time()
                            rec["alt_query"] = alt_query
                            _log.info("Unstuck: alternativa lanzada para %s %dx%02d → '%s' (hash %s, intento %d)",
                                      series_name, season, episode, alt_query, h2[:8], rec["stuck_tries"])
                        else:
                            _log.info("Unstuck: alternativa no encontrada para %s %dx%02d (%s)", series_name, season, episode, why2)
                        self._unstuck_set_rec(tmdb_id, season, episode, rec)
                        continue
                    # Si no toca alternativa y sigue atascado pero en backoff, saltar este episodio
                    if rec.get("primary_hash") and _q_cache.get(rec["primary_hash"]) is not None:
                        # Hay primaria y no toca alternativa → no relanzar primaria
                        continue
            # ── Fin Unstuck

            # Igual que el botón manual: se busca SIN el título del episodio
            # (incluirlo recorta los resultados de aMule -- ver
            # _build_missing_ep_season_episode_rows). best_result ya exige
            # temporada×episodio, el título no hace falta.
            query = build_amule_query(series_name, season, episode,
                                      templates=self.config_data.get("series_search_patterns", {}) or {},
                                      prefers_castellano=self._series_prefers_castellano(series_name))
            try:
                _typ_ep = self._typical_size_for_series(series_name, season)
            except Exception:
                _typ_ep = None
            ok, why, h = self._auto_amule_download_series(query, typical_size=_typ_ep)
            if ok:
                # Éxito: queda "checked" (no se vuelve a reintentar) y se
                # borra cualquier reintento previo. Además se registra en
                # Unstuck como descarga primaria para medir tiempo+avance.
                self._set_auto_checked_episode(tmdb_id, season, episode)
                self._clear_auto_retry_episode(tmdb_id, season, episode)
                if self.config_data.get("unstuck_enabled") and h:
                    rec = self._unstuck_get_rec(tmdb_id, season, episode) or {}
                    rec["primary_hash"] = h
                    rec["primary_started_ts"] = _time.time()
                    rec["primary_last_bytes"] = 0
                    rec["primary_last_progress_ts"] = _time.time()
                    rec["primary_query"] = query
                    rec.setdefault("stuck_tries", 0)
                    self._unstuck_set_rec(tmdb_id, season, episode, rec)
                _log.info("Autocompletado: descarga lanzada para '%s'", query)
                try:
                    self.after(0, lambda n=series_name, s=season, e=episode: self._set_status(f"{n} {s}x{int(e):02d} → descarga lanzada", SUCCESS_COLOR))
                except Exception:
                    pass
            else:
                if "ya en completados" in (why or "").lower():
                    _log.info("Autocompletado: '%s' ya en completados de aMule, no se relanza", query)
                    try:
                        self.after(0, lambda n=series_name, s=season, e=episode: self._set_status(f"{n} {s}x{int(e):02d} ya en completados de aMule (no se vuelve a bajar)", "#F39C12"))
                    except Exception:
                        pass
                    # No marcar checked ni retry: ya está en disco, el autowatcher lo subirá cuando lo detecte estable
                    continue
                # Fallo (sin candidato, aMule caído...): NO se marca checked.
                # Se registra en el mapa de reintentos con backoff exponencial
                # para volver a probarlo en pasadas futuras, cada vez más
                # espaciado (ver _AUTO_RETRY_*).
                self._set_auto_retry_episode(tmdb_id, season, episode)
                _log.info("Autocompletado: '%s' no se pudo descargar (%s)", query, why)
                try:
                    self.after(0, lambda n=series_name, s=season, e=episode, w=why: self._set_status(f"{n} {s}x{int(e):02d} sin candidato ({w})", WARNING_COLOR))
                except Exception:
                    pass

        # ── Unstuck: limpiar recs de episodios ya completados (ya no faltan) → borrar la descarga que quedó colgada
        if self.config_data.get("unstuck_enabled"):
            try:
                fresh_missing = fresh.get("missing", {}) if 'fresh' in locals() else {}
                still_missing = {(s, ep) for s, eps in fresh_missing.items() for ep in eps}
                umap = self._unstuck_map().get(str(tmdb_id), {})
                for s_str, eps in list(umap.items()):
                    for ep_str, rec in list(eps.items()):
                        key = (int(s_str), int(ep_str))
                        if key not in still_missing:
                            # Episodio ya no falta → una de las dos descargas completó y el archivo ya está en FTP
                            # Borrar la que siga en cola (si queda) y limpiar rec
                            for hk in (rec.get("primary_hash"), rec.get("alt_hash")):
                                if hk and hk in _q_cache:
                                    try:
                                        with self._amule_ec_lock:
                                            ec3 = EcClient(host=self.config_data.get("amule_host", "localhost"),
                                                           port=self.config_data.get("amule_port", 4712),
                                                           password=self.config_data.get("amule_password", ""),
                                                           timeout=6.0)
                                            ec3.connect()
                                            ec3.cancel_download(hk)
                                            ec3.close()
                                        _log.info("Unstuck: episodio %dx%02d ya completo, borrada descarga residual %s", key[0], key[1], hk[:8])
                                    except Exception:
                                        pass
                            self._unstuck_clear_rec(tmdb_id, int(s_str), int(ep_str))
            except Exception:
                pass

    def _auto_episode_in_history(self, history_remote_paths: set, series_name: str,
                                 season: int, episode: int) -> bool:
        """True si *upload_history* ya registra subido este capítulo (serie,
        temporada × ep) en alguna ruta remota -- advertencia local barata
        para no lanzar una descarga de algo que ya está en el servidor."""
        # Normalización de los dos lados antes de comparar: el nombre de la
        # serie tal cual lo da TMDB puede llevar puntuación que la ruta
        # remota real no tiene ("Prodigiosa: Las aventuras de Ladybug" con
        # dos puntos vs ".../Prodigiosa Las aventuras de Ladybug/..." sin
        # ellos), y una comparación literal de subcadena fallaba SIEMPRE
        # para esa serie -- el autocompletado re-descargaba capítulos ya
        # subidos una y otra vez aunque el cruce FTP (que sí normaliza, ver
        # series_similarity) los encontrara en el servidor.
        norm_series = normalize_series_name(series_name) if series_name else ""
        if not norm_series:
            return False
        for remote in history_remote_paths:
            det = detect_episode(remote)
            if (det.get("season") == season and det.get("episode") == episode):
                if norm_series in normalize_series_name(remote):
                    return True
        return False

    def _series_prefers_castellano(self, series_name: str) -> bool:
        """Si algún archivo de la lista para esa serie contiene 'castellano' en el nombre,
        la búsqueda aMule debe priorizarlo (ver build_amule_query prefers_castellano).
        Si hay un template personalizado para la serie, se respeta tal cual y no se
        añade 'castellano' automáticamente (el usuario ya controla la query)."""
        # Template personalizado → no añadir castellano automáticamente
        pats = self.config_data.get("series_search_patterns", {}) or {}
        low = (series_name or "").strip().lower()
        for k in pats:
            if k.strip().lower() == low:
                return False
        low_series = low
        for e in getattr(self, "files", []):
            # Identidad de serie: media_info.title o detected title
            e_series = ""
            if getattr(e, "media_info", None) and getattr(e.media_info, "title", ""):
                e_series = e.media_info.title
            elif getattr(e, "detected", None):
                e_series = (e.detected or {}).get("title", "")
            if e_series.strip().lower() == low_series:
                if "castellano" in (getattr(e, "name", "") or "").lower():
                    return True
        # Fallback: si el propio series_name ya trae castellano (template con castellano), no duplicar
        return False

    def _auto_amule_download_series(self, query: str, search_type: str | None = None, typical_size: int | None = None,
                                        max_size: int | None = None, is_movie: bool = False,
                                        expected_year: int | None = None):
        """Busca *query* en aMule y descarga el mejor candidato (mismo
        criterio best_result que el botón manual). Devuelve (ok, motivo, hash_hex).
        hash_hex es el MD4 hex del partfile descargado (o "" si falla). No
        toca la sesión/pestaña Descargas (crea la suya y la cierra). *max_size*
        (uso de "Adelgazar"): techo en bytes -- se reenvía a best_result para
        no descargar algo igual o más pesado que lo que ya hay."""
        try:
            self.after(0, lambda q=query: self._set_status(f"Buscando '{q[:60]}' en aMule…", PENDING_COLOR))
        except Exception:
            pass
        from core.amule_download import auto_download
        ok, why, h, _name = auto_download(self.config_data, self._amule_ec_lock, query, search_type=search_type,
                                          typical_size=typical_size, max_size=max_size,
                                          is_movie=is_movie, expected_year=expected_year)
        return ok, why, h

    def _typical_size_for_series(self, series_name: str, season: int | None = None) -> int | None:
        """Tamaño típico de episodio ya en el servidor para *series_name* (solo FTP, sin historial).

        Usa la temporada pedida si tiene >=3 ficheros (>10MB); si no, acumula las más recientes por
        número hasta >=6 ficheros. Así no se mezclan calidades distintas (T01 SD 175MB con T03 HD
        400-600MB): mezclarlas daba 175MB de típico para descargas HD. Sobre los tamaños elegidos se
        usa _typical_from_sizes (mayoría estricta de bucket, si no mediana global). Resultado con
        caché de 10 min por (serie, temporada). Registra en app.log."""
        from core.amule_download import typical_size_for_series
        return typical_size_for_series(self.config_data, series_name, season)

    def _remove_series_from_missing_episodes(self, tmdb_id: int):
        """Quita una serie ENTERA de "Episodios que faltan" -- mismo
        mecanismo que _remove_uploaded_from_missing_episodes (quitar de
        self._missing_ep_results + del caché en disco + redibujar sin
        resetear la página), pero para la fila completa en vez de un solo
        episodio. Usado tanto al borrar la serie desde el botón de esta
        misma pantalla (ver _finish_delete_missing_ep_series) como al
        borrarla desde Liberar espacio (ver _finish_delete_cleanup_item):
        en ambos casos la serie ya no está en el servidor, así que no
        tiene sentido seguir listándola como "con episodios pendientes".
        Debe llamarse desde el hilo de la GUI."""
        from core.missing_episodes import remove_series
        removed = remove_series(self._missing_ep_results, tmdb_id)
        # Con "Ocultar completas" apagado, la serie borrada puede ser una
        # sin huecos: esa vive en la otra lista, y aun así hay que sacarla
        # de la caché en disco (si no, reaparecería en el próximo repintado
        # aunque ya no esté en el servidor).
        if self._missing_ep_complete_rows is not None:
            if remove_series(self._missing_ep_complete_rows, tmdb_id):
                removed = True
        if not removed:
            return

        from core.missing_episodes_cache import load_cache, save_cache, remove_series_from_cache
        cache = load_cache()
        if remove_series_from_cache(cache, tmdb_id):
            save_cache(cache)
            self._push_missing_episodes_to_ftp()

        # La serie ya no está en el servidor -- el autocompletado no tiene
        # nada que completar, y seguir activo solo haría que el worker
        # (cada 30 min) la tratara como "serie nueva" y se descargara
        # entera otra vez si todavía está en Recomendado (ver
        # _auto_complete_series_single_impl). Se desactiva igual que el
        # interruptor "⚡" (ver _toggle_missing_ep_auto_complete).
        key = str(tmdb_id)
        auto_series = self._auto_complete_series()
        if key in auto_series:
            auto_series.discard(key)
            self.config_data.set("missing_ep_auto_complete", sorted(auto_series))
            since = self._auto_since_map()
            if since.pop(key, None) is not None:
                self.config_data.set("missing_ep_auto_since", since)
            self.config_data.save()
            # Mi Ownership compartido también sobra: la serie ya no está y
            # este equipo no la va a completar (ver _toggle_missing_ep_auto_complete).
            me = self.config_data.get("app_user_name", "").strip()
            if me:
                from core.auto_series import (
                    remove_auto_owner, save_local_cache as _save_auto_series_cache)
                self._auto_series_shared = remove_auto_owner(
                    self._auto_series_shared, "tv", tmdb_id, me)
                _save_auto_series_cache(self._auto_series_shared)
                self._push_auto_series_to_ftp(
                    lambda data: remove_auto_owner(data, "tv", tmdb_id, me))
            # Quitar la cuenta atrás del detalle de la serie si estaba puesta.
            self._forget_auto_countdown(tmdb_id)

        self._render_missing_episodes_table(reset_page=False)
