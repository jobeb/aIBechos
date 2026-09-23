"""
Configuración compartida del servidor -- los parámetros que el grupo ha
decidido que deben ser iguales para cualquier persona que use aIBechos
contra el mismo FTP, en vez de tener que configurarlos a mano en cada
cliente nuevo y mantenerlos sincronizados a ojo entre todos (categorías,
plantillas de nombre, enlaces, credenciales de TMDB/IA/Plex/Jellyfin...).
El contenido "de verdad" vive en un único JSON en el FTP (ver gui/app.py::
_server_config_remote_path, derivado de shared_data_ftp_path).

A diferencia de core/favorites.py y core/reservations.py, aquí NO hace
falta un mirror local aparte: las claves de SHARED_CONFIG_KEYS ya viven en
config.json (son parte del esquema normal de Config), así que aplicar lo
descargado es simplemente Config.set_many() + Config.save() -- el propio
config.json ES el mirror local, no hay un segundo archivo que mantener
sincronizado con él.

La regla que separa SHARED_CONFIG_KEYS del resto de config.py::DEFAULTS NO
es "solo hechos objetivos del servidor" (esa fue la primera versión de
este módulo, y resultó demasiado restrictiva) ni tampoco "todo lo que
suele coincidir entre personas" (esa fue la causa de un incidente real:
publicar desde un cliente sobrescribió en silencio las plantillas de
nombre de todos los demás). La regla correcta es: **una clave va aquí si
el grupo ha decidido explícitamente que debe ser una única fuente de
verdad compartida** -- ni "cualquier cosa parecida" ni "solo lo que no se
puede discutir". Ver gui/app.py::_publish_server_config, que exige
confirmación explícita antes de sobrescribir esto para todos.

Categorías incluidas y por qué:
- TMDB/IA (tmdb_api_key, language, ai_api_key, ai_fallback_enabled): el
  grupo comparte una única cuenta/clave en vez de que cada persona
  registre la suya. ai_api_key en concreto normalmente vive SOLO en el
  keyring de cada equipo, nunca en texto plano -- aquí SÍ viaja en texto
  plano dentro del JSON del FTP porque el grupo lo decidió así de forma
  explícita, sabiendo que el archivo remoto solo está protegido por la
  contraseña FTP, no por keyring. Eso no cambia cómo se guarda en cada
  cliente al RECIBIRLA: Config.set() sigue mandando esa clave al keyring
  local (_KEYRING_KEYS en config.py) y Config.save() sigue blanqueándola
  antes de escribir config.json -- el texto plano solo existe en el JSON
  compartido del FTP, nunca en el disco de cada cliente.
- comicvine_api_key: identificación de cómics/manga (ver
  core/comicvine_client.py), mismo razonamiento y mismo tratamiento que
  tmdb_api_key -- cuenta compartida del grupo, texto plano en el JSON del
  FTP, nunca en _KEYRING_KEYS.
- google_books_api_key: identificación de ebooks (ver core/book_client.py).
  A diferencia de las anteriores, ES opcional -- Google Books funciona sin
  key, pero su cuota anónima es compartida GLOBALMENTE (no por IP/equipo),
  así que puede saturarse (429) por el uso de cualquier desconocido en
  internet. Compartirla dentro del grupo evita que cada persona tenga que
  registrar la suya solo para librarse de esa cuota compartida.
- Plantillas de nombre (tv_template/movie_template/anime_template/
  libro_template) y
  ftp_categories: para que todo el mundo suba con el mismo formato y a la
  misma estructura de carpetas, no una mezcla según quién subió cada
  archivo.
- rename_remote: afecta al nombre que queda en el servidor compartido
  (lo que ve todo el mundo), a diferencia de rename_local que solo toca
  la copia local de quien sube.
- Servidores de medios (plex_enabled/plex_host/plex_token,
  jellyfin_enabled/jellyfin_host/jellyfin_api_key/jellyfin_username):
  mismo razonamiento que TMDB/IA para los tokens -- el grupo decidió
  compartir un único Plex/Jellyfin en vez de que cada persona configure
  el suyo. jellyfin_username además es una decisión de grupo por
  naturaleza (qué cuenta cuenta como "visto" para Liberar espacio).
- Enlaces personalizables (custom_links_show/season/episode): ojo, un
  enlace personalizado puede apuntar a algo LOCAL de quien lo configuró
  (p.ej. "http://localhost:4711/..." a un eMule propio) -- si eso se
  publica, deja de funcionar para cualquier otro cliente. El grupo lo
  quiso compartido de todas formas; quien publique debe evitar enlaces
  con host/puerto locales si quiere que sigan funcionando para todos.
- reservation_quota_gb: el límite de reservas debe ser el mismo para
  cualquiera que reserve espacio contra este servidor (ver
  core/reservations.py), no una cuota distinta según quién la configuró
  en su propio equipo.
- p2p_trusted_groups / p2p_blocked_groups: qué grupos proveedores son de
  confianza y qué marcas de idioma no se descargan (ver
  core/download_quality.set_provider_lists, Ajustes → Servidor →
  Proveedores) -- si cada cliente llevara las suyas, el mismo capítulo
  se descargaría en idiomas distintos según quién pulsó el botón.
  OJO: amule_host/amule_port/amule_password NO van aquí (cada equipo
  tiene su propio aMule); solo las listas, que son criterio de grupo.

Excluido a propósito, y por qué (configuración de CLIENTE):
- ftp_protocol/ftp_host/ftp_port/ftp_user/ftp_password/ftp_use_tls/ftp_parallel/
  ftp_speed_limit/ftp_retries: toda la pestaña "Conexión FTP" -- hacen
  falta para conectar y descargar este mismo archivo (no se pueden
  autodescubrir), y además cada persona podría querer apuntar a un
  servidor distinto sin que eso le imponga nada a los demás; el cupo de
  paralelismo/velocidad/reintentos describe la conexión de CADA equipo,
  no una propiedad del servidor.
- app_user_name, shared_data_ftp_path: identidad de esta persona y ancla
  desde la que se deriva la ruta de este mismo archivo -- no pueden
  formar parte de lo que el archivo transporta.
- appearance, color_theme, last_dir, table_col_widths,
  table_hidden_columns, amule_search_type, skipped_update_version:
  preferencias de interfaz o estado de ESTE equipo, sin ningún efecto
  sobre el servidor.
- watch_folder, poll_interval, auto_action, manual_action, auto_extract_archives,
  min_confidence, desktop_notifications, start_with_windows, rename_local: comportamiento
  del modo automático y de la subida manual de CADA equipo -- forzarlo
  igual para todos no tendría sentido (cada persona vigila su propia
  carpeta local, y puede querer un destino distinto tras subir a mano).
- watch_sync_user_mappings, watch_sync_last_run_ts, watch_sync_schedule_enabled,
  watch_sync_schedule_time (ver core/watch_sync.py): el emparejamiento de
  usuarios Plex<->Jellyfin es conocimiento personal ("qué persona es
  cuál"), NO una decisión de grupo -- y sobre todo, SHARED_CONFIG_KEYS se
  aplica sin revisión en cada arranque de la app (ver
  _apply_synced_server_config en gui/app.py), lo que rompería justo la
  garantía de "revisión humana antes de escribir" que tiene el botón
  manual de esta función. La programación horaria en concreto SÍ escribe
  sin confirmación por decisión explícita del usuario -- pero esa
  decisión es de CADA equipo (uno puede querer programarlo de madrugada,
  otro no querer programarlo en absoluto), compartirla forzaría la misma
  hora/activación a todo el mundo sin que lo pidieran. Omisión
  deliberada, no un descuido.
"""

