"""
Solicitudes de descarga de la web móvil (solicitudes-web/, ver
core/download_requests.py): el hilo que cada pocos minutos reclama las
pendientes, las lanza en aMule con el mismo embudo que el autocompletado,
comprueba si ya llegaron al servidor y avisa a la web.

Mixin sin interfaz que hereda QtAppCore (gui_qt/core_host.py) (usa lo de core/app_auto_complete.py y
core/app_files_core.py).
"""

import threading
import time as _time

from core.applog import get_logger
from core.ec_client import EcClient

_log = get_logger("aIBechos.gui", "app.log")


class DownloadRequestsMixin:
    # Constantes de clase (antes en App, gui/app.py)
    _DOWNLOAD_REQUESTS_STARTUP_DELAY = 120
    _DOWNLOAD_REQUESTS_EXPAND_CAP = 50

    def _start_download_requests_worker(self):
        """Hilo daemon que atiende aIBechos_solicitudes.json: arranca con
        retardo tras abrir la app y repite cada download_requests_interval
        (defecto 5 min). Reclama pendientes (first-claim-wins con
        app_user_name), lanza las descargas en aMule con el mismo embudo
        que el autocompletado y marca done/failed. Ver
        _download_requests_cycle. Nunca lanza: cualquier fallo queda en
        app.log y el siguiente ciclo reintenta."""
        def _loop():
            _time.sleep(self._DOWNLOAD_REQUESTS_STARTUP_DELAY)
            while True:
                try:
                    interval = int(self.config_data.get("download_requests_interval", 300) or 300)
                except (TypeError, ValueError):
                    interval = 300
                try:
                    self._download_requests_cycle()
                except Exception:
                    _log.exception("Solicitudes: fallo en el ciclo")
                _time.sleep(max(60, interval))
        if self._download_requests_worker is None or not self._download_requests_worker.is_alive():
            self._download_requests_worker = threading.Thread(target=_loop, daemon=True)
            self._download_requests_worker.start()

    def _download_requests_user(self) -> str:
        """Quién reclama: app_user_name, o Equipo-<host> si está vacío
        (dos PCs sin nombre reclamarían igual y se pisarían el claim)."""
        user = (self.config_data.get("app_user_name", "") or "").strip()
        if user:
            return user
        try:
            import socket as _socket
            return f"Equipo-{_socket.gethostname()}"
        except Exception:
            return "Equipo"

    def _download_requests_remote_path(self) -> str:
        folder = (self.config_data.get("shared_data_ftp_path", "") or "").strip()
        if not folder:
            return ""
        from core.shared_data import filename
        return f"{folder.rstrip('/')}/{filename('solicitudes')}"

    def _download_requests_ftp(self):
        """Cliente FTP conectado según Ajustes, o None (sin red/credenciales).
        Quien lo recibe debe disconnect() en finally."""
        own_ftp = self._new_ftp_client()
        try:
            ok, _msg = own_ftp.connect(
                self.config_data.get("ftp_host", ""),
                int(self.config_data.get("ftp_port", 21)),
                self.config_data.get("ftp_user", ""),
                self.config_data.get("ftp_password", ""),
                self.config_data.get("ftp_use_tls", False))
        except Exception:
            return None
        if not ok:
            try:
                own_ftp.disconnect()
            except Exception:
                pass
            return None
        return own_ftp

    def _download_requests_push(self, data: dict, remote_path: str) -> bool:
        """Relee el remoto, fusiona *data* encima (gana lo más nuevo por
        solicitud, ver core/download_requests.merge) y sube. True si se
        guardó. Patrón anti-carrera de _toggle_favorite."""
        import json as _json
        from core import download_requests as _dr
        from core.shared_data import read_shared_json
        own_ftp = self._download_requests_ftp()
        if own_ftp is None:
            return False
        try:
            fresh, _is_new = read_shared_json(own_ftp, remote_path, "dict")
        except Exception:
            fresh = None
        try:
            if fresh is None:
                return False
            merged = _dr.prune(_dr.merge(data, fresh))
            payload = _json.dumps(merged, ensure_ascii=False).encode("utf-8")
            ok, _msg = own_ftp.upload_bytes(payload, remote_path)
            return bool(ok)
        except Exception:
            _log.exception("Solicitudes: fallo subiendo %s", remote_path)
            return False
        finally:
            try:
                own_ftp.disconnect()
            except Exception:
                pass

    def _download_requests_cycle(self):
        """Una pasada: done/stuck de mis activas, reclamar hasta el tope,
        lanzar descargas y subir cambios. Sin carpeta compartida o con el
        worker desactivado no hace nada."""
        from core import download_requests as _dr
        from core.shared_data import read_shared_json
        if not self.config_data.get("download_requests_enabled", True):
            return
        remote_path = self._download_requests_remote_path()
        if not remote_path:
            return
        user = self._download_requests_user()
        try:
            max_active = int(self.config_data.get("download_requests_max_active", 5) or 5)
        except (TypeError, ValueError):
            max_active = 5
        own_ftp = self._download_requests_ftp()
        if own_ftp is None:
            return
        try:
            data, _is_new = read_shared_json(own_ftp, remote_path, "dict")
        except Exception:
            data = None
        finally:
            try:
                own_ftp.disconnect()
            except Exception:
                pass
        if data is None:
            return
        # Cambios que no se pudieron subir en el ciclo anterior (Fallo de
        # red al final): se fusionan primero para no perder claims.
        carry = getattr(self, "_download_requests_carry", None) or {}
        if carry:
            data = _dr.merge(data, carry)
            self._download_requests_carry = {}
        data = _dr.prune(data)
        my_ids = [rid for rid, e in data.items()
                  if isinstance(e, dict) and e.get("claimed_by") == user
                  and e.get("status") in ("claimed", "downloading")]
        changed = False
        # Canceladas desde la web que tenía este PC: fuera de aMule.
        for rid in _dr.to_cancel(data, user):
            if self._download_request_cancel_amule(data.get(rid) or {}):
                nd = _dr.mark_cancel_done(data, rid)
                if nd is not None:
                    data = nd
                    changed = True
                    _log.info("Solicitudes: %s cancelada desde la web, descarga quitada de aMule",
                              _dr.describe(data.get(rid) or {}))
        # Índice directo del servidor para las que no tienen fila en
        # memoria/caché (una sola ronda de listados por ciclo).
        wanted = {}
        for rid in my_ids:
            entry = data.get(rid) or {}
            if _dr.is_replacement(entry):
                continue  # ya estaba: la presencia no dice nada (ver check_done)
            try:
                tid = int(entry.get("tmdb_id"))
            except (TypeError, ValueError):
                continue
            wanted[tid] = {"media_type": entry.get("media_type"),
                           "title": entry.get("title"),
                           "year": entry.get("year")}
        try:
            server_index = self._download_requests_server_index(wanted)
        except Exception:
            server_index = {}
        done_now = 0
        amule_queue, local_names = self._download_requests_amule_state(
            any((data.get(r) or {}).get("status") == "downloading" for r in my_ids))
        for rid in my_ids:
            outcome = self._download_request_check_done(data.get(rid) or {}, server_index)
            if outcome is None and amule_queue is not None and _dr.lost_in_amule(
                    data.get(rid) or {}, amule_queue, local_names):
                outcome = "lost"
            if outcome == "done":
                nd = _dr.mark_status(data, rid, "done", user=user)
                if nd is not None:
                    data = nd
                    changed = True
                    done_now += 1
                    _log.info("Solicitudes: %s completada (%s)", rid, _dr.describe(data.get(rid) or {}))
            elif outcome == "lost":
                nd = _dr.release(data, rid, user=user, error="ya no estaba en aMule")
                if nd is not None:
                    data = nd
                    changed = True
                    _log.info("Solicitudes: %s ya no estaba en aMule, se vuelve a lanzar",
                              _dr.describe(data.get(rid) or {}))
            elif outcome == "relaunch":
                nd = _dr.mark_status(data, rid, "claimed", user=user)
                if nd is not None:
                    data = nd
                    changed = True
            elif outcome == "stuck":
                nd = _dr.release(data, rid, user=user, error="sin verificar en 7 días")
                if nd is not None:
                    data = nd
                    changed = True
        # Solo ocupan hueco las recientes: una descarga de más de
        # CLAIM_TTL_SECONDS sigue en aMule esperando fuentes (el done
        # llega al subirse) y contarla bloqueaba la cola entera -- caso
        # real: 5 capítulos sin fuentes desde la mañana y nada nuevo se
        # lanzaba en horas.
        active = sum(1 for _rid, e in data.items()
                     if isinstance(e, dict) and e.get("claimed_by") == user
                     and e.get("status") in ("claimed", "downloading")
                     and not _dr.is_claim_stale(e))
        claimed_now = []
        for rid, _entry in _dr.pending_for_worker(data):
            if active >= max(1, max_active):
                break
            if (_entry.get("claimed_by") == user
                    and _entry.get("status") == "downloading"):
                # Ya está en MI aMule: relanzarla repetiría la búsqueda y
                # podría bajar otra copia. Otro equipo sí puede cogerla.
                continue
            nd = _dr.claim(data, rid, user)
            if nd is None:
                continue
            data = nd
            active += 1
            changed = True
            claimed_now.append(rid)
        if claimed_now:
            # Publicar los claims ANTES de lanzar (búsquedas de minutos):
            # así otro PC los ve reclamados y no duplica el trabajo.
            if self._download_requests_push(data, remote_path):
                changed = False
            else:
                self._download_requests_carry = _dr.merge(
                    getattr(self, "_download_requests_carry", None) or {}, data)
        for rid in list(claimed_now) + [rid for rid in my_ids
                                        if isinstance(data.get(rid), dict)
                                        and data.get(rid).get("status") == "claimed"]:
            entry = data.get(rid) or {}
            if entry.get("claimed_by") != user or entry.get("status") != "claimed":
                continue
            nd, _launched = self._download_request_launch(data, rid)
            if nd is not None:
                data = nd
                changed = True
        if changed:
            if self._download_requests_push(data, remote_path):
                if done_now:
                    self._download_requests_notify_web()
            else:
                self._download_requests_carry = _dr.merge(
                    getattr(self, "_download_requests_carry", None) or {}, data)

    def _download_request_cancel_amule(self, entry: dict) -> bool:
        """Quita de aMule la descarga de una solicitud cancelada desde la
        web (borra también lo descargado a medias). True si ya no queda
        nada suyo en aMule; False si aMule no responde (se reintenta en
        el siguiente ciclo)."""
        from core import download_requests as _dr
        try:
            with self._amule_ec_lock:
                ec = EcClient(host=self.config_data.get("amule_host", "localhost"),
                              port=self.config_data.get("amule_port", 4712),
                              password=self.config_data.get("amule_password", ""),
                              timeout=8.0)
                ec.connect()
                try:
                    queue = [{"hash_hex": it.get("hash_hex"), "name": it.get("name")}
                             for it in (ec.get_download_queue() or [])]
                    for h in _dr.amule_hashes_to_cancel(entry, queue):
                        ok, msg = ec.cancel_download(h)
                        if not ok:
                            _log.warning("Solicitudes: no se pudo cancelar %s en aMule: %s", h, msg)
                            return False
                finally:
                    ec.close()
        except Exception as e:
            _log.debug("Solicitudes: aMule no disponible para cancelar (%s)", e)
            return False
        return True

    def _cancel_web_request(self, req_id: str) -> bool:
        """Cancela una solicitud de la web desde este equipo (botón de la
        subpestaña Solicitudes): la marca "cancelled" en la cola compartida
        (pegajoso en merge, ver core/download_requests.py) y quita su
        descarga de aMule si la tenía este PC. El worker en curso la ignora
        desde entonces por estado final. True si quedó cancelada."""
        from core import download_requests as _dr
        user = self._download_requests_user()
        remote_path = self._download_requests_remote_path()
        if not req_id or not remote_path:
            return False
        entry = None
        try:
            from core.shared_data import read_shared_json
            own_ftp = self._download_requests_ftp()
            if own_ftp is None:
                return False
            try:
                data, _is_new = read_shared_json(own_ftp, remote_path, "dict")
            finally:
                try:
                    own_ftp.disconnect()
                except Exception:
                    pass
            if not isinstance(data, dict):
                return False
            entry = data.get(req_id)
            if not isinstance(entry, dict) or entry.get("status") not in (
                    "pending", "claimed", "downloading"):
                return False
            nd = _dr.mark_status(data, req_id, "cancelled", user=user)
            if nd is None:
                return False   # reclamada por otro equipo: no tocar
            cancelled = dict(nd.get(req_id) or {})
            cancelled["cancelled_by"] = user
            nd = dict(nd)
            nd[req_id] = cancelled
            if not self._download_requests_push(nd, remote_path):
                return False
        except Exception:
            _log.exception("Solicitudes: no se pudo cancelar %s", req_id)
            return False
        try:
            if isinstance(entry, dict):
                self._download_request_cancel_amule(entry)
        except Exception:
            pass
        _log.info("Solicitudes: %s cancelada por %s", _dr.describe(entry), user)
        return True

    def _download_requests_amule_state(self, needed: bool):
        """(cola de aMule [{hash_hex, name}] o None si no se pudo leer,
        nombres de archivo de la carpeta vigilada) -- para detectar
        descargas de solicitudes que ya no están en aMule (ver
        _dr.lost_in_amule). Sin aMule: (None, []) y no se toca nada."""
        if not needed:
            return None, []
        queue = None
        try:
            with self._amule_ec_lock:
                ec = EcClient(host=self.config_data.get("amule_host", "localhost"),
                              port=self.config_data.get("amule_port", 4712),
                              password=self.config_data.get("amule_password", ""),
                              timeout=8.0)
                ec.connect()
                try:
                    queue = [{"hash_hex": it.get("hash_hex"), "name": it.get("name")}
                             for it in (ec.get_download_queue() or [])]
                finally:
                    ec.close()
        except Exception as e:
            _log.debug("Solicitudes: sin cola de aMule (%s)", e)
            return None, []
        names = []
        folder = (self.config_data.get("watch_folder", "") or "").strip()
        if folder:
            try:
                import os as _os
                for _root, _dirs, files in _os.walk(folder):
                    names.extend(files)
                    if len(names) > 5000:
                        break
            except Exception:
                pass
        return queue, names

    def _download_requests_notify_web(self):
        """Avisa a la web de solicitudes de que hay algo completado (ya
        escrito en la cola compartida): la web relee la cola y manda la
        notificación push a quien lo pidió (ver api/index.php
        notify_check). En un hilo y sin reintentos: si falla, la web lo
        detecta igual al usarse. Nunca lanza."""
        base = (self.config_data.get("solicitudes_web_url", "") or "").strip()
        if not base:
            return

        def _go():
            import urllib.request
            url = base.rstrip("/") + "/api/index.php?action=notify_check"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "aIBechos"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    _log.info("Solicitudes: aviso a la web (%s): %s", resp.status,
                              resp.read(200).decode("utf-8", "replace"))
            except Exception as e:
                _log.warning("Solicitudes: no se pudo avisar a la web (%s): %s", url, e)
        threading.Thread(target=_go, daemon=True).start()

    def _download_request_check_done(self, entry: dict, server_index: dict | None = None) -> str | None:
        """Revisa si lo pedido ya está en el servidor: "done" (ya no
        falta), "stuck" (downloading sin verificar en más de
        STUCK_AFTER_SECONDS), "relaunch" (sustitución sin lanzar como
        tal) o None (sigue pendiente). Mira las filas en
        memoria + completas en caché y, si la serie/peli no tiene fila
        (p. ej. una peli que nunca entró en las listas de Películas),
        pregunta directo al servidor de medios (ver
        _download_requests_server_index, sin TMDB). Sin red salvo ese
        último recurso."""
        from core import download_requests as _dr
        if not isinstance(entry, dict):
            return None
        try:
            tmdb_id = int(entry.get("tmdb_id"))
        except (TypeError, ValueError):
            return None
        media_type = entry.get("media_type")
        season = entry.get("season")
        episode = entry.get("episode")
        if _dr.is_replacement(entry):
            # Sustitución de algo que YA estaba: verlo en el servidor no
            # dice nada (sería done al instante, sin descargar). Solo
            # cuenta una subida de este PC posterior al lanzamiento
            # (replace_since, ver _download_request_launch). Sin
            # replace_since = la web la convirtió en sustitución cuando ya
            # se descargaba como normal: se relanza para registrarla.
            if not entry.get("replace_since"):
                return "relaunch"
            try:
                if _dr.uploaded_request_since(_dr.load_upload_history(), entry,
                                              entry.get("replace_since")):
                    return "done"
            except Exception:
                pass
            return self._download_request_check_done_stuck(entry)
        if media_type == "movie":
            for row in (getattr(self, "_movies_results", None) or []):
                try:
                    if int(row.get("tmdb_id")) != tmdb_id:
                        continue
                except (TypeError, ValueError):
                    continue
                return "done" if row.get("in_server") else None
            if tmdb_id in (server_index or {}).get("movies", set()):
                return "done"
            return self._download_request_check_done_stuck(entry)
        row = None
        for r in (self._missing_ep_results or []):
            try:
                if int(r.get("tmdb_id")) == tmdb_id:
                    row = r
                    break
            except (TypeError, ValueError):
                continue
        if row is not None:
            missing = row.get("missing") or {}
            if season is None:
                return "done" if not missing else None
            eps = missing.get(season, missing.get(str(season), [])) or []
            if episode is not None:
                return "done" if episode not in eps else None
            return "done" if not eps else None
        try:
            complete = self._load_complete_series_from_cache() or []
        except Exception:
            complete = []
        for r in complete:
            try:
                if int(r.get("tmdb_id")) == tmdb_id:
                    return "done"
            except (TypeError, ValueError):
                continue
        # Subido por ESTE pc (a mano o por otro flujo): el historial
        # local lo registra aunque la peli/serie no tenga fila en
        # Episodios/Películas (caso real: peli pedida por la web, subida
        # manual, sin rastro en las listas -- el done no llegaba nunca).
        try:
            from core.download_requests import load_upload_history as _luh
            from core.download_requests import uploaded_request as _ur
            if _ur(_luh(), entry):
                return "done"
        except Exception:
            pass
        present = (server_index or {}).get("series", {}).get(tmdb_id)
        if present is not None:
            if season is not None and episode is not None:
                return "done" if (int(season), int(episode)) in present else None
            if season is not None:
                try:
                    expected = self._tmdb_expected_episodes(tmdb_id).get(int(season), [])
                except Exception:
                    expected = []
                if expected and all(e in present for e in expected):
                    return "done"
                return None
            try:
                expected_all = self._tmdb_expected_episodes(tmdb_id) or {}
            except Exception:
                expected_all = {}
            if expected_all and all(e in present
                                   for s, eps in expected_all.items() for e in eps):
                return "done"
            return None
        return self._download_request_check_done_stuck(entry)

    @staticmethod
    def _download_request_check_done_stuck(entry: dict) -> str | None:
        """"stuck" si lleva downloading sin verificar más de
        STUCK_AFTER_SECONDS (el worker lo libera para reintento)."""
        from core import download_requests as _dr
        if (isinstance(entry, dict) and entry.get("status") == "downloading"
                and _dr.is_claim_stale(entry, ttl=_dr.STUCK_AFTER_SECONDS)):
            return "stuck"
        return None

    def _download_requests_server_index(self, wanted: dict) -> dict:
        """Presencia directa en el servidor de medios para lo pedido:
        {"movies": {tmdb}, "series": {tmdb: {(s, e)}}}.
        *wanted*: {tmdb: {"media_type", "title", "year"}}.

        Empareja por TMDB exacto O por nombre normalizado (los IDs de
        Jellyfin/Plex a veces están mal -- caso real: peli con Tmdb
        erróneo que solo casa por título; ver core/download_requests
        .names_match, misma regla que la web). Jellyfin primero, Plex
        solo para lo que falte. Lo que falla se omite (sin dato, no
        ausencia). Solo se llama con lo que de verdad hace falta.
        """
        from core import download_requests as _dr

        index: dict = {"movies": set(), "series": {}}
        wanted_movies = {t: v for t, v in (wanted or {}).items()
                         if isinstance(v, dict)
                         and v.get("media_type") == "movie"}
        wanted_tv = {t: v for t, v in (wanted or {}).items()
                     if isinstance(v, dict)
                     and v.get("media_type") != "movie"}
        if not wanted_movies and not wanted_tv:
            return index

        def _as_int(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def _find_item(items, tmdb, title, year):
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                if _as_int(it.get("tmdb_id")) == tmdb:
                    return it
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                if _dr.names_match(title, year, it.get("name") or "",
                                   None):
                    return it
            return None

        jhost = (self.config_data.get("jellyfin_host", "") or "").strip()
        jkey = (self.config_data.get("jellyfin_api_key", "") or "").strip()
        if jhost and jkey:
            try:
                from core import media_server_refresh as _msr
                if wanted_movies:
                    jmovies = _msr.get_jellyfin_movies(jhost, jkey) or []
                    for tmdb, info in wanted_movies.items():
                        if tmdb in index["movies"]:
                            continue
                        if _find_item(jmovies, tmdb, info.get("title"),
                                      info.get("year")) is not None:
                            index["movies"].add(tmdb)
                if wanted_tv:
                    jseries = _msr.get_jellyfin_series(jhost, jkey) or []
                    for tmdb, info in wanted_tv.items():
                        if tmdb in index["series"]:
                            continue
                        item = _find_item(jseries, tmdb, info.get("title"),
                                          info.get("year"))
                        if item is None:
                            continue
                        try:
                            eps = _msr.get_jellyfin_episodes(
                                jhost, jkey, item.get("id"))
                        except Exception:
                            eps = None
                        index["series"][tmdb] = set(eps) if eps else set()
            except Exception:
                pass
        missing_movies = set(wanted_movies) - index["movies"]
        missing_tv = set(wanted_tv) - set(index["series"])
        if (missing_movies or missing_tv):
            phost = (self.config_data.get("plex_host", "") or "").strip()
            ptoken = (self.config_data.get("plex_token", "") or "").strip()
            if phost and ptoken:
                try:
                    from core import media_server_refresh as _msr2
                    if missing_movies:
                        pmovies = _msr2.get_plex_movies(phost, ptoken) or []
                        for tmdb in missing_movies:
                            info = wanted_movies[tmdb]
                            if _find_item(pmovies, tmdb, info.get("title"),
                                           info.get("year")) is not None:
                                index["movies"].add(tmdb)
                    if missing_tv:
                        pseries = _msr2.get_plex_series(phost, ptoken) or []
                        for tmdb in missing_tv:
                            info = wanted_tv[tmdb]
                            item = _find_item(pseries, tmdb,
                                              info.get("title"),
                                              info.get("year"))
                            if item is None:
                                continue
                            try:
                                eps = _msr2.get_plex_episodes(
                                    phost, ptoken, item.get("rating_key"))
                            except Exception:
                                eps = None
                            index["series"][tmdb] = set(eps) if eps else set()
                except Exception:
                    pass
        return index

    def _download_request_canonical(self, entry: dict) -> tuple[str, str]:
        """(título, año) canónicos vía TMDB para construir la query de
        aMule (respeta series_search_patterns); si TMDB falla, lo que
        traiga la solicitud tal cual."""
        title = str(entry.get("title") or "").strip()
        year = str(entry.get("year") or "").strip()
        if not title:
            return title, year
        try:
            prefer = "movie" if entry.get("media_type") == "movie" else "tv"
            results = self.tmdb.search_multi(title, prefer_type=prefer)
            if results:
                info = self.tmdb.build_media_info(results[0])
                return (info.title or title), (info.year or year)
        except Exception:
            pass
        return title, year

    def _download_request_targets(self, entry: dict, name: str) -> list:
        """Qué descargar para la solicitud: lista de ("episode", s, e),
        ("season_pack", s) o [("movie",)]. Serie/temporada se expanden a
        episodios (hueco de la fila si se conoce, si no lo emitido según
        TMDB), con tope _DOWNLOAD_REQUESTS_EXPAND_CAP; si no hay
        episodios localizables, un pack de temporada como último recurso."""
        media_type = entry.get("media_type")
        season = entry.get("season")
        episode = entry.get("episode")
        if media_type == "movie":
            return [("movie",)]
        if season is not None and episode is not None:
            return [("episode", int(season), int(episode))]
        try:
            tmdb_id = int(entry.get("tmdb_id"))
        except (TypeError, ValueError):
            return []
        wanted = {}
        for r in (self._missing_ep_results or []):
            try:
                if int(r.get("tmdb_id")) == tmdb_id:
                    wanted = r.get("missing") or {}
                    break
            except (TypeError, ValueError):
                continue
        if not wanted:
            try:
                expected = self._tmdb_expected_episodes(tmdb_id) or {}
            except Exception:
                expected = {}
            wanted = expected
        if season is not None:
            eps = wanted.get(season, wanted.get(str(season), [])) or []
            if eps:
                return [("episode", int(season), int(e))
                        for e in eps[:self._DOWNLOAD_REQUESTS_EXPAND_CAP]]
            return [("season_pack", int(season))]
        targets = []
        for s in sorted(wanted, key=lambda x: int(x)):
            for e in wanted.get(s, []) or []:
                targets.append(("episode", int(s), int(e)))
                if len(targets) >= self._DOWNLOAD_REQUESTS_EXPAND_CAP:
                    _log.warning("Solicitudes: %s supera el tope de %d descargas por ciclo",
                                 entry.get("title"), self._DOWNLOAD_REQUESTS_EXPAND_CAP)
                    return targets
        if targets:
            return targets
        # Serie sin hueco conocido ni TMDB: al menos un pack T1.
        return [("season_pack", 1)]

    def _download_request_launch(self, data: dict, req_id: str):
        """Lanza en aMule lo que pide la solicitud (ya reclamada por este
        equipo): devuelve (nuevo_data, lanzó_algo). Todo ok -> mark
        downloading; todo falla -> release con el motivo (suelta el claim
        para que otro equipo lo reintente, con intentos acotados)."""
        from core import download_requests as _dr
        from core.amule_search import build_amule_query, build_amule_season_query
        entry = data.get(req_id) or {}
        user = self._download_requests_user()
        name, year = self._download_request_canonical(entry)
        if not name:
            return _dr.release(data, req_id, user=user, error="sin título"), False
        is_movie = entry.get("media_type") == "movie"
        try:
            templates = self.config_data.get("series_search_patterns", {}) or {}
        except Exception:
            templates = {}
        try:
            prefers = bool(self._series_prefers_castellano(name))
        except Exception:
            prefers = False
        try:
            typical = self._typical_size_for_series(name, entry.get("season"))
        except Exception:
            typical = None
        targets = self._download_request_targets(entry, name)
        if not targets:
            return _dr.release(data, req_id, user=user, error="sin objetivos"), False
        ok_any = False
        last_error = ""
        hashes = []
        for target in targets:
            try:
                if target[0] == "movie":
                    query = build_amule_query(name, None, None, year, templates, prefers)
                    ok, motivo, _h = self._auto_amule_download_series(
                        query, is_movie=True, expected_year=int(year) if str(year).isdigit() else None)
                elif target[0] == "season_pack":
                    query = build_amule_season_query(name, target[1], templates, prefers)
                    ok, motivo, _h = self._auto_amule_download_series(query, is_movie=False)
                else:
                    _t, s, e = target
                    query = build_amule_query(name, s, e, year, templates, prefers)
                    ok, motivo, _h = self._auto_amule_download_series(
                        query, is_movie=False, typical_size=typical)
            except Exception as ex:
                ok, motivo, _h = False, str(ex)[:200], ""
            if ok:
                ok_any = True
                if _h:
                    hashes.append(_h)
            else:
                last_error = motivo
                _log.warning("Solicitudes: %s sin candidato (%s): %s",
                             _dr.describe(entry), query, motivo)
        if ok_any:
            _log.info("Solicitudes: %s en descarga", _dr.describe(entry))
            nd = _dr.mark_status(data, req_id, "downloading", user=user)
            if nd is not None and hashes:
                # Para ver luego si sigue en aMule (ver _dr.lost_in_amule).
                nd = _dr.set_amule_hashes(nd, req_id, hashes) or nd
            if nd is not None and _dr.is_replacement(entry):
                self._download_request_record_replacement(entry, name, year)
                nd = _dr.mark_replacement_launched(nd, req_id) or nd
            return nd, True
        return _dr.release(data, req_id, user=user, error=last_error or "sin candidato"), False

    def _download_request_record_replacement(self, entry: dict, name: str, year: str):
        """Sustitución pedida desde la web: registra el pendiente
        "request" de core/slim_pending.py para que AutoWatcher suba lo
        que llegue aunque ya exista en el servidor y borre el viejo
        DESPUÉS (mismo flujo que el reemplazo por adelgazamiento). Solo
        con la descarga ya lanzada. Sin hilos GUI aquí dentro."""
        try:
            from core import download_requests as _dr
            from core.slim_pending import record as _record_pending, norm_key
            is_movie = entry.get("media_type") == "movie"
            try:
                added_by = str(self.config_data.get("app_user_name", "") or "").strip()
            except Exception:
                added_by = ""
            _record_pending({
                "kind": "request",
                "media_type": "movie" if is_movie else "tv",
                "key_norm": norm_key(name),
                "tmdb_id": int(entry.get("tmdb_id")),
                "season": None if is_movie else entry.get("season"),
                "episode": None if is_movie else entry.get("episode"),
                "year": str(year or "") if is_movie else "",
                "heavy_remote_file": "",
                "heavy_size": 0,
                "max_light_size": 0,
                "query": "",
                "added_ts": _time.time(),
                "added_by": added_by or str(entry.get("requested_by") or ""),
            })
            _log.info("Solicitudes: %s es sustitución, se reemplazará al llegar",
                      _dr.describe(entry))
        except Exception:
            _log.exception("Solicitudes: no se pudo registrar la sustitución")
