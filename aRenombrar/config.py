"""
Gestión de configuración persistente (JSON en carpeta del usuario).
"""

import json
from pathlib import Path

import keyring

from core.appdirs import APP_NAME, LEGACY_APP_NAME, app_data_dir

# Claves que se guardan en el almacén de credenciales del sistema (keyring)
# en vez de en config.json en texto plano. El nombre de la clave en config
# y en keyring es el mismo en los cuatro casos.
_KEYRING_KEYS = ("ftp_password", "ai_api_key", "plex_token", "jellyfin_api_key")

# Marcas internas de "esta migración ya se hizo". SÍ se guardan en config.json
# (si no, la migración se repetiría en cada arranque), pero NO deben viajar al
# exportar la configuración: llevarlas a otro equipo le haría saltarse una
# migración que allí no se ha hecho nunca, y ese equipo se quedaría sin
# contraseña o sin sus datos compartidos sin motivo aparente.
INTERNAL_FLAGS = (
    "_keyring_service_migrated",
    "_shared_data_migrated",
    "_autostart_identity_migrated",
)

# Versión del FORMATO de exportación/importación de configuración (gui/app.py
# ::_export_config/_import_config) -- distinta de la versión de la app
# (core/version.py): esta solo sube cuando cambia la ESTRUCTURA del archivo
# exportado de forma que una versión más vieja de aIBechos no sepa
# interpretarla correctamente (p.ej. una clave que cambia de significado o
# se divide en varias) -- añadir una clave nueva opcional NO cuenta, eso ya
# lo absorbe set_many() sin problema.
CONFIG_EXPORT_SCHEMA_VERSION = 1

