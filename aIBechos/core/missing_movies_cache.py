"""
Caché persistente de la pestaña "Recomendado" (recomendar películas y
series que no están en el servidor, ver core/missing_movies.py y
gui/app.py::_scan_missing_movies). El primer escaneo es caro (varias listas
de TMDB + la biblioteca de películas/series del servidor) -- esto guarda el
resultado para que los siguientes solo crucen lo que haya cambiado, y para
que la pestaña muestre recomendaciones aunque se abra sin conexión.

Formato: {clave: {"media_type", "title", "year", "release_date",
"overview", "poster_url", "genres", "genre_ids", "vote_average",
"popularity", "list", "in_server", "watch", "original_language",
"origin_country"}, "_meta": {"last_scan_ts": float}}

La clave es "{media_type}:{tmdb_id}" (p.ej. "movie:12345", "tv:67890"):
TMDB numera películas y series en espacios separados, así que un tmdb_id
puede ser una película Y una serie a la vez (mismo número, cosas
distintas) -- la clave compuesta evita que una pise a la otra. Las cachés
de versiones anteriores solo guardaban películas con claves numéricas
("12345"); load_cache/normalize_cache migran esas claves a "movie:12345"
en memoria.

"list" es la lista de TMDB de la que salió ("trending"/"popular"/
"upcoming"/"now_playing"/"on_the_air"). "in_server" es True cuando el
cruce con Jellyfin/Plex detectó que la película/serie YA está en el
servidor -- esas filas se muestran solo si el usuario apaga "Ocultar ya en
el servidor" (mismo patrón que "Ocultar completas" en Episodios), nunca se
recomiendan para descargar. "watch" es la disponibilidad fuera del cine
consultada a TMDB ({"flatrate": ["Netflix", ...], "rent": [...], "buy":
[...], ...}; ver TMDBClient.get_movie_watch_providers) -- alimenta el
interruptor "Solo disponibles en plataformas". Solo aplica a películas;
las series (media_type "tv") no consultan watch providers y quedan con
"watch": {}.

Compartido entre clientes vía FTP (ver gui/app.py::
_push_missing_movies_to_ftp/_sync_missing_movies_from_ftp), igual que
"Episodios que faltan". Antes era personal de cada instalación (el cruce
in_server se calculaba solo contra el servidor de medios LOCAL y no tenía
sentido compartirlo); desde que una subida marca la película como en
servidor al instante (ver gui/app.py::_remove_uploaded_movie_from_movies_list)
y un cliente puede querer que el resto de instalaciones del mismo servidor
también la quiten de sus listas, el flag "in_server" sí viaja por la red.
El push fusiona (merge_movies_cache) en vez de reemplazar, combinando
in_server con OR para que un cliente cuya biblioteca aún no ha reindexado
la película no la "desmarque" para los demás."""

import json

from core.appdirs import app_data_dir

_FILENAME = "missing_movies_cache.json"
_cache: dict | None = None


def _path():
    return app_data_dir() / _FILENAME


def cache_key(media_type: str, tmdb_id: int) -> str:
    """Clave compuesta de la caché para un tmdb_id de un tipo concreto --
    "{media_type}:{tmdb_id}". Necesaria porque TMDB numera películas y
    series por separado: una película y una serie pueden compartir el mismo
    número de tmdb_id, y sin el tipo en la clave se pisarían."""
    return f"{media_type}:{tmdb_id}"


def parse_cache_key(key: str) -> tuple | None:
    """Inversa de cache_key: (media_type, tmdb_id) para una clave ya
    compuesta. Devuelve None si no parece una clave de la caché (p.ej.
    "_meta"). Las claves de versiones antiguas ("12345", solo el número)
    no se admiten aquí -- normalize_cache las convierte antes."""
    if ":" not in key:
        return None
    media_type, _, rest = key.partition(":")
    if media_type not in ("movie", "tv"):
        return None
    try:
        return media_type, int(rest)
    except (TypeError, ValueError):
        return None


def normalize_cache(cache: dict) -> dict:
    """Devuelve una copia de *cache* con las claves migradas al formato
    actual "{media_type}:{tmdb_id}". Las claves que ya tienen el prefijo
    "movie:"/"tv:" se dejan tal cual; las claves numéricas (formato de
    versiones antiguas, solo películas) se convierten a "movie:{id}".
    Cualquier otra clave no válida (incluido "_meta") se conserva tal cual."""
    normalized = {}
    for key, entry in cache.items():
        if ":" in key:
            normalized[key] = entry
            continue
        try:
            int(key)
        except (TypeError, ValueError):
            normalized[key] = entry
            continue
        if isinstance(entry, dict):
            entry = dict(entry)
            entry.setdefault("media_type", "movie")
        normalized[f"movie:{key}"] = entry
    return normalized


def _read_from_disk() -> dict:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = normalize_cache(_read_from_disk())
    return _cache


