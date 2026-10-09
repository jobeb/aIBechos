"""
Estado y servicios compartidos por todas las pestañas de la interfaz Qt:
configuración, cliente TMDB, favoritos/reservas (con su sincronización por
FTP), aviso en la barra de estado, enlaces personalizables...

Es el equivalente a los métodos "de servicio" que en la versión Tk viven
repartidos por la clase App (_toggle_favorite, _push_reservations_to_ftp,
_open_custom_link...). Nada de aquí crea widgets salvo los avisos modales
(QMessageBox) que necesitan un padre.
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser

import requests
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from config import Config
from core import shared_data
from core.amule_download import EC_LOCK
from core.api_client import TMDBClient
from core.applog import get_logger
from core.transfer import make_client
from gui_qt.bridge import ui, run_in_thread
from gui_qt.theme import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR

_log = get_logger("aIBechos.qt", "app.log")


class AppContext(QObject):
    status = Signal(str, str)            # texto, color
    favorites_changed = Signal()
    reservations_changed = Signal()
    missing_cache_changed = Signal()     # missing_episodes_cache.json cambió en disco

    _SYNC_MIN_INTERVAL = 20   # s, mismo freno que la versión Tk

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config()
        self.tmdb = TMDBClient(self.config.get("tmdb_api_key", ""))
        from core.favorites import load_local_cache as _load_fav
        from core.reservations import load_local_cache as _load_res
        self.favorites = _load_fav()
        self.reservations = _load_res()
        self.ec_lock = EC_LOCK
        self._last_sync = {}
        self._ftp_dir_cache = {}       # raíz FTP -> carpetas, vive lo que dure la sesión
        self._ftp_dir_lock = threading.Lock()

    # ── Barra de estado ──

    def set_status(self, text: str, color: str = PENDING_COLOR) -> None:
        """Seguro desde cualquier hilo."""
        if threading.current_thread() is threading.main_thread():
            self.status.emit(text, color)
        else:
            ui(lambda: self.status.emit(text, color))

    # ── FTP ──

    def new_ftp_client(self):
        return make_client(self.config.get("ftp_protocol", "ftp"))

    def connect_ftp(self):
        """Conexión propia y nueva (nunca compartida entre hilos), o None si
        no hay FTP configurado o no se pudo conectar. Quien la recibe hace
        disconnect()."""
        if not self.config.get("ftp_host", ""):
            return None
        ftp = self.new_ftp_client()
        try:
            ok, _msg = ftp.connect(
                self.config.get("ftp_host", ""), int(self.config.get("ftp_port", 21)),
                self.config.get("ftp_user", ""), self.config.get("ftp_password", ""),
                self.config.get("ftp_use_tls", False))
        except Exception:
            ok = False
        if not ok:
            try:
                ftp.disconnect()
            except Exception:
                pass
            return None
        return ftp

    def existing_series_path(self, r: dict, ftp=None, use_cache_only: bool = False) -> str:
        """Ruta "raíz/carpeta" ya existente en el FTP para la serie *r*, o
        "" -- mismo emparejamiento que la subida y AutoWatcher
        (core.ftp_categories.find_existing_category_folder), con el nombre
        real de carpeta del servidor de medios si se conoce. Con
        use_cache_only=False lista de verdad (llamar desde un hilo, con una
        conexión *ftp* propia)."""
        from core.ftp_categories import find_existing_category_folder
        cats = (self.config.get("ftp_categories", {}) or {}).get("tv", [])

        def dir_lookup(root):
            with self._ftp_dir_lock:
                cached = self._ftp_dir_cache.get(root)
            if cached:
                return cached
            if use_cache_only or ftp is None:
                return None
            # Dos listados unidos: un único NLST a veces vuelve incompleto
            # sin error en algunos servidores (ver la versión Tk).
            first = set(ftp.list_dirs(root) or [])
            second = set(ftp.list_dirs(root) or [])
            dirs = list(first | second)
            with self._ftp_dir_lock:
                self._ftp_dir_cache[root] = dirs
            return dirs

        year = (r.get("first_air_date") or "")[:4]
        try:
            category, folder = find_existing_category_folder(
                cats, r.get("name", ""), r.get("folder_name"), dir_lookup,
                known_year=year if year.isdigit() else None)
        except Exception:
            _log.warning("Ruta de '%s' en el FTP: fallo al buscarla", r.get("name"), exc_info=True)
            return ""
        if not category or not folder:
            return ""
        return f"{category.get('root', '').rstrip('/')}/{folder}"

    def shared_path(self, key: str) -> str:
        """Ruta remota de un archivo compartido (ver core/shared_data.py) o
        "" si no hay carpeta compartida configurada."""
        folder = self.config.get("shared_data_ftp_path", "").strip()
        if not folder:
            return ""
        return f"{folder.rstrip('/')}/{shared_data.filename(key)}"

    def _throttled(self, key: str) -> bool:
        now = time.time()
        if now - self._last_sync.get(key, 0) < self._SYNC_MIN_INTERVAL:
            return True
        self._last_sync[key] = now
        return False

    def _read_modify_write(self, key: str, transform, on_merged) -> None:
        """Descarga el JSON compartido FRESCO, le aplica *transform* y lo
        vuelve a subir (minimiza la carrera con otros clientes); llama a
        *on_merged(merged)* en el hilo de la interfaz si se subió. Si el
        remoto existe pero no se puede leer, no se toca (read_shared_json)."""
        remote_path = self.shared_path(key)
        if not remote_path:
            return

        def worker():
            ftp = self.connect_ftp()
            if ftp is None:
                return
            try:
                data, _new = shared_data.read_shared_json(ftp, remote_path, "dict")
                if data is None:
                    return
                merged = transform(data)
                payload = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, _m = ftp.upload_bytes(payload, remote_path)
                if up_ok:
                    ui(lambda: on_merged(merged))
            finally:
                ftp.disconnect()
        run_in_thread(worker)

    def _fetch_shared(self, key: str, on_data) -> None:
        remote_path = self.shared_path(key)
        if not remote_path:
            return

        def worker():
            ftp = self.connect_ftp()
            if ftp is None:
                return
            try:
                data, is_new = shared_data.read_shared_json(ftp, remote_path, "dict")
            finally:
                ftp.disconnect()
            if data is not None and not is_new:
                ui(lambda: on_data(data))
        run_in_thread(worker)

    # ── Favoritos ──

    def is_favorite(self, media_type: str, tmdb_id) -> bool:
        from core.favorites import is_favorite
        return is_favorite(self.favorites, media_type, tmdb_id)

    def favorite_tooltip(self, media_type: str, tmdb_id) -> str:
        if not self.is_favorite(media_type, tmdb_id):
            return "Añadir a favoritos"
        return "Quitar de favoritos" + self.favorite_attribution(media_type, tmdb_id)

    def favorite_attribution(self, media_type: str, tmdb_id) -> str:
        """Sufijo " Marcado por X el <fecha>." (o "" si no consta)."""
        from core.favorites import favorite_info
        info = favorite_info(self.favorites, media_type, tmdb_id)
        return _attribution("Marcado", info.get("added_by"), info.get("added_at"))

    def toggle_favorite(self, media_type: str, tmdb_id, name: str) -> None:
        """Optimista en local y luego sincroniza con el FTP (mismo patrón que
        App._toggle_favorite de la versión Tk)."""
        from core.favorites import add_favorite, remove_favorite, save_local_cache
        if not self.is_favorite(media_type, tmdb_id):
            op = add_favorite
            args = (media_type, tmdb_id, name, self.config.get("app_user_name", "").strip(), int(time.time()))
        else:
            op, args = remove_favorite, (media_type, tmdb_id)
        self.favorites = op(self.favorites, *args)
        save_local_cache(self.favorites)
        self.favorites_changed.emit()
        self._read_modify_write("favoritos", lambda d: op(d, *args), self._apply_synced_favorites)

    def _apply_synced_favorites(self, merged: dict) -> None:
        from core.favorites import save_local_cache
        if merged == self.favorites:
            return
        self.favorites = merged
        save_local_cache(self.favorites)
        self.favorites_changed.emit()

    def sync_favorites(self) -> None:
        if not self._throttled("favoritos"):
            self._fetch_shared("favoritos", self._apply_synced_favorites)

    # ── Reservas ──

    def is_reserved(self, media_type: str, tmdb_id) -> bool:
        from core.reservations import is_reserved
        return is_reserved(self.reservations, media_type, tmdb_id)

    def reservation_tooltip(self, media_type: str, tmdb_id) -> str:
        if not self.is_reserved(media_type, tmdb_id):
            return "Reservar: protege del borrado"
        return "Quitar reserva" + self.reservation_attribution(media_type, tmdb_id)

    def reservation_attribution(self, media_type: str, tmdb_id) -> str:
        """Sufijo con quién reservó y cuándo, y que solo esa persona la suelta."""
        from core.reservations import reserved_by, reserved_at
        owner = (reserved_by(self.reservations, media_type, tmdb_id) or "").strip()
        text = _attribution("Reservado", owner, reserved_at(self.reservations, media_type, tmdb_id))
        if owner:
            text += f" Solo {owner} puede soltarla."
        return text

    def best_known_size_bytes(self, media_type: str, tmdb_id, fallback_bytes: int = 0) -> int:
        """Tamaño real de la carpeta completa si "Liberar espacio" lo tiene en
        su caché de último análisis (ver App._best_known_size_bytes)."""
        try:
            from core.cleanup_candidates_cache import load_cache
            for it in (load_cache() or {}).get("items", []):
                if it.media_type == media_type and it.tmdb_id == tmdb_id:
                    return it.size_bytes
        except Exception:
            pass
        return fallback_bytes

    def toggle_reservation(self, parent, media_type: str, tmdb_id, name: str, size_bytes: int) -> None:
        """Igual que favoritos, más dos comprobaciones: hace falta "Tu
        nombre" (a quién cargar la cuota) y, al reservar, sitio en la cuota;
        al liberar, solo quien la reservó."""
        from core.reservations import (add_reservation, remove_reservation, fits_in_quota,
                                       remaining_bytes, reserved_by, save_local_cache)
        user = self.config.get("app_user_name", "").strip()
        if not user:
            QMessageBox.warning(parent, "Falta tu nombre",
                                "Configura \"Tu nombre\" en Ajustes → Conexión FTP antes de reservar "
                                "espacio, así se sabe a quién cargarle la cuota.")
            return
        quota_bytes = int(self.config.get("reservation_quota_gb", 100)) * 1024 ** 3
        if not self.is_reserved(media_type, tmdb_id):
            if not fits_in_quota(self.reservations, user, size_bytes, quota_bytes):
                remaining_gb = remaining_bytes(self.reservations, user, quota_bytes) / (1024 ** 3)
                QMessageBox.warning(parent, "Cuota de reservas agotada",
                                    f"Te quedan {remaining_gb:.1f}GB libres de tu cuota de "
                                    f"{quota_bytes / 1024 ** 3:.0f}GB -- \"{name}\" no cabe. Libera "
                                    "alguna reserva antes de añadir otra.")
                return
            op, args = add_reservation, (media_type, tmdb_id, name, size_bytes, user, int(time.time()))
        else:
            owner = reserved_by(self.reservations, media_type, tmdb_id)
            if owner and owner != user:
                QMessageBox.warning(parent, "Reservado por otra persona",
                                    f"\"{name}\" lo reservó {owner} -- solo esa persona puede liberarlo.")
                return
            op, args = remove_reservation, (media_type, tmdb_id)
        self.reservations = op(self.reservations, *args)
        save_local_cache(self.reservations)
        self.reservations_changed.emit()
        self._read_modify_write("reservas", lambda d: op(d, *args), self._apply_synced_reservations)

    def _apply_synced_reservations(self, merged: dict) -> None:
        from core.reservations import save_local_cache
        if merged == self.reservations:
            return
        self.reservations = merged
        save_local_cache(self.reservations)
        self.reservations_changed.emit()

    def sync_reservations(self) -> None:
        if not self._throttled("reservas"):
            self._fetch_shared("reservas", self._apply_synced_reservations)

    # ── Episodios que faltan (caché compartida) ──

    def sync_missing_episodes(self) -> None:
        """Fusiona la caché compartida sobre el mirror local (conservando lo
        personal, ver merge_remote_into_local) y avisa si cambió."""
        if self._throttled("episodios_que_faltan"):
            return

        def _apply(remote_cache):
            from core.missing_episodes_cache import load_cache, save_cache, merge_remote_into_local
            previous = load_cache()
            merged = merge_remote_into_local(previous, remote_cache)
            if merged == previous:
                return
            try:
                save_cache(merged)
            except Exception:
                _log.warning("Episodios que faltan (Qt): no se pudo guardar el mirror local", exc_info=True)
                return
            self.missing_cache_changed.emit()
        self._fetch_shared("episodios_que_faltan", _apply)

    def push_missing_episodes(self) -> None:
        """Comparte la caché local actual, sin los campos personales."""
        remote_path = self.shared_path("episodios_que_faltan")
        if not remote_path:
            return

        def worker():
            from core.missing_episodes_cache import load_cache, strip_personal_fields
            ftp = self.connect_ftp()
            if ftp is None:
                return
            try:
                payload = strip_personal_fields(load_cache())
                data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, msg = ftp.upload_bytes(data, remote_path)
                if not up_ok:
                    _log.warning("Episodios que faltan (Qt): no se pudo subir la caché compartida (%s)", msg)
            finally:
                ftp.disconnect()
        run_in_thread(worker)

    # ── Veredictos de doblaje de la IA, compartidos (core/shared_dub_verdicts.py) ──

    def fetch_shared_dub_verdicts(self, on_data) -> None:
        if not self._throttled("doblaje_ia"):
            self._fetch_shared("doblaje_ia", on_data)

    def push_shared_dub_verdict(self, tmdb_id, verdict: dict, on_merged) -> None:
        """Solo tras una consulta MANUAL a la IA (nunca desde la de lotes)."""
        from core.shared_dub_verdicts import set_verdict
        user = self.config.get("app_user_name", "")
        self._read_modify_write("doblaje_ia", lambda d: set_verdict(d, tmdb_id, verdict, user), on_merged)

    # ── Enlaces ──

    def open_custom_link(self, template: str, variables: dict, background: bool = False) -> None:
        """Abre la URL en el navegador o, si el enlace es "en segundo plano",
        hace un GET silencioso (webhooks de Sonarr/Radarr...)."""
        from core.custom_links import build_link_url
        url = build_link_url(template, variables)
        if not url:
            return
        if not background:
            webbrowser.open(url)
            return
        self.set_status(f"Ejecutando en segundo plano: {url}")

        def worker():
            try:
                resp = requests.get(url, timeout=10)
            except Exception as e:
                self.set_status(f"Enlace en segundo plano falló: {e}", ERROR_COLOR)
                return
            if resp.status_code < 400:
                self.set_status("✓ Enlace en segundo plano completado", SUCCESS_COLOR)
            else:
                self.set_status(f"Enlace en segundo plano devolvió {resp.status_code}", WARNING_COLOR)
        run_in_thread(worker)

    def open_in_media_server(self, source: str, server_id) -> None:
        if not server_id:
            self.set_status("No se pudo abrir: vuelve a analizar para que esta serie tenga el "
                            "enlace guardado", WARNING_COLOR)
            return
        if source == "jellyfin":
            host = self.config.get("jellyfin_host", "").rstrip("/")
            if host:
                webbrowser.open(f"{host}/web/#/details?id={server_id}")
            return
        if source == "plex":
            host = self.config.get("plex_host", "").rstrip("/")
            token = self.config.get("plex_token", "")
            if not host or not token:
                return
            self.set_status("Abriendo en Plex...")

            def worker():
                from core.media_server_refresh import get_plex_machine_identifier
                machine_id = get_plex_machine_identifier(host, token)
                if not machine_id:
                    self.set_status("No se pudo conectar con Plex para abrir el enlace", ERROR_COLOR)
                    return
                webbrowser.open(f"https://app.plex.tv/desktop/#!/server/{machine_id}/details"
                                f"?key=%2Flibrary%2Fmetadata%2F{server_id}")
                self.set_status("")
            run_in_thread(worker)


def _attribution(verb: str, by, ts) -> str:
    """Sufijo de tooltip "\\n<verb> por X el <fecha>." con lo que conste."""
    by = (by or "").strip()
    date = ""
    try:
        ts = int(ts or 0)
        if ts > 0:
            import datetime as _dt
            date = _dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        date = ""
    if by and date:
        return f"\n{verb} por {by} el {date}."
    if by:
        return f"\n{verb} por {by}."
    if date:
        return f"\n{verb} el {date}."
    return ""