SHARED_CONFIG_KEYS = (
    # TMDB / fallback de IA -- credenciales y ajustes compartidos por el
    # grupo en vez de que cada persona registre las suyas.
    "tmdb_api_key", "language", "ai_api_key", "ai_fallback_enabled",
    # ComicVine (identificación de cómics/manga, ver
    # core/comicvine_client.py) -- misma razón que tmdb_api_key: una única
    # cuenta del grupo en vez de que cada persona registre la suya.
    "comicvine_api_key",
    # Google Books (ebooks, ver core/book_client.py) -- opcional (funciona
    # sin ella), pero misma razón si el grupo decide configurar una: cuota
    # per-proyecto compartida en vez de que cada persona dependa de la
    # cuota anónima global (que puede saturarse por el uso de CUALQUIERA,
    # no solo del grupo).
    "google_books_api_key",
    # Convención de nombres y estructura real del servidor.
    "tv_template", "movie_template", "anime_template", "libro_template", "comic_template",
    "ftp_categories",
    "rename_remote",
    # Servidores de medios -- host, activado y credenciales.
    "plex_enabled", "plex_host", "plex_token",
    "jellyfin_enabled", "jellyfin_host", "jellyfin_api_key", "jellyfin_username",
    # Enlaces personalizables del detector de episodios que faltan.
    "custom_links_show", "custom_links_season", "custom_links_episode",
    # Cuota de reservas (ver core/reservations.py).
    "reservation_quota_gb",
    # Listas de proveedores de P2P (ver core/download_quality) -- mismo
    # motivo que las plantillas: decisión de grupo, no de cada equipo.
    "p2p_trusted_groups", "p2p_blocked_groups",
)


def extract_shared_config(get_fn) -> dict:
    """Construye el dict a publicar a partir de *get_fn* (normalmente
    Config.get) -- solo las claves de SHARED_CONFIG_KEYS, cada una con lo
    que valga ahora mismo en local. No incluye learned_junk_terms -- no es
    una clave de Config, vive en core/learned_terms.py y se gestiona
    aparte (ver gui/app.py::_publish_server_config)."""
    return {key: get_fn(key) for key in SHARED_CONFIG_KEYS}


def filter_shared_config(data: dict) -> dict:
    """Del dict descargado del FTP, solo las claves reconocidas de
    SHARED_CONFIG_KEYS -- para no aplicar en local nada que no sea de
    verdad configuración de servidor (un JSON manipulado a mano, o de una
    versión más nueva con claves que esta versión no espera). Igual que
    extract_shared_config, learned_junk_terms se gestiona aparte, no pasa
    por aquí."""
    return {key: value for key, value in data.items() if key in SHARED_CONFIG_KEYS}


_LAST_SYNCED_FILENAME = "server_config_last_synced.json"


def _last_synced_path():
    from core.appdirs import app_data_dir
    return app_data_dir() / _LAST_SYNCED_FILENAME


def load_last_synced_snapshot() -> dict:
    """Última copia de SHARED_CONFIG_KEYS vista en un sync/publish
    anterior -- para saber, la próxima vez que se sincronice desde el FTP,
    qué claves ha tocado el usuario EN LOCAL desde entonces sin haberlas
    publicado todavía (ver diff_local_changes/gui/app.py::
    _apply_synced_server_config). Bug real que motivó esto: un enlace
    personalizable nuevo, guardado en local pero sin publicar, desaparecía
    al reiniciar la app -- el sync silencioso de arranque sobrescribía
    TODA la configuración de servidor sin distinguir "esto lo cambié yo
    hace un momento" de "esto lo publicó otra persona". {} si nunca hubo
    un sync/publish todavía (primera vez en este cliente, o versión
    anterior a este arreglo) -- en ese caso no hay base fiable de
    comparación, así que el llamador debe tratarlo como "aplicar todo",
    igual que siempre se hizo."""
    path = _last_synced_path()
    if not path.exists():
        return {}
    try:
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_last_synced_snapshot(data: dict) -> None:
    import json
    path = _last_synced_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def diff_local_changes(get_fn, last_synced: dict) -> set:
    """Claves de SHARED_CONFIG_KEYS cuyo valor LOCAL actual (get_fn,
    normalmente Config.get) ya no coincide con la última copia vista de un
    sync/publish (last_synced) -- son ediciones locales sin publicar
    todavía, que _apply_synced_server_config no debe pisar en silencio con
    lo que llegue del FTP."""
    return {key for key in SHARED_CONFIG_KEYS if get_fn(key) != last_synced.get(key)}