def save_cache(cache: dict) -> None:
    global _cache
    _cache = cache
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def remove_movie_from_cache(cache: dict, tmdb_id: int, media_type: str = "movie") -> bool:
    """Quita la entrada de *cache* para tmdb_id (del tipo pedido) -- usado
    cuando el usuario quita una recomendación (ver App._dismiss_missing_movie).
    Mutación en sitio. El llamador es responsable de save_cache() después."""
    key = cache_key(media_type, tmdb_id)
    if key not in cache:
        return False
    del cache[key]
    return True


def mark_movie_in_server(cache: dict, tmdb_id: int, media_type: str = "movie") -> bool:
    """Marca como "ya en el servidor" la entrada de *cache* para tmdb_id
    (del tipo pedido) -- usado cuando la app acaba de subir esa película (ver
    App._remove_uploaded_movie_from_movies_list): la propia app ya sabe que
    acaba de subirla, no hace falta esperar a que Jellyfin/Plex la
    reindexe. Devuelve True si de verdad cambió algo (la entrada existía y
    no estaba ya marcada); el llamador es responsable de save_cache()
    después. El flag se conserva en la caché (no se borra la película)
    porque es justo lo que se comparte por FTP: "ya está en el servidor",
    ver merge_movies_cache."""
    key = cache_key(media_type, tmdb_id)
    entry = cache.get(key)
    if entry is None or entry.get("in_server"):
        return False
    entry["in_server"] = True
    return True


def merge_movies_cache(local_cache: dict, remote_cache: dict) -> dict:
    """Fusiona dos cachés de "Recomendado" (la local y la remota ya
    descargada del FTP) para el PUSH: el resultado es lo que se vuelve a
    subir. No hay dirección preferida porque ninguna de las dos es "más
    fresca" que la otra -- cada cliente tiene las películas/series que le
    han salido en sus propias listas TMDB y su propio flag "in_server".
    Reglas por elemento (clave "{media_type}:{tmdb_id}"):
      * si solo está en una de las dos, se conserva tal cual;
      * si está en las dos, se prefieren los datos del LOCAL (la caché
        recién actualizada de este cliente) pero rellenando huecos con el
        remoto, y "in_server" se combina con OR: si cualquiera de los dos
        clientes dice que ya está en su servidor, queda marcado -- así una
        instalación cuya biblioteca todavía no ha reindexado no lo
        "desmarca" para las demás.
    Se normalizan las claves antiguas (numéricas, de cuando solo había
    películas) a "movie:{id}" en ambos lados antes de fusionar.
    "_meta" se conserva (del local si existe, si no del remoto)."""
    local_cache = normalize_cache(local_cache)
    remote_cache = normalize_cache(remote_cache)
    keys = set(local_cache) | set(remote_cache)
    result = {}
    for key in keys:
        if key == "_meta":
            result[key] = local_cache.get("_meta") or remote_cache.get("_meta")
            continue
        local = local_cache.get(key) or {}
        remote = remote_cache.get(key) or {}
        if not local:
            result[key] = dict(remote)
            continue
        if not remote:
            result[key] = dict(local)
            continue
        merged = dict(local)
        for field in ("media_type", "title", "year", "release_date", "overview",
                      "poster_url", "vote_average", "popularity", "list",
                      "certification", "genres", "genre_ids", "watch",
                      "original_language", "origin_country"):
            if not merged.get(field) and remote.get(field):
                merged[field] = remote[field]
        merged["in_server"] = bool(local.get("in_server")) or bool(remote.get("in_server"))
        result[key] = merged
    return result


def apply_remote_in_server(local_cache: dict, remote_cache: dict) -> tuple:
    """Aplica a *local_cache* (copia) el flag "in_server=True" de las
    películas/series que el REMOTO diga que ya están en el servidor -- lo
    que se usa al BAJAR del FTP (ver gui/app.py::_apply_synced_missing_movies).
    A diferencia de merge_movies_cache (el push), aquí NO se añaden ni se
    borran entradas: si el usuario descartó una recomendación (botón 🚫,
    que la quita de la caché local) no debe volver a aparecer por el solo
    hecho de que otro cliente la tenga compartida. Devuelve (resultado,
    cambio_hubo): el resultado es una copia de *local_cache* con
    in_server=True en las entradas que el remoto marque, y el booleano
    indica si de verdad cambió algo (para saber si merece la pena
    guardar/repintar)."""
    local_cache = normalize_cache(local_cache)
    remote_cache = normalize_cache(remote_cache)
    result = dict(local_cache)
    changed = False
    for key, remote_entry in (remote_cache or {}).items():
        if key == "_meta" or not isinstance(remote_entry, dict):
            continue
        if not remote_entry.get("in_server"):
            continue
        local_entry = result.get(key)
        if local_entry is None:
            continue
        if not local_entry.get("in_server"):
            local_entry = dict(local_entry)
            local_entry["in_server"] = True
            result[key] = local_entry
            changed = True
    return result, changed


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None