"""
Solicitudes de descarga compartidas entre la web de solicitudes y todos
los clientes de aIBechos que apuntan al mismo servidor FTP (ver
gui/app.py para la sincronización real y el worker que las atiende --
este módulo solo tiene el formato y las funciones puras de
crear/reclamar/marcar/fusionar, sin tocar la red). Mismo patrón que
core/reservations.py (claim con dueño y fecha), pero para trabajo por
hacer en vez de espacio apartado.

El contenido "de verdad" vive en un único JSON en el FTP
(aIBechos_solicitudes.json, ver core/shared_data.py::SHARED_DATA_FILES);
la web solo crea entradas en "pending" y lee estados, y solo los
clientes de escritorio cambian status/claimed_* (el primero que ve una
solicitud pendiente la reclama con su app_user_name; los demás la
ignoran al ver claimed_by ajeno).

Formato: {"<req_id>": {"tmdb_id": 1234, "media_type": "tv",
"season": 1, "episode": 2, "title": "...", "year": "2024",
"requested_by": "nombre Jellyfin", "requested_at": 1789700000,
"status": "pending", "claimed_by": "", "claimed_at": 0, "attempts": 0,
"last_error": "", "completed_at": 0, "updated_at": 1789700000}}

- season/episode solo para capítulos sueltos de TV (None = serie o
  temporada entera; las pelis no los usan).
- "status": pending (nueva) -> claimed (un PC la cogió) ->
  downloading (aMule la aceptó) -> done | failed. La web solo pone
  pending; el escritorio mueve el resto.
- "claimed_at" (epoch): un claim de más de CLAIM_TTL_SECONDS se
  considera muerto (PC apagado a medias) y otro cliente puede
  reclamarla (attempts+1).
- "requested_by" lo fija el servidor web desde la sesión Jellyfin,
  nunca lo que mande el navegador (ver solicitudes-web/api.php).
"""

import time
import uuid

#: Un claim más viejo que esto se da por muerto y se puede reclamar.
CLAIM_TTL_SECONDS = 2 * 3600

#: Intentos máximos antes de dar una solicitud por fallida.
MAX_ATTEMPTS = 5

#: Una solicitud reclamada/en descarga sin verificación de completado
#: durante más de esto vuelve a "pending" para que otro equipo la
#: reintente (el PC original pudo apagarse sin marcar nada).
STUCK_AFTER_SECONDS = 7 * 86400

#: Estados finales que prune() borra pasados PRUNE_AFTER_SECONDS.
FINAL_STATUSES = ("done", "failed")
PRUNE_AFTER_SECONDS = 30 * 86400

_VALID_MEDIA_TYPES = ("tv", "movie")
_VALID_STATUSES = ("pending", "claimed", "downloading", "done", "failed")


def new_request_id() -> str:
    """Id único para una solicitud (hex corto, sin guiones)."""
    return uuid.uuid4().hex[:16]


def new_request(tmdb_id: int, media_type: str, title: str, requested_by: str,
                season=None, episode=None, year: str = "",
                now: float | None = None) -> tuple[str, dict]:
    """(req_id, entrada) nueva en "pending" -- valida lo mínimo
    (tmdb_id positivo, media_type tv|movie) y lanza ValueError si no
    cuadra, para que ni la web ni el escritorio guarden basura."""
    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        raise ValueError(f"tmdb_id inválido: {tmdb_id!r}")
    if tmdb_id <= 0:
        raise ValueError(f"tmdb_id inválido: {tmdb_id!r}")
    if media_type not in _VALID_MEDIA_TYPES:
        raise ValueError(f"media_type inválido: {media_type!r}")
    if not (title or "").strip():
        raise ValueError("title vacío")
    if not (requested_by or "").strip():
        raise ValueError("requested_by vacío")
    try:
        season = int(season) if season is not None else None
        episode = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        raise ValueError("season/episode inválidos")
    if media_type == "movie":
        season, episode = None, None
    ts = now if now is not None else time.time()
    req_id = new_request_id()
    return req_id, {
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "season": season,
        "episode": episode,
        "title": str(title).strip(),
        "year": str(year or "").strip(),
        "requested_by": str(requested_by).strip(),
        "requested_at": ts,
        "status": "pending",
        "claimed_by": "",
        "claimed_at": 0,
        "attempts": 0,
        "last_error": "",
        "completed_at": 0,
        "updated_at": ts,
    }


def _entry(data: dict, req_id: str) -> dict | None:
    entry = (data or {}).get(req_id)
    return entry if isinstance(entry, dict) else None


def is_claim_stale(entry: dict, now: float | None = None,
                   ttl: int = CLAIM_TTL_SECONDS) -> bool:
    """True si el claim de *entry* caducó (claimed/downloading con
    claimed_at más viejo que *ttl*) -- otro cliente puede reclamarla."""
    if not isinstance(entry, dict):
        return True
    if entry.get("status") not in ("claimed", "downloading"):
        return False
    try:
        age = (now if now is not None else time.time()) - float(entry.get("claimed_at") or 0)
    except (TypeError, ValueError):
        return True
    return age > ttl


