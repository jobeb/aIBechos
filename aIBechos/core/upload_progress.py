"""Progreso de subidas FTP en curso, para que el worker de solicitudes
publique el % de subida de lo pedido desde la web.

Solo memoria del proceso (se pierde al cerrar, no pasa nada: el tick
siguiente lo repinta o la solicitud se marca done). Con candado: lo
escribe AutoWatcher desde sus hilos de subida y lo lee el worker de
solicitudes. Las entradas caducan solas (TTL) y se borran al terminar
la subida, para no enseñar un 100% viejo a otra solicitud del mismo
título. Nunca toca red ni lanza."""

import threading
import time

#: Segundos que vale una muestra sin refrescar.
TTL_SECONDS = 600

_LOCK = threading.Lock()
_UPLOADS: dict = {}


def _norm_key(media_type, tmdb_id, season, episode):
    """Clave (media_type, tmdb_id, season, episode) normalizada, o None
    si no identifica una peli/capítulo (libros, sin tmdb...)."""
    try:
        mt = str(media_type or "").strip().lower()
        if mt not in ("tv", "movie"):
            return None
        tid = int(tmdb_id)
        if tid <= 0:
            return None
        s = int(season) if season is not None else None
        e = int(episode) if episode is not None else None
        if mt == "movie":
            s, e = None, None
        return (mt, tid, s, e)
    except (TypeError, ValueError):
        return None


def report(media_type, tmdb_id, season, episode, pct) -> None:
    """Guarda el tanto por uno (0.0-1.0) de subida en curso. Sin ids
    válidos no hace nada."""
    key = _norm_key(media_type, tmdb_id, season, episode)
    if key is None:
        return
    try:
        value = max(0.0, min(1.0, float(pct)))
    except (TypeError, ValueError):
        return
    with _LOCK:
        _UPLOADS[key] = (value, time.time())


def snapshot() -> dict:
    """{(media_type, tmdb_id, season, episode): tanto_por_uno} frescas."""
    now = time.time()
    with _LOCK:
        fresh = {k: v for k, (v, ts) in _UPLOADS.items()
                 if now - ts < TTL_SECONDS}
        if len(fresh) != len(_UPLOADS):
            _UPLOADS.clear()
            _UPLOADS.update(fresh)
        return dict(fresh)


def discard(media_type, tmdb_id, season, episode) -> None:
    """Olvida la subida (al terminar, fallar o cancelar)."""
    key = _norm_key(media_type, tmdb_id, season, episode)
    if key is None:
        return
    with _LOCK:
        _UPLOADS.pop(key, None)
