"""
Configuración sin interfaz: guardar y aplicar los ajustes en caliente,
sincronizar/publicar/descartar la configuración compartida del servidor,
exportar/importar la de cliente, el traslado único de datos compartidos y del
autoarranque tras el cambio de nombre, el autoarranque por plataforma y el
aviso de versión nueva. Mixin que hereda QtAppCore
(gui_qt/core_host.py).

Ganchos de interfaz: _error_dialog/_warn_dialog/_ask_yes_no, _ask_save_path/
_ask_open_path, _make_rename_reservations_dialog, _make_confirm_dialog,
_on_server_config_applied, _on_config_imported, _show_update_dialog,
_invalidate_missing_ep_detail_frames.
"""

import json
import os
import sys
import threading
from pathlib import Path

from config import CONFIG_EXPORT_SCHEMA_VERSION, INTERNAL_FLAGS
from core import shared_data
from core.appdirs import APP_NAME, LEGACY_APP_NAME, is_linux, is_macos, is_windows
from core.applog import get_logger
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from core.version import __version__

_log = get_logger("aIBechos.gui", "app.log")


class SettingsCoreMixin:
    _MACOS_LAUNCH_AGENT_LABEL = "com.aibechos.app"
    _OLD_MACOS_LAUNCH_AGENT_LABEL = "com.arenombrar.app"
    _LINUX_AUTOSTART_FILENAME = "aibechos-autostart.desktop"
    _OLD_LINUX_AUTOSTART_FILENAME = "arenombrar-autostart.desktop"

    def _migrate_shared_data_folder(self):
        """Trae los datos que el grupo comparte a la carpeta y los nombres
        nuevos, una sola vez, tras el cambio de nombre de la aplicación.

        Se COPIA, no se mueve: los archivos antiguos se quedan donde están, así
        que quien todavía no haya actualizado sigue trabajando con normalidad.

        No se pisa nada que ya exista en el destino. Esa es la salvaguarda
        importante: si otro usuario actualiza más tarde que tú, su migración no
        machacará con datos viejos lo que tú ya hayas escrito en la carpeta
        nueva.

        Si la conexión falla no se marca nada, así que se reintenta en el
        siguiente arranque."""
        if self.config_data.get("_shared_data_migrated"):
            return
        carpeta_antigua = self.config_data.get("shared_data_ftp_path", "").strip()
        if not carpeta_antigua:
            self.config_data.set("_shared_data_migrated", True)   # nada que traer
            self.config_data.save()
            return
        carpeta_nueva = shared_data.compute_new_folder(carpeta_antigua)

        def worker():
            own_ftp = self._new_ftp_client()
            copiados = 0
            try:
                ok, _msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    return
                for clave in shared_data.SHARED_DATA_FILES:
                    destino = f"{carpeta_nueva.rstrip('/')}/{shared_data.filename(clave)}"
                    if own_ftp.file_exists(destino):
                        continue          # ya lo trajo alguien: no se toca
                    origen = (f"{carpeta_antigua.rstrip('/')}/"
                              f"{shared_data.filename(clave, legacy=True)}")
                    raw = own_ftp.download_bytes(origen)
                    if raw is None:
                        continue          # ese archivo no existía; es normal
                    ok_subida, _ = own_ftp.upload_bytes(raw, destino)
                    if ok_subida:
                        copiados += 1
            except Exception as e:
                _log.warning("Cambio de nombre: no se pudieron traer los datos "
                             "compartidos (se reintentará): %s", e)
                return
            finally:
                own_ftp.disconnect()
            self.after(0, lambda: self._finish_shared_data_migration(
                carpeta_nueva, copiados))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_shared_data_migration(self, carpeta_nueva: str, copiados: int):
        self.config_data.set("shared_data_ftp_path", carpeta_nueva)
        self.config_data.set("_shared_data_migrated", True)
        self.config_data.save()
        _log.info("Cambio de nombre: %d archivo(s) compartido(s) traído(s) a '%s'",
                  copiados, carpeta_nueva)
        # Aplicar ya la configuración compartida desde su sitio nuevo, para no
        # arrancar esta sesión con la de antes del cambio.
        self._sync_server_config_from_ftp()

    def _server_config_remote_path(self) -> str:
        """Ruta remota de la configuración compartida del servidor (ver
        core/server_config.py) -- archivo propio dentro de la misma
        carpeta compartida, mismo motivo que _reservations_remote_path."""
        return self._shared_data_path(shared_data.filename("config_servidor"))

    def _sync_server_config_from_ftp(self):
        """Descarga la configuración compartida del servidor y la aplica en
        local (TMDB/IA, plantillas, categorías, servidores de medios,
        enlaces, cuota de reservas... ver
        core/server_config.py::SHARED_CONFIG_KEYS) -- se llama una vez al
        arrancar. Silencioso si no hay ruta configurada, la descarga falla,
        o el archivo remoto todavía no existe (nadie lo ha publicado
        nunca): los valores locales se quedan como estaban, no es un
        error."""
        remote_path = self._server_config_remote_path()
        if not remote_path:
            return

        def worker():
            from core.server_config import filter_shared_config
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
                updates = filter_shared_config(remote_data)
                # learned_junk_terms no es una clave de Config -- vive en
                # core/learned_terms.py, se gestiona aparte (igual que en
                # _export_config/_import_config).
                learned_terms = remote_data.get("learned_junk_terms")
                if updates or learned_terms is not None:
                    self.after(0, lambda: self._apply_synced_server_config(updates, learned_terms))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _apply_synced_server_config(self, updates: dict, learned_terms=None, force: bool = False):
        """force=True (ver _discard_local_server_config) aplica *updates*
        tal cual, sin proteger ninguna clave -- el usuario ya confirmó
        explícitamente que quiere descartar TODOS sus cambios locales sin
        publicar y adoptar la del servidor. force=False (el sync
        silencioso de arranque) sí protege claves con cambios locales sin
        publicar, ver más abajo."""
        from core.server_config import load_last_synced_snapshot, save_last_synced_snapshot, diff_local_changes
        last_synced = {} if force else load_last_synced_snapshot()
        if last_synced:
            # No pisar en silencio una clave que el usuario ya cambió en
            # local desde el último sync/publish y todavía no ha
            # publicado (bug real: un enlace personalizable nuevo,
            # guardado pero sin publicar, desaparecía al reiniciar
            # porque este sync sobrescribía todo sin distinguir) -- se
            # queda tal cual está en local hasta que decida publicarla o
            # descartarla a mano (ver _discard_local_server_config, que
            # SÍ sobrescribe sin distinguir, porque ahí lo pide
            # explícitamente).
            locally_changed = diff_local_changes(self.config_data.get, last_synced)
        else:
            # Primera vez que se sincroniza en este cliente (o versión
            # anterior a este arreglo, sin snapshot todavía) -- sin base
            # fiable de comparación, se aplica todo tal cual siempre se
            # hizo; el snapshot que se guarda al final ya deja
            # preparada la protección para el PRÓXIMO sync.
            locally_changed = set()
        to_apply = {k: v for k, v in updates.items() if k not in locally_changed}
        skipped = locally_changed & updates.keys()
        if to_apply:
            self.config_data.set_many(to_apply)
            self.config_data.save()
            # Las listas de proveedores alimentan al scoring en memoria
            # (ver _apply_provider_lists): sin esto, lo recién sincronizado
            # no se aplicaba hasta reiniciar la app.
            self._apply_provider_lists(self.config_data)
        # El snapshot se actualiza con TODO lo descargado, se haya
        # aplicado o no -- así, si el usuario publica más tarde o su
        # valor local vuelve a coincidir con el remoto, deja de
        # protegerse sin necesidad para siempre.
        save_last_synced_snapshot({**last_synced, **updates})
        if learned_terms is not None:
            from core.learned_terms import set_learned_terms
            set_learned_terms(learned_terms)
        # El panel de Ajustes se construye diferido (ver _build_ui) -- si
        # el usuario nunca lo ha abierto todavía, no hay widgets que
        # refrescar; se leerán ya actualizados de self.config_data la
        # primera vez que se construya.
        if to_apply:
            self._on_server_config_applied()
        if skipped:
            self._set_status(
                f"Configuración de servidor actualizada ({len(to_apply)} parámetro(s)) -- "
                f"{len(skipped)} sin tocar porque tienes cambios locales sin publicar", WARNING_COLOR)
        elif to_apply:
            self._set_status(
                f"Configuración de servidor actualizada ({len(to_apply)} parámetro(s))", PENDING_COLOR)

    def _discard_local_server_config(self):
        """Botón "Descartar cambios y recuperar del servidor" en Ajustes →
        Servidor -- a diferencia de _sync_server_config_from_ftp (que se
        llama en silencio al arrancar y no dice nada si falla, para no
        molestar con un error en cada inicio), esto es una acción
        explícita del usuario: SIEMPRE informa del resultado, éxito o
        error, incluyendo el caso de que nadie haya publicado nunca una
        configuración."""
        remote_path = self._server_config_remote_path()
        if not remote_path:
            self._warn_dialog(
                "Falta la carpeta compartida",
                "Configura \"Carpeta compartida (datos)\" en Ajustes → Cliente → Conexión FTP "
                "antes de recuperar la configuración del servidor.")
            return
        if not self._make_confirm_dialog(
                "Descartar configuración local",
                "¿Descartar tu configuración local y recuperar la del servidor?",
                "Esto sustituirá tu configuración local de esta pestaña (TMDB/IA -- incluidas las "
                "claves de API --, plantillas de nombre, categorías FTP, Plex/Jellyfin -- incluidos "
                "sus tokens --, enlaces y la cuota de reservas) por la última publicada en el "
                "servidor, descartando cualquier cambio sin publicar que tengas aquí.",
                confirm_text="Sí, descartar y recuperar", confirm_color=ERROR_COLOR).result:
            return

        self._set_status("Recuperando configuración del servidor...", PENDING_COLOR)

        def worker():
            from core.server_config import filter_shared_config
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    self.after(0, lambda: self._set_status(f"No se pudo conectar: {msg}", ERROR_COLOR))
                    return
                raw = own_ftp.download_bytes(remote_path)
                if raw is None:
                    self.after(0, lambda: self._set_status(
                        "Nadie ha publicado todavía una configuración de servidor", WARNING_COLOR))
                    return
                try:
                    remote_data = _json.loads(raw.decode("utf-8"))
                except ValueError:
                    self.after(0, lambda: self._set_status(
                        "El archivo de configuración del servidor está corrupto", ERROR_COLOR))
                    return
                if not isinstance(remote_data, dict):
                    self.after(0, lambda: self._set_status(
                        "El archivo de configuración del servidor tiene un formato inesperado", ERROR_COLOR))
                    return
                updates = filter_shared_config(remote_data)
                learned_terms = remote_data.get("learned_junk_terms")
                self.after(0, lambda: self._apply_synced_server_config(updates, learned_terms, force=True))
            finally:
                own_ftp.disconnect()
        threading.Thread(target=worker, daemon=True).start()

    def _publish_server_config(self):
        """Sube la configuración de ESTE equipo como la configuración
        compartida del servidor -- a diferencia de favoritos/reservas
        (que se fusionan), esto es una publicación deliberada que
        SOBRESCRIBE por completo lo que hubiera, así que pide confirmación
        explícita: afecta a cualquier otra persona que use aIBechos
        contra este mismo servidor la próxima vez que arranque. Incluye
        credenciales (TMDB/IA/Plex/Jellyfin) a propósito -- ver la nota de
        seguridad en core/server_config.py: viajan en texto plano dentro
        del JSON del FTP, protegidas solo por la contraseña FTP."""
        remote_path = self._server_config_remote_path()
        if not remote_path:
            self._warn_dialog(
                "Falta la carpeta compartida",
                "Configura \"Carpeta compartida (datos)\" en Ajustes → Cliente → Conexión FTP "
                "antes de publicar la configuración del servidor.")
            return
        if not self._make_confirm_dialog(
                "Publicar configuración del servidor",
                "¿Publicar la configuración de este equipo como la del servidor?",
                "Esto sobrescribirá la configuración compartida (TMDB/IA -- incluidas las claves de "
                "API --, plantillas de nombre, categorías FTP, Plex/Jellyfin -- incluidos sus tokens --, "
                "enlaces y la cuota de reservas) con la de este equipo. Cualquier otra persona que use "
                "aIBechos contra este servidor la adoptará la próxima vez que abra la app.",
                confirm_text="Sí, publicar").result:
            return

        from core.server_config import extract_shared_config
        from core.learned_terms import load_learned_terms
        data = extract_shared_config(self.config_data.get)
        data["learned_junk_terms"] = load_learned_terms()
        self._set_status("Publicando configuración del servidor...", PENDING_COLOR)

        def worker():
            import json as _json
            own_ftp = self._new_ftp_client()
            try:
                ok, msg = own_ftp.connect(
                    self.config_data.get("ftp_host", ""),
                    int(self.config_data.get("ftp_port", 21)),
                    self.config_data.get("ftp_user", ""),
                    self.config_data.get("ftp_password", ""),
                    self.config_data.get("ftp_use_tls", False))
                if not ok:
                    self.after(0, lambda: self._set_status(f"No se pudo conectar: {msg}", ERROR_COLOR))
                    return
                payload = _json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                up_ok, up_msg = own_ftp.upload_bytes(payload, remote_path)
            finally:
                own_ftp.disconnect()
            if up_ok:
                # Local YA es la configuración publicada -- actualizar el
                # snapshot de referencia para que el próximo sync de
                # arranque no la trate como "cambio local sin publicar"
                # (ver _apply_synced_server_config).
                from core.server_config import save_last_synced_snapshot
                save_last_synced_snapshot(data)
                self.after(0, lambda: self._set_status("Configuración de servidor publicada", SUCCESS_COLOR))
            else:
                self.after(0, lambda: self._set_status(f"No se pudo publicar: {up_msg}", ERROR_COLOR))
        threading.Thread(target=worker, daemon=True).start()

    def _check_for_updates_at_startup(self):
        """Comprueba en segundo plano si hay una versión más nueva publicada
        en GitHub Releases (core/update_check.py) y, si la hay y el usuario
        no la saltó ya, muestra un aviso para ir a la página de la release.
        Nunca descarga ni reemplaza el ejecutable en sitio. Silencioso si la
        consulta falla -- no es una acción que el usuario haya pedido
        explícitamente."""
        def worker():
            from core.update_check import check_for_update
            result = check_for_update(__version__)
            if result is None:
                return
            tag, html_url = result
            if tag == self.config_data.get("skipped_update_version", ""):
                return
            self.after(0, lambda: self._show_update_dialog(tag, html_url))
        threading.Thread(target=worker, daemon=True).start()

    def _export_config(self):
        path = self._ask_save_path("Exportar configuración", "aIBechos_config.json")
        if not path:
            return
        from core.server_config import SHARED_CONFIG_KEYS
        # Solo configuración de CLIENTE -- la de servidor (incluidos los
        # términos aprendidos del fallback de IA, que tampoco viven en
        # config.json) tiene su propio mecanismo de sincronizar/publicar
        # (ver _sync_server_config_from_ftp/_publish_server_config);
        # meterla también aquí sería redundante y confuso, dos caminos
        # distintos para cambiar lo mismo.
        data = {k: v for k, v in self.config_data.to_dict().items()
                if k not in SHARED_CONFIG_KEYS and k not in INTERNAL_FLAGS}
        # Metadatos de versión: permiten avisar al importar si el archivo
        # viene de una versión de aIBechos más nueva que podría usar un
        # formato que esta versión no entiende del todo.
        data["_export_schema_version"] = CONFIG_EXPORT_SCHEMA_VERSION
        data["_app_version"] = __version__
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            self._set_status(f"Error al exportar: {e}", ERROR_COLOR)
            return
        self._set_status(f"Configuración exportada: {Path(path).name}", SUCCESS_COLOR)

    def _import_config(self):
        path = self._ask_open_path("Importar configuración")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._set_status(f"Error al leer el archivo: {e}", ERROR_COLOR)
            return
        if not isinstance(data, dict):
            self._set_status("Archivo de configuración inválido", ERROR_COLOR)
            return

        # Archivos exportados antes de este campo (sin _export_schema_version)
        # se tratan como versión 1 -- es la única que ha existido hasta ahora.
        file_schema  = data.pop("_export_schema_version", 1)
        file_app_ver = data.pop("_app_version", None)
        if file_schema > CONFIG_EXPORT_SCHEMA_VERSION:
            if not self._ask_yes_no(
                    "Archivo de una versión más nueva",
                    f"Este archivo se exportó con una versión de aIBechos más "
                    f"reciente que esta{f' (v{file_app_ver})' if file_app_ver else ''} "
                    f"y puede usar un formato que esta versión no entiende del todo. "
                    f"¿Importar de todas formas?"):
                return

        if not self._ask_yes_no(
                "Importar configuración",
                "Esto sobrescribirá tu configuración de CLIENTE actual (conexión FTP, carpeta "
                "vigilada, preferencias de este equipo... excepto la contraseña FTP, que deberás "
                "volver a introducir). No toca la configuración de servidor. ¿Continuar?"):
            return
        data.pop("ftp_password", None)   # nunca importar contraseñas en texto plano
        # Solo configuración de CLIENTE -- un archivo exportado con una
        # versión anterior de la app (antes de este cambio) podía traer
        # claves de servidor mezcladas; se descartan aquí igual que si
        # nunca hubieran estado en el archivo (ver _export_config).
        from core.server_config import SHARED_CONFIG_KEYS
        data.pop("learned_junk_terms", None)
        data = {k: v for k, v in data.items() if k not in SHARED_CONFIG_KEYS}
        # Mismo paso de validación que al guardar Ajustes a mano (ver
        # _save_all_settings) -- sin esto, importar una configuración con
        # un "Tu nombre" distinto cambiaba de identidad en silencio, sin
        # comprobar que el nombre no estuviera ya en uso ni preguntar qué
        # hacer con las reservas del nombre anterior.
        if "app_user_name" in data:
            data["app_user_name"] = self._resolve_app_user_name_change(data["app_user_name"])
        self.config_data.set_many(data)
        self.config_data.save()
        self._on_config_imported()
        self._set_status("Configuración importada", SUCCESS_COLOR)

    def _resolve_app_user_name_change(self, new_name: str) -> str:
        """Valida el cambio de "Tu nombre" ANTES de guardar nada -- llamado
        desde _save_all_settings. Puede devolver un nombre distinto al
        pedido (revertido al anterior) si el usuario cancela alguno de los
        pasos, para que _save_all_settings no lo persista.

        Dos comprobaciones, en este orden:
        1. Unicidad: ¿ya hay reservas de otra persona con ese mismo
           nombre? Bloquea tanto crear el nombre por primera vez como
           cambiarlo -- compartir nombre mezclaría cuotas y podría
           impedir liberar una reserva ajena por confundirla con propia.
        2. Si había un nombre anterior CON reservas propias, preguntar qué
           hacer con ellas: traspasarlas al nombre nuevo o desprotegerlas
           todas (_RenameReservationsDialog). Cancelar aquí revierte el
           nombre entero, no solo esta comprobación."""
        old_name = self.config_data.get("app_user_name", "").strip()
        if new_name == old_name:
            return new_name

        from core.reservations import is_name_taken, used_bytes
        if new_name and is_name_taken(self._reservations, new_name, exclude=old_name or None):
            self._error_dialog(
                "Nombre en uso",
                f"Ya hay reservas hechas por alguien llamado \"{new_name}\" -- elige un nombre "
                "distinto para no mezclar tu cuota de reservas con la suya.")
            return old_name

        if old_name and used_bytes(self._reservations, old_name) > 0:
            dlg = self._make_rename_reservations_dialog(old_name, new_name)
            if dlg.result == "transfer":
                self._transfer_reservations(old_name, new_name)
                self._transfer_auto_owners(old_name, new_name)
            elif dlg.result == "unprotect":
                self._unprotect_all_reservations(old_name)
                self._remove_all_auto_owners(old_name)
            else:
                return old_name
        elif old_name and new_name and new_name != old_name:
            # Sin reservas pero con posible propiedad del rayo a cuestas:
            # viaja con el nombre igual que las reservas (ver
            # core/auto_series.py); si no, el nombre viejo quedaría como
            # dueño fantasma para siempre.
            self._transfer_auto_owners(old_name, new_name)

        return new_name

    def _apply_provider_lists(self, data) -> None:
        """Pasa las listas de descargas (Ajustes → Servidor → Preferencias
        descargas: Proveedores, Idioma, Otros filtros y Puntuación) al
        scoring. *data* es config_data o el dict recién recogido de
        Ajustes (ambos responden a .get)."""
        try:
            from core.download_quality import (
                set_provider_lists, set_filter_lists, set_score_weights,
                LANG_CONFIG_KEYS)
            if hasattr(data, "get"):
                trusted = data.get("p2p_trusted_groups", [])
                blocked = data.get("p2p_blocked_groups", [])
                langs = {kind: data.get(ckey, []) for kind, ckey in LANG_CONFIG_KEYS.items()}
                adult = data.get("p2p_blocked_adult", [])
                sample = data.get("p2p_blocked_sample", [])
                scr = data.get("p2p_blocked_scr", [])
                exts = data.get("p2p_blocked_exts", [])
                weights = data.get("p2p_score_weights", {})
            else:
                trusted, blocked = [], []
                langs, adult, sample, scr, exts, weights = {}, [], [], [], [], {}
            set_provider_lists(trusted, blocked)
            set_filter_lists(langs, adult, sample, scr, exts)
            set_score_weights(weights)
        except Exception:
            pass

    def _commit_settings(self, data: dict) -> dict:
        """Guarda y aplica en caliente los ajustes recogidos de la interfaz
        (*data*, solo las claves de las sub-pestañas construidas). Devuelve
        *data* tal como quedó guardado: "Tu nombre" puede volver al anterior
        si el cambio se cancela (ver _resolve_app_user_name_change)."""
        cfg = self.config_data.get
        if "app_user_name" in data:
            data["app_user_name"] = self._resolve_app_user_name_change(data["app_user_name"])
        self.config_data.set_many(data)
        self.config_data.save()
        self._apply_provider_lists({**{k: cfg(k) for k in (
            "p2p_trusted_groups", "p2p_blocked_groups", "p2p_lang_vos",
            "p2p_lang_fr", "p2p_lang_it", "p2p_lang_de", "p2p_lang_pt",
            "p2p_lang_ca", "p2p_blocked_adult", "p2p_blocked_sample",
            "p2p_blocked_scr", "p2p_blocked_exts", "p2p_score_weights")}, **data})
        self._set_autostart(data.get("start_with_windows", cfg("start_with_windows")))
        self.tmdb.set_api_key(data.get("tmdb_api_key", cfg("tmdb_api_key", "")))
        self.tmdb.set_language(data.get("language", cfg("language", "es-ES")))
        # Sin esto, self.comicvine/self.book_client (creados una sola vez en
        # __init__) se quedaban con la key vieja hasta reiniciar la app --
        # el "Guardar" la persistía en config.json, pero el cliente en
        # memoria seguía usando la de antes (vacía, para Google Books, hasta
        # que el usuario configuraba una) durante toda la sesión. Bug real:
        # tras poner una API Key de Google Books y guardar, las búsquedas
        # seguían fallando por la cuota anónima porque la key nunca llegó
        # al cliente ya en uso.
        self.comicvine.set_api_key(data.get("comicvine_api_key", cfg("comicvine_api_key", "")))
        self.book_client.set_api_key(data.get("google_books_api_key", cfg("google_books_api_key", "")))
        if self._watcher and self._watcher.running and "poll_interval" in data:
            self._watcher.poll_interval = data["poll_interval"]
        self._invalidate_missing_ep_detail_frames()
        self._set_status("✓ Configuración guardada", SUCCESS_COLOR)
        return data

    def _set_autostart(self, enabled: bool):
        """Añade o elimina el arranque automático al iniciar sesión —
        registro de Windows, LaunchAgent de macOS, o .desktop de
        autoarranque XDG en Linux, según la plataforma."""
        if is_windows():
            self._set_autostart_windows(enabled)
        elif is_macos():
            self._set_autostart_macos(enabled)
        elif is_linux():
            self._set_autostart_linux(enabled)

    def _migrate_autostart_identity(self):
        """Pasa el arranque automático al nombre nuevo, una sola vez.

        Solo actúa si el usuario YA lo tenía puesto: la presencia de la entrada
        antigua es la señal, así que a quien no lo usara no se le activa nada.

        Se vuelve a crear con `_set_autostart_*(True)` en vez de copiar la
        entrada vieja tal cual, porque esa apunta al ejecutable con el nombre
        y la carpeta anteriores -- y tras actualizar puede que ya no exista.
        Sin esto quedaría además una entrada huérfana lanzando un programa que
        no está."""
        if self.config_data.get("_autostart_identity_migrated"):
            return
        try:
            if is_windows():
                self._migrate_autostart_windows()
            elif is_macos():
                self._migrate_autostart_macos()
            elif is_linux():
                self._migrate_autostart_linux()
        except Exception as e:
            _log.warning("Cambio de nombre: no se pudo migrar el arranque "
                         "automático: %s", e)
        self.config_data.set("_autostart_identity_migrated", True)
        self.config_data.save()

    def _migrate_autostart_windows(self):
        import winreg
        ruta = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ruta, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            try:
                winreg.QueryValueEx(key, LEGACY_APP_NAME)
            except FileNotFoundError:
                return              # no tenía arranque automático
            winreg.DeleteValue(key, LEGACY_APP_NAME)
        self._set_autostart_windows(True)
        _log.info("Cambio de nombre: arranque automático trasladado a '%s'", APP_NAME)

    def _migrate_autostart_macos(self):
        import subprocess
        antiguo = (Path.home() / "Library" / "LaunchAgents" /
                   f"{self._OLD_MACOS_LAUNCH_AGENT_LABEL}.plist")
        if not antiguo.exists():
            return
        # Descargarlo antes de borrarlo: si no, el agente viejo sigue vivo en
        # la sesión de launchd hasta el próximo inicio de sesión.
        subprocess.run(["launchctl", "unload", "-w", str(antiguo)],
                       capture_output=True)
        antiguo.unlink()
        self._set_autostart_macos(True)
        _log.info("Cambio de nombre: LaunchAgent trasladado a '%s'",
                  self._MACOS_LAUNCH_AGENT_LABEL)

    def _migrate_autostart_linux(self):
        antiguo = (Path.home() / ".config" / "autostart" /
                   self._OLD_LINUX_AUTOSTART_FILENAME)
        if not antiguo.exists():
            return
        antiguo.unlink()
        self._set_autostart_linux(True)
        _log.info("Cambio de nombre: arranque automático trasladado a '%s'",
                  self._LINUX_AUTOSTART_FILENAME)

    def _set_autostart_windows(self, enabled: bool):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE)
            if enabled:
                if getattr(sys, "frozen", False):
                    exe_cmd = f'"{sys.executable}" --minimized'
                else:
                    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
                    exe_cmd = f'"{sys.executable}" "{main_py}" --minimized'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            self._set_status(f"Error registro: {e}", ERROR_COLOR)

    def _macos_launch_agent_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self._MACOS_LAUNCH_AGENT_LABEL}.plist"

    def _set_autostart_macos(self, enabled: bool):
        """Equivalente macOS del registro de Windows: un LaunchAgent de
        usuario (~/Library/LaunchAgents/*.plist) con RunAtLoad, cargado con
        "launchctl load -w" para que también tenga efecto inmediato, no solo
        en el próximo inicio de sesión."""
        import plistlib
        import subprocess
        plist_path = self._macos_launch_agent_path()
        try:
            if enabled:
                if getattr(sys, "frozen", False):
                    args = [sys.executable, "--minimized"]
                else:
                    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
                    args = [sys.executable, main_py, "--minimized"]
                plist = {
                    "Label": self._MACOS_LAUNCH_AGENT_LABEL,
                    "ProgramArguments": args,
                    "RunAtLoad": True,
                }
                plist_path.parent.mkdir(parents=True, exist_ok=True)
                with open(plist_path, "wb") as f:
                    plistlib.dump(plist, f)
                subprocess.run(["launchctl", "load", "-w", str(plist_path)],
                                capture_output=True)
            elif plist_path.exists():
                subprocess.run(["launchctl", "unload", "-w", str(plist_path)],
                                capture_output=True)
                plist_path.unlink()
        except Exception as e:
            self._set_status(f"Error LaunchAgent: {e}", ERROR_COLOR)

    def _linux_autostart_path(self) -> Path:
        return Path.home() / ".config" / "autostart" / self._LINUX_AUTOSTART_FILENAME

    def _set_autostart_linux(self, enabled: bool):
        """Equivalente Linux del registro de Windows/LaunchAgent de macOS:
        un archivo .desktop en ~/.config/autostart/ -- la convención XDG
        (freedesktop.org) que GNOME/KDE/XFCE y la mayoría de entornos de
        escritorio ya respetan de fábrica, sin depender de una unidad de
        systemd ni de nada específico de una distribución concreta."""
        path = self._linux_autostart_path()
        try:
            if enabled:
                if getattr(sys, "frozen", False):
                    exec_cmd = f'"{sys.executable}" --minimized'
                else:
                    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
                    exec_cmd = f'"{sys.executable}" "{main_py}" --minimized'
                content = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={APP_NAME}\n"
                    f"Exec={exec_cmd}\n"
                    "X-GNOME-Autostart-enabled=true\n"
                    "Terminal=false\n"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            elif path.exists():
                path.unlink()
        except Exception as e:
            self._set_status(f"Error autoarranque: {e}", ERROR_COLOR)