DEFAULTS = {
    "tmdb_api_key": "",
    "language": "es-ES",
    # Nombre: "Breaking Bad 3x07 One Minute.mkv"
    "tv_template": "{serie} {temporada}x{episodio:02d} {titulo}{ext}",
    # Nombre: "The Dark Knight (2008).mkv"
    "movie_template": "{serie} ({año}){ext}",
    # Anime igual que TV pero episodio con 3 dígitos
    "anime_template": "{serie} {temporada}x{episodio:03d} {titulo}{ext}",
    # Libros (ebooks de texto vía Google Books): sin número de volumen
    # fiable, así que el nombre por defecto es solo el título.
    # Nombre: "El Nombre del Viento.epub"
    "libro_template": "{serie}{ext}",
    # Cómics/manga vía ComicVine: a diferencia del ebook, el número de
    # emisión SÍ es fiable -- se extrae del propio nombre de archivo
    # (patrón "#NN", ver detect_episode en core/api_client.py) igual que
    # ya se hace con el episodio de una serie. Formato confirmado contra
    # cómics reales ya en el servidor del usuario ("Avatar - The Last
    # Airbender - The Promise (2012) #01.cbr"): título en inglés (el que
    # da ComicVine), año, y "#" + número con 2 dígitos.
    # Nombre: "Avatar - The Last Airbender - The Promise (2012) #01.cbr"
    "comic_template": "{serie} ({año}) #{episodio:02d}{ext}",
    # "ftp" (con ftp_use_tls para FTPS explícito) o "sftp" (SSH, puerto 22).
    # SFTP no es FTP cifrado sino otro protocolo distinto, ver
    # core/sftp_client.py -- por eso es un ajuste aparte y no otro interruptor
    # junto a ftp_use_tls.
    "ftp_protocol": "ftp",
    "ftp_host": "",
    "ftp_port": 21,
    "ftp_user": "",
    "ftp_password": "",
    "ftp_use_tls": False,
    "ftp_path_template": "/datos2/series/{serie}/Temporada {temporada:02d}/",
    "ftp_movie_path_template": "",
    "appearance": "dark",
    "color_theme": "blue",
    # Estado de la ventana principal al cerrar (se restaura al arrancar):
    # "window_geometry" es "WxH+X+Y" y "window_maximized" indica si estaba
    # maximizada -- ver _save_window_state/_apply_window_state en gui/app.py.
    "window_geometry": "",
    "window_maximized": False,
    "last_dir": "",
    "watch_folder": "",
    "poll_interval": 10,
    "auto_action": "Mantener original",
    "manual_action": "Mantener original",
    # Descomprimir .zip/.7z/.rar/.tar (y variantes comprimidas de tar)
    # encontrados en la carpeta vigilada (ver core/archive_extract.py) --
    # desactivado por defecto porque crea/mueve/borra archivos sin
    # confirmación, a diferencia de solo identificar.
    "auto_extract_archives": False,
    "ftp_parallel": 1,
    # Cuántas conexiones se reparten UN MISMO archivo. El servidor limita cada
    # conexión por separado (~2 MB/s medidos), así que con una sola no se llega
    # al límite de velocidad configurado por muy alto que esté. Es un
    # presupuesto total, no por archivo: si se suben varios a la vez, se
    # reparte entre ellos (ver App._streams_for_upload). Solo aplica a SFTP.
    "ftp_upload_streams": 4,
    "ftp_speed_limit": 0,
    "ftp_retries": 3,
    "desktop_notifications": True,
    "start_with_windows": False,
    "min_confidence": 70,
    "rename_local": True,
    "rename_remote": True,
    # Fallback de IA (Groq) cuando TMDB no encuentra resultados con el
    # título limpiado localmente — desactivado por defecto, no se envía
    # nada a terceros hasta que el usuario lo active explícitamente.
    "ai_fallback_enabled": False,
    "ai_api_key": "",
    # Identificación de cómics/manga (core/comicvine_client.py) -- credencial
    # compartida por el grupo igual que tmdb_api_key (ver
    # core/server_config.py), NO por máquina como ai_api_key/plex_token/
    # jellyfin_api_key: por eso NO está en _KEYRING_KEYS. ComicVine, a
    # diferencia de Google Books, exige API key en cada petición.
    "comicvine_api_key": "",
    # Google Books (ebooks) SÍ funciona sin key -- pero la cuota anónima es
    # muy ajustada y compartida globalmente entre cualquiera que llame sin
    # una, así que puede saturarse (429) incluso con una sola búsqueda si
    # otros usuarios la están agotando en ese momento. Una key gratuita
    # propia (console.cloud.google.com, activar "Books API") da cuota per-
    # proyecto en vez de la anónima compartida.
    "google_books_api_key": "",
    # Refresco de biblioteca en Plex/Jellyfin tras subir -- cada uno
    # independiente, se puede activar solo uno, los dos, o ninguno.
    "plex_enabled": False,
    "plex_host": "",
    "plex_token": "",
    "jellyfin_enabled": False,
    "jellyfin_host": "",
    "jellyfin_api_key": "",
    # Usuario de Jellyfin cuyo historial de visionado se consulta (vacío =
    # el primero que devuelva el servidor) -- en un servidor con un solo
    # usuario da igual, pero en uno compartido/familiar el primero de la
    # lista puede ser la cuenta de administrador, que normalmente no ve
    # nada -- necesario para que "Liberar espacio" no marque como "nunca
    # vista" contenido que sí se ha visto, solo que con OTRO usuario.
    "jellyfin_username": "",
    # Enlaces personalizables desde el detector de episodios que faltan y
    # los paneles de detalle de Archivos/Liberar espacio -- solo
    # informativos (ver core/custom_links.py): abren una URL con variables
    # sustituidas, nunca se conectan a nada por su cuenta. Una lista
    # independiente por nivel (serie/temporada/episodio/película) porque
    # cada uno tiene sentido con una plantilla distinta -- p.ej. la ficha
    # de TMDB cambia de "/tv/{tmdb_id}" a ".../season/{temporada}" a
    # ".../season/{temporada}/episode/{episodio}" según el nivel, y las
    # películas usan "/movie/{tmdb_id}" en vez de "/tv/...". "movie" es su
    # propio nivel (no reutiliza "episode") porque {temporada}/{episodio}
    # no existen para una película y una plantilla mixta quedaría rota a
    # medias.
    "custom_links_show": [
        {"name": "Ver en TMDB", "url_template": "https://www.themoviedb.org/tv/{tmdb_id}"},
    ],
    "custom_links_season": [
        {"name": "Ver en TMDB", "url_template": "https://www.themoviedb.org/tv/{tmdb_id}/season/{temporada}"},
    ],
    "custom_links_episode": [
        {"name": "Ver en TMDB", "url_template":
            "https://www.themoviedb.org/tv/{tmdb_id}/season/{temporada}/episode/{episodio}"},
        {"name": "Enviar por WhatsApp", "url_template": "https://wa.me/?text={nombre_archivo}"},
    ],
    "custom_links_movie": [
        {"name": "Ver en TMDB", "url_template": "https://www.themoviedb.org/movie/{tmdb_id}"},
        {"name": "Enviar por WhatsApp", "url_template": "https://wa.me/?text={nombre_archivo}"},
    ],
    # Etiqueta de release (p.ej. "v1.2.0") que el usuario ya descartó en el
    # aviso de actualización al arrancar -- ver core/update_check.py. Vacío
    # = ninguna descartada todavía. No se vuelve a avisar de esa MISMA
    # versión, pero sí de una posterior.
    "skipped_update_version": "",
    # Ruta FTP (explícita, no deducida de ninguna categoría) donde se guarda
    # el JSON de favoritos compartido entre todos los clientes que apuntan
    # al mismo servidor -- ver core/favorites.py. Vacío = favoritos
    # deshabilitados (solo mirror local, sin sincronizar).
    "shared_data_ftp_path": "",
    # Nombre que identifica a esta persona frente a las demás que usan
    # aIBechos contra el mismo servidor -- clave de su cuota individual
    # de 100GB en "Liberar espacio" (ver core/reservations.py). Vacío =
    # no puede reservar espacio todavía (hace falta para saber a quién
    # cargarle la cuota).
    "app_user_name": "",
    # Anchos de columna de las tablas (Archivos/Episodios/Liberar espacio/
    # Historial), guardados al soltar un separador -- ver gui/table_view.py
    # y _save_table_col_widths en gui/app.py. {tabla: {columna: ancho_px}}.
    "table_col_widths": {},
    # Columnas ocultas de las tablas, guardadas desde el menú contextual
    # de la cabecera (clic derecho) -- ver TableView.set_hidden y
    # _save_hidden_columns en gui/app.py. {tabla: [claves_ocultas]}.
    "table_hidden_columns": {},
    # Orden elegido al pulsar una cabecera de una tabla (Archivos/Episodios/
    # Liberar espacio/Descargar), guardado para persistir entre sesiones --
    # ver _save_table_sort en gui/app.py. {tabla: {"key": cl, "asc": bool}}.
    "table_sort": {},
    # Cuota de reservas por persona, en GB (ver core/reservations.py) --
    # configuración de SERVIDOR (core/server_config.py): el mismo límite
    # para todo el que reserve espacio contra este servidor, no una
    # constante fija en el código.
    "reservation_quota_gb": 100,
    # Emparejamiento manual de usuarios Plex<->Jellyfin para la
    # sincronización de visionado (ver core/watch_sync.py) -- confirmado a
    # mano una vez por la persona que ejecuta la herramienta y reutilizado
    # en corridas siguientes; vacío = sin emparejar todavía, la utilidad
    # pedirá emparejar la primera vez que se abra. CLIENTE, no compartido
    # (ver core/server_config.py, sección "Excluido a propósito", para el
    # motivo: es conocimiento personal, y SHARED_CONFIG_KEYS se aplica sin
    # revisión en cada arranque, lo que rompería la garantía de revisión
    # humana antes de escribir que es la razón de ser de esta función).
    # [{"plex_user_id","plex_user_name","jellyfin_user_id","jellyfin_user_name"}, ...]
    "watch_sync_user_mappings": [],
    # Epoch de la última sincronización (manual o programada) -- sobre
    # todo informativo ("hace 3 días" en la pestaña), nunca se usa para
    # decidir QUÉ sincronizar (cada corrida compara el estado COMPLETO
    # actual de ambas plataformas, no un delta desde la última vez); sí
    # se usa para decidir si YA tocó hoy la sincronización programada
    # (ver App._check_watch_sync_schedule), comparando su fecha con la
    # de hoy -- así no se repite varias veces si el minuto programado se
    # vuelve a comprobar.
    "watch_sync_last_run_ts": 0,
    # Sincronización automática diaria a una hora fija -- a diferencia
    # del botón manual, SI escribe sin pedir confirmación (decisión
    # explícita del usuario: quiso que ocurriera sola, sabiendo que eso
    # renuncia a la revisión humana previa que tiene el resto de esta
    # función). watch_sync_schedule_time en formato "HH:MM", vacío =
    # sin programar. Ver App._start_watch_sync_scheduler.
    "watch_sync_schedule_enabled": False,
    "watch_sync_schedule_time": "",
    # Persistencia entre reinicios de los interruptores de "Episodios que
    # faltan" y del botón "⚡ Auto" -- por defecto todos apagados/ocultando
    # nada, igual que antes de tener esto, pero si el usuario los deja
    # activados se recuerda la próxima vez en vez de resetear siempre a lo
    # de fábrica. auto_watcher_running es el estado real del botón (se
    # actualiza en cada arranque/parada, no solo al cerrar) -- al arrancar
    # la app, si estaba a True, se reanuda el modo automático solo (ver
    # App._restore_auto_watcher_state), sustituyendo a la lógica anterior
    # que solo miraba si había una carpeta configurada.
    "missing_ep_show_ignored": False,
    "missing_ep_hide_ai_dismissed": False,
    "missing_ep_hide_no_dub": False,
    "missing_ep_pin_favorites": True,
    # "Ocultar completas": activado de fábrica (comportamiento de siempre --
    # la tabla solo lista series con huecos). Al apagarlo se añaden a la
    # lista las series del servidor que NO tienen ningún hueco, sacadas de
    # missing_episodes_cache.json (ver App._load_complete_series_from_cache),
    # para poder usar la pestaña como catálogo completo del servidor.
    "missing_ep_hide_complete": True,
    # "Ocultar ya en el servidor" de la pestaña "Películas": activado de
    # fábrica (lo normal es recomendar solo lo que NO está en el servidor).
    # Al apagarlo se añaden las películas que ya están en Plex/Jellyfin para
    # ver el catálogo entero de las listas de TMDB (ver
    # core/missing_movies.py::apply_in_server_filter).
    "missing_movies_hide_in_server": True,
    "missing_movies_watch_only": True,
    # Filtro de tipo de la pestaña "Recomendado": "all" (todo junto),
    # "movies" (solo películas) o "series" (solo series). Se elige con el
    # selector Todo/Películas/Series de la barra de filtros.
    "missing_movies_type_filter": "all",
    # Filtro de años de estreno de la pestaña "Recomendado": "1"/"2"/"3"/
    # "5"/"8"/"10" (últimos X años, contando hacia atrás desde el año actual)
    # o "Todos". Se elige con el selector "Años" de la barra de filtros.
    "missing_movies_year_filter": "1",
    # Filtro de género de la pestaña "Recomendado": nombre del género TMDB
    # ("Terror", "Comedia", "Animación"...), "Anime" (alias de Animación) o
    # "" / "Todos" (sin filtrar). Se elige con el selector "Género" de la
    # barra de filtros y se persiste al cambiar, como el resto.
    "missing_movies_genre_filter": "",
    # "Ocultar asiáticas" de la pestaña "Recomendado": activado de fábrica
    # (el usuario no quiere nada de Asia oriental ni del sur -- China, Japón,
    # Corea, India/Bollywood, Tailandia... -- que puebla de sobra las listas
    # de tendencias/populares de TMDB). Se descarta lo que TMDB marca con
    # original_language/origin_country asiático (ver apply_origin_filter en
    # core/missing_movies.py); sin dato de origen nunca se descarta. Se
    # persiste al cambiar (missing_movies_hide_asian), como el resto.
    "missing_movies_hide_asian": True,
    # El escaneo de Películas pide 10 páginas fijas de TMDB por cada lista
    # (tendencias/populares/próximos/en emisión), ~20 películas por página.
    # Se eliminó el selector "Páginas por lista": es un valor fijo en
    # gui/app.py (_on_movies_scan).
    # Autocompletado por serie (pestaña "Episodios que faltan", botón "auto"
    # de cada fila): lista de tmdb_id (str) con el autocompletado ACTIVO --
    # la app busca en aMule y descarga los capítulos que faltan solos y de
    # forma persistente (nuevos capítulos que van saliendo se añaden solos).
    # Es personal de CADA instalación (config.json del usuario, no viaja por
    # FTP), igual que los switches missing_ep_*. El estado "ya revisado" por
    # episodio/serie se guarda en missing_ep_auto_checked (ver abajo).
    "missing_ep_auto_complete": [],
    # {tmdb_id_str: {season: [ep, ...]}} -- episodios de series con
    # autocompletado que YA ESTÁN RESUELTOS (descarga lanzada y/o subidos al
    # servidor): no se vuelven a intentar. Los intentos FALLIDOS no van aquí,
    # van a missing_ep_auto_retries (backoff exponencial, ver app.py).
    "missing_ep_auto_checked": {},
    # {tmdb_id_str: {season: {ep: {"tries": int, "ts": epoch}}}} -- episodios
    # que se intentaron y fallaron (p.ej. sin candidato en aMule). Se
    # reintentan automáticamente con espera CRECIENTE (backoff exponencial:
    # 30 min, 1 h, 2 h, 4 h...) hasta que haya candidato, la serie deje de
    # faltar o se desactive el autocompletado. "tries" = nº de intentos ya
    # hechos, "ts" = época del último.
    "missing_ep_auto_retries": {},
    "auto_watcher_running": False,
    # aMule para buscar y descargar episodios que faltan -- host/puerto/
    # contraseña del External Connections de aMule (Preferencias -> Control
    # Remoto). El cliente habla el protocolo EC binario directamente por TCP.
    "amule_host": "localhost",
    "amule_port": 4712,
    "amule_password": "",
    # Red de búsqueda elegida en la pestaña Descargas ("Kad"/"Global"/"Local")
    # -- se persiste para que no vuelva a Kad al reiniciar.
"amule_search_type": "Kad",
    # Template de búsqueda en aMule por serie (dict {nombre_TMDB: template}).
    # Solo para cuando la búsqueda automática no da resultados: el usuario ayuda
    # proporcionando un template que sí encuentra. El template se usa tal cual
    # para construir la query de los capítulos que faltan (gui/app.py).
    # Vars: {serie}, {temporada}, {temporada:02d}, {episodio}, {episodio:02d}, {año}
    # Ej: {"That Time I Got Reincarnated as a Slime": "Slime {temporada}x{episodio:02d}"}
    # Si el template no contiene {temporada}/{episodio}, se añade " {temporada}x{episodio:02d}" solo.
    "series_search_patterns": {},
    # Sistema de reintentos inteligentes para archivos que fallan continuamente
    # (por ejemplo, en emule o cuando TMDB no tiene el título).
    # Si está activado, el watcher esperará un tiempo creciente antes de volver
    # a intentar un archivo que antes falló, en lugar de reprocesarlo en cada ciclo.
    "unstuck_enabled": False,
    "unstuck_max_retries": 5,                          # Máximo nº de reintentos antes de darse por vencido
    "unstuck_backoff_base_minutes": 30,              # Base del backoff exponencial (minutos)
    "unstuck_backoff_max_minutes": 480,              # Backoff máximo (8 horas)
    "unstuck_file_ttl_minutes": 1440,                # TTL (minutos) antes de reconsiderar un archivo "atrapado"
    # Estado del sistema Unstuck para aMule (descargas colgadas):
    # {tmdb_id_str: {season_str: {ep_str: {primary_hash, primary_started_ts, primary_last_bytes, primary_last_progress_ts,
    #                                      alt_hash, alt_started_ts, alt_last_bytes, alt_last_progress_ts,
    #                                      stuck_tries, last_alt_ts}}}}
    # Se gestiona desde gui/app.py y core/unstuck.py; no tocar a mano.
    "missing_ep_auto_downloads": {},
    # Paginado global para todas las tablas paginadas (ver TableView.page_size
    # y _on_global_page_size_changed en gui/app.py). Valores 10/50/100 fijos,
    # "Ajustado" calcula fitted = altura_disponible // alto_fila solo al abrir
    # pestaña (sin recalcular al redimensionar) para evitar doble render.
    "page_size": "Ajustado",
}