def claim(data: dict, req_id: str, user: str,
          now: float | None = None) -> dict | None:
    """Devuelve un dict NUEVO con *req_id* reclamada por *user*, o None
    si no se puede (no existe, ya reclamada por otro sin caducar, o en
    estado final). Solo reclama "pending" o claims caducados (suma un
    intento en ese caso)."""
    entry = _entry(data, req_id)
    if entry is None or not (user or "").strip():
        return None
    status = entry.get("status")
    ts = now if now is not None else time.time()
    if status == "pending":
        attempts = entry.get("attempts") or 0
    elif is_claim_stale(entry, now=ts):
        try:
            attempts = int(entry.get("attempts") or 0) + 1
        except (TypeError, ValueError):
            attempts = 1
    else:
        return None
    result = dict(data)
    result[req_id] = {**entry, "status": "claimed",
                      "claimed_by": str(user).strip(),
                      "claimed_at": ts, "attempts": attempts,
                      "updated_at": ts}
    return result


def mark_status(data: dict, req_id: str, status: str, user: str = "",
                error: str = "", now: float | None = None) -> dict | None:
    """Devuelve un dict NUEVO con *status* aplicado a *req_id*, o None si
    no existe o el estado no es válido. Si se pasa *user*, solo deja
    mover solicitudes reclamadas por él (el dueño del claim manda);
    con *user* vacío no se comprueba dueño (uso interno del worker
    sobre su propio claim). "done" sella completed_at."""
    entry = _entry(data, req_id)
    if entry is None or status not in _VALID_STATUSES:
        return None
    if user and entry.get("claimed_by") and entry.get("claimed_by") != user:
        return None
    ts = now if now is not None else time.time()
    updated = {**entry, "status": status, "updated_at": ts}
    if error:
        updated["last_error"] = str(error)[:500]
    if status == "done":
        updated["completed_at"] = ts
    result = dict(data)
    result[req_id] = updated
    return result


def release(data: dict, req_id: str, user: str = "", error: str = "",
            now: float | None = None) -> dict | None:
    """Devuelve un dict NUEVO con *req_id* devuelta a "pending" (claim
    liberado) o None si no existe. Suma un intento; si llega a
    MAX_ATTEMPTS la marca "failed" en vez de liberarla. Con *user* solo
    libera su propio claim. Para fallos de descarga que debe reintentar
    otro equipo (o este en otro ciclo)."""
    entry = _entry(data, req_id)
    if entry is None:
        return None
    if user and entry.get("claimed_by") and entry.get("claimed_by") != user:
        return None
    ts = now if now is not None else time.time()
    try:
        attempts = int(entry.get("attempts") or 0) + 1
    except (TypeError, ValueError):
        attempts = 1
    result = dict(data)
    if attempts >= MAX_ATTEMPTS:
        result[req_id] = {**entry, "status": "failed", "attempts": attempts,
                          "updated_at": ts,
                          "last_error": str(error or entry.get("last_error") or "")[:500]}
        return result
    updated = {**entry, "status": "pending", "claimed_by": "",
               "claimed_at": 0, "attempts": attempts, "updated_at": ts}
    if error:
        updated["last_error"] = str(error)[:500]
    result[req_id] = updated
    return result


def merge(local: dict, remote: dict) -> dict:
    """Fusiona dos copias del JSON (local + recién descargado): por cada
    req_id gana la entrada con mayor updated_at; las que solo están en un
    lado se conservan. Así dos clientes (o la web) pueden escribir a la
    vez sin pisarse del todo -- quien suba después lleva lo último de
    cada solicitud. Nunca lanza con basura: ignora entradas no-dict."""
    merged = {}
    for req_id, entry in (local or {}).items():
        if isinstance(entry, dict):
            merged[req_id] = entry
    for req_id, entry in (remote or {}).items():
        if not isinstance(entry, dict):
            continue
        current = merged.get(req_id)
        if not isinstance(current, dict):
            merged[req_id] = entry
            continue
        try:
            new_ts = float(entry.get("updated_at") or 0)
        except (TypeError, ValueError):
            new_ts = 0
        try:
            cur_ts = float(current.get("updated_at") or 0)
        except (TypeError, ValueError):
            cur_ts = 0
        if new_ts >= cur_ts:
            merged[req_id] = entry
    return merged