def config_path() -> Path:
    return app_data_dir() / "config.json"


class Config:
    def __init__(self):
        self._data: dict = dict(DEFAULTS)
        self.load()

    def load(self):
        path = config_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

        # Migrar la lista única de enlaces personalizables de antes de
        # separarla por nivel (serie/temporada/episodio) -- se queda tal
        # cual en el nivel "episodio" (el nombre original), y serie/
        # temporada arrancan con los valores por defecto nuevos en vez de
        # quedarse vacíos.
        if "custom_episode_links" in self._data:
            old_links = self._data.pop("custom_episode_links")
            if old_links and self._data.get("custom_links_episode") == DEFAULTS["custom_links_episode"]:
                self._data["custom_links_episode"] = old_links

        # Traer las credenciales de cuando la aplicación se llamaba de otra
        # forma: el llavero del sistema las guarda bajo AQUEL nombre, así que
        # sin esto el usuario se quedaría sin contraseña del servidor nada más
        # actualizar. Va ANTES del bloque de abajo para que su lectura final
        # (_get_keyring) ya encuentre lo que se acaba de copiar.
        if "_keyring_service_migrated" not in self._data:
            self._migrate_keyring_service()

        # Migrar credenciales de versiones anteriores (guardadas en texto
        # plano en config.json, p.ej. por edición manual o versiones
        # antiguas) al almacén de credenciales del sistema, y no dejar
        # rastro de ellas en el JSON de aquí en adelante.
        for key in _KEYRING_KEYS:
            plain = self._data.get(key, "")
            if plain:
                self._set_keyring(key, plain)
                self._data[key] = ""
                self.save()
            self._data[key] = self._get_keyring(key)

        # Migrar las plantillas únicas de ruta FTP (una por tipo de contenido)
        # al nuevo esquema de categorías (varias por tipo, con varias rutas
        # raíz cada una). Solo se ejecuta una vez: si ftp_categories ya existe
        # (aunque esté vacío a propósito) no se vuelve a tocar.
        if "ftp_categories" not in self._data:
            self._migrate_ftp_categories()

    def _migrate_keyring_service(self):
        """Trae las credenciales del llavero guardadas con el nombre antiguo.

        Se hace UNA sola vez y queda anotado en config.json. La marca importa
        sobre todo en macOS: al cambiar el identificador de la aplicación, el
        Llavero pide permiso ("aIBechos quiere usar información guardada por
        aIBechos"), y si el usuario dice que no, sin la marca volvería a
        preguntar en cada arranque.

        Cada clave va por su cuenta: que una falle o se deniegue no impide
        traer las demás. Lo que no se pueda traer se queda vacío y el usuario
        vuelve a escribirlo, igual que ya pasaba cuando no hay llavero."""
        for key in _KEYRING_KEYS:
            try:
                anterior = keyring.get_password(LEGACY_APP_NAME, key)
            except Exception:
                continue
            if not anterior:
                continue
            try:
                keyring.set_password(APP_NAME, key, anterior)
            except Exception:
                continue      # no se pudo escribir: se deja donde estaba
            try:
                keyring.delete_password(LEGACY_APP_NAME, key)
            except Exception:
                pass          # limpiar el rastro es deseable, no imprescindible
        self._data["_keyring_service_migrated"] = True
        self.save()

    def _migrate_ftp_categories(self):
        from core.ftp_categories import build_wildcard_category
        tv_tpl    = self._data.get("ftp_path_template", DEFAULTS["ftp_path_template"])
        movie_tpl = self._data.get("ftp_movie_path_template", DEFAULTS["ftp_movie_path_template"])
        self._data["ftp_categories"] = {
            "tv":    [build_wildcard_category("Series", tv_tpl)] if (tv_tpl or "").strip() else [],
            "movie": [build_wildcard_category("Películas", movie_tpl)] if (movie_tpl or "").strip() else [],
        }
        self.save()

    def save(self):
        path = config_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def to_dict(self) -> dict:
        """Copia de la configuración actual, sin ninguna credencial en texto
        plano — usada tanto para guardar en disco como para exportar."""
        data = dict(self._data)
        for key in _KEYRING_KEYS:
            data[key] = ""
        return data

    @staticmethod
    def _get_keyring(key: str) -> str:
        """Lee una credencial del almacén del sistema. Si no hay backend de
        keyring disponible, se degrada a "" (el usuario deberá reingresarla)
        en vez de crashear la app."""
        try:
            return keyring.get_password(APP_NAME, key) or ""
        except Exception:
            return ""

    @staticmethod
    def _set_keyring(key: str, value: str):
        try:
            if value:
                keyring.set_password(APP_NAME, key, value)
            else:
                keyring.delete_password(APP_NAME, key)
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value):
        if key in _KEYRING_KEYS:
            self._set_keyring(key, value)
        self._data[key] = value

    def set_many(self, updates: dict):
        for key, value in updates.items():
            self.set(key, value)

    def __getitem__(self, key):
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.set(key, value)