def prune(data: dict, now: float | None = None) -> dict:
    """Devuelve un dict NUEVO sin las solicitudes en estado final
    (done/failed) de hace más de PRUNE_AFTER_SECONDS -- la web ya las
    mostró y el JSON compartido no debe crecer sin límite. Lo pendiente
    o en curso no se toca nunca, por viejo que sea."""
    ts = now if now is not None else time.time()
    result = {}
    for req_id, entry in (data or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") in FINAL_STATUSES:
            try:
                age = ts - float(entry.get("updated_at") or 0)
            except (TypeError, ValueError):
                age = 0
            if age > PRUNE_AFTER_SECONDS:
                continue
        result[req_id] = entry
    return result


def pending_for_worker(data: dict, now: float | None = None) -> list:
    """Lista de (req_id, entrada) reclamables por el worker: "pending" o
    claims caducados, ordenadas por requested_at (las más viejas
    primero)."""
    ts = now if now is not None else time.time()
    out = []
    for req_id, entry in (data or {}).items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status == "pending" or is_claim_stale(entry, now=ts):
            out.append((req_id, entry))
    try:
        out.sort(key=lambda kv: float((kv[1] or {}).get("requested_at") or 0))
    except (TypeError, ValueError):
        pass
    return out


def describe(entry: dict) -> str:
    """"Título (año) [T1 / T1E02]" para logs y la web -- nunca lanza."""
    try:
        title = str((entry or {}).get("title") or "¿?").strip() or "¿?"
        year = str((entry or {}).get("year") or "").strip()
        base = f"{title} ({year})" if year else title
        season = (entry or {}).get("season")
        episode = (entry or {}).get("episode")
        if season is not None and episode is not None:
            return f"{base} [{season}x{int(episode):02d}]"
        if season is not None:
            return f"{base} [T{season}]"
        return base
    except Exception:
        return "¿?"


_EPISODE_RE = None  # perezoso, ver _episode_numbers_in_name()


def _episode_numbers_in_name(stem: str):
    """(temporada, episodio) si el nombre trae SxxEyy o NNxNN, o
    (None, None). Solo para confirmar capítulos sueltos."""
    import re as _re

    global _EPISODE_RE
    if _EPISODE_RE is None:
        _EPISODE_RE = _re.compile(
            r"[Ss](\d{1,2})[Ee](\d{1,3})|(?<!\d)(\d{1,2})[xX](\d{2,3})")
    m = _EPISODE_RE.search(stem or "")
    if not m:
        return None, None
    if m.group(1) is not None:
        return int(m.group(1)), int(m.group(2))
    return int(m.group(3)), int(m.group(4))


def _remote_basename(remote: str) -> str:
    """Nombre de archivo de una ruta remota (o local) del historial."""
    if not remote or not isinstance(remote, str):
        return ""
    base = remote.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base.strip()


def load_upload_history(path=None) -> list:
    """Registros de upload_history.json (lista) -- [] si no existe o
    está roto. *path* solo para tests; por defecto el de app_data_dir.
    Solo importan los "ok" (llegaron al servidor)."""
    import json as _json

    if path is None:
        from core.appdirs import app_data_dir
        path = app_data_dir() / "upload_history.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def matches_upload(entry: dict, remote: str) -> bool:
    """True si *remote* (ruta del historial de subidos) ES lo pedido en
    *entry*: mismo título confirmado con año (ver
    core/series_match.best_match_with_year: el año del pedido manda y
    un año distinto en el archivo descarta) y, para capítulos, mismos
    números S/E en el nombre. Para temporada/serie completa no se
    puede confirmar con UN archivo: siempre False (eso lo decide el
    servidor de medios, no el historial). Nunca lanza."""
    try:
        from core.series_match import best_match_with_year, series_similarity
    except Exception:
        return False
    try:
        if not isinstance(entry, dict):
            return False
        title = str(entry.get("title") or "").strip()
        if not title:
            return False
        year = str(entry.get("year") or "").strip() or None
        stem = _remote_basename(remote)
        if not stem:
            return False

        def _title_ok(candidate: str) -> bool:
            if year:
                best, _ratio = best_match_with_year(
                    "%s (%s)" % (title, year), [candidate], year,
                    min_ratio=0.85)
                return best is not None
            return series_similarity(title, candidate,
                                     strict=True) >= 0.85

        media_type = entry.get("media_type")
        season = entry.get("season")
        episode = entry.get("episode")
        if media_type == "movie":
            return _title_ok(stem)
        if media_type == "tv" and season is not None \
                and episode is not None:
            s_num, e_num = _episode_numbers_in_name(stem)
            if s_num is None or int(season) != s_num \
                    or int(episode) != e_num:
                return False
            return _title_ok(stem[:_episode_start(stem)])
        # Temporada/serie completa no se confirma con UN archivo.
        return False
    except Exception:
        return False
def _episode_start(stem: str) -> int:
    """Índice donde empieza el marcador S/E en *stem* (0 si no hay)."""
    s_num, _ = _episode_numbers_in_name(stem)
    if s_num is None:
        return 0
    import re as _re

    m = _re.search(r"[Ss]\d{1,2}[Ee]\d{1,3}|(?<!\d)\d{1,2}[xX]\d{2,3}",
                   stem or "")
    return m.start() if m else 0


def uploaded_request(history: list, entry: dict) -> bool:
    """True si ALGÚN registro "ok" del historial es lo pedido (ver
    matches_upload) -- para dar por completada una solicitud subida a
    mano o por otro flujo sin pasar por las filas de Episodios/Películas
    (caso real: peli subida manual que ni está en las listas)."""
    try:
        for e in history or []:
            if not isinstance(e, dict) or e.get("status") != "ok":
                continue
            if matches_upload(entry, e.get("remote") or ""):
                return True
        return False
    except Exception:
        return False
