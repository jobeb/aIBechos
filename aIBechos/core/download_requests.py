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
"last_error": "", "completed_at": 0, "updated_at": 1789700000,
"replace": true, "replace_since": 1789700100.0}}

- season/episode solo para capítulos sueltos de TV (None = serie o
  temporada entera; las pelis no los usan).
- "status": pending (nueva) -> claimed (un PC la cogió) ->
  downloading (aMule la aceptó) -> done | failed. La web pone pending
  y "cancelled" (cancelada desde la web por quien la pidió o por un
  admin, con "cancelled_by"); el escritorio mueve el resto. Una
  cancelada que algún PC tenía reclamada lleva "cancel_done": false
  hasta que ese PC quita la descarga de aMule (ver to_cancel).
  "cancelled" es pegajoso en merge(): ninguna escritura posterior del
  escritorio la puede deshacer.
- "replace" (solo true, se omite si no): sustitución pedida desde la
  web de algo que YA está en el servidor. El worker NO debe darla por
  hecha al ver presencia (eso haría done inmediato sin descargar):
  solo es done cuando hay una subida NUEVA al servidor posterior a
  "replace_since" (ver uploaded_request_since), que el worker sella al
  lanzar la descarga. AutoWatcher tampoco debe omitirla por
  duplicada: al lanzarla, el PC que reclama registra un pendiente
  "kind": "request" en core/slim_pending.py (el mismo mecanismo que
  el reemplazo por adelgazamiento) y el vigilante sube la nueva y
  borra la vieja después.
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
FINAL_STATUSES = ("done", "failed", "cancelled")
PRUNE_AFTER_SECONDS = 30 * 86400

_VALID_MEDIA_TYPES = ("tv", "movie")
_VALID_STATUSES = ("pending", "claimed", "downloading", "done", "failed", "cancelled")


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
        cur_cancel = current.get("status") == "cancelled"
        new_cancel = entry.get("status") == "cancelled"
        if cur_cancel != new_cancel:
            # Cancelada en un lado: gana siempre (el escritorio pudo
            # escribir después, p. ej. "downloading" al acabar de lanzar,
            # sin haber visto la cancelación).
            merged[req_id] = _sticky_cancel(current if cur_cancel else entry,
                                            entry if cur_cancel else current)
        elif new_ts >= cur_ts:
            merged[req_id] = entry
    return merged


def _sticky_cancel(cancelled: dict, other: dict) -> dict:
    """La cancelada, con lo que el otro lado sabía de la descarga: los
    hashes de aMule y, si la web la canceló aún "pending" mientras un
    PC la reclamaba, quién la tiene (y entonces ese PC aún debe quitarla
    de aMule: cancel_done vuelve a False)."""
    out = dict(cancelled)
    if not out.get("amule_hashes") and other.get("amule_hashes"):
        out["amule_hashes"] = other.get("amule_hashes")
    if not out.get("claimed_by") and other.get("claimed_by"):
        out["claimed_by"] = other.get("claimed_by")
        out["cancel_done"] = False
    return out


def to_cancel(data: dict, user: str) -> list:
    """req_ids cancelados desde la web que *user* tenía reclamados y
    cuya descarga aún no ha quitado de aMule."""
    out = []
    for req_id, entry in (data or {}).items():
        if (isinstance(entry, dict) and entry.get("status") == "cancelled"
                and entry.get("claimed_by") == user and not entry.get("cancel_done")):
            out.append(req_id)
    return out


def mark_cancel_done(data: dict, req_id: str, now: float | None = None) -> dict | None:
    """Dict NUEVO con la descarga de la cancelada ya quitada de aMule."""
    entry = _entry(data, req_id)
    if entry is None or entry.get("status") != "cancelled":
        return None
    result = dict(data)
    result[req_id] = {**entry, "cancel_done": True,
                      "updated_at": now if now is not None else time.time()}
    return result


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



# Basura que no identifica título (misma lista que sol_junk_tokens() en
# solicitudes-web/api/lib.php y JUNK_TOKENS en common.js -- mantener
# los tres sincronizados: es la regla que empareja lo que Jellyfin
# identifica mal o sin ProviderId Tmdb).
_JUNK_TOKENS = frozenset(
    "2160p 1080p 720p 480p 4k uhd uhq hd sd bluray brrip "
    "bdrip bdremux webdl webrip web hdtv dvdrip dvdscr dvd hdcam cam ts "
    "telesync x264 x265 h264 h265 hevc avc av1 ac3 aac dts dd51 atmos "
    "truehd flac mp3 dual latino castellano spanish espanol vos vose vo "
    "subtitulada subs sub extended extendida unrated directors theatrical "
    "remux proper repack r5 complete trilogy version especial special "
    "by".split())

_YEAR_RE = None  # perezoso, ver _year_in_name()


def _year_in_name(text: str):
    """Año 1900-2035 en *text* o None."""
    import re as _re

    global _YEAR_RE
    if _YEAR_RE is None:
        _YEAR_RE = _re.compile(r"\b(19|20)\d{2}\b")
    m = _YEAR_RE.search(text or "")
    if not m:
        return None
    year = int(m.group())
    return year if 1900 <= year <= 2035 else None


def norm_title(title: str) -> str:
    """Título a forma comparable: minúsculas, sin tildes, sin grupos
    [...]/(...), sin dominios, sin basura técnica y sin años (el año
    viaja aparte). Espejo de sol_norm_title() (PHP) y normTitle() (JS).
    """
    import re as _re
    import unicodedata as _ud

    text = _ud.normalize("NFKD", str(title or "")).lower()
    text = "".join(c for c in text if not _ud.combining(c))
    text = _re.sub(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", " ", text)
    text = _re.sub(
        r"\b(?:www\.)?\S+\.(?:com|net|org|es|io|to|me|tv|cc|mx|lat)\b",
        " ", text, flags=_re.IGNORECASE)
    text = _re.sub(r"[^a-z0-9 ]+", " ", text)
    words = []
    for word in text.split():
        word = word.strip()
        if not word or word in _JUNK_TOKENS:
            continue
        if _year_in_name(word) is not None and len(word) == 4:
            continue
        words.append(word)
    return " ".join(words)


def names_match(tmdb_title: str, tmdb_year, item_name: str,
                item_year=None) -> bool:
    """¿Es *item_name* (Jellyfin/Plex/archivo) la obra *tmdb_title*?
    Igual que sol_name_match() (PHP) y nameMatch() (JS): igualdad
    exacta, o el item empieza por el título (límite de palabra) con año
    compatible (si ambos lo traen). Conservador a propósito: mejor no
    emparejar que marcar done lo que no está. Nunca lanza."""
    try:
        tmdb_norm = norm_title(tmdb_title)
        item_norm = norm_title(item_name)
        if not tmdb_norm or not item_norm:
            return False
        if tmdb_norm == item_norm:
            return True
        if item_norm.startswith(tmdb_norm + " "):
            try:
                ty = int(str(tmdb_year or "")[:4] or 0)
            except (TypeError, ValueError):
                ty = 0
            iy = item_year
            if iy is None:
                iy = _year_in_name(item_name)
            try:
                iy = int(iy or 0)
            except (TypeError, ValueError):
                iy = 0
            if not ty or not iy or ty == iy:
                return True
        return False
    except Exception:
        return False


def match_any_name(title: str, year, items) -> bool:
    """¿Coincide (*title*, *year*) con alguno de *items* ([(nombre,
    año_o_None)])? Atajo para listas de Jellyfin/Plex ya normalizadas
    fuera (ver _download_requests_server_index en gui/app.py)."""
    try:
        for name, y in items or []:
            if names_match(title, year, name, y):
                return True
        return False
    except Exception:
        return False

def is_replacement(entry: dict) -> bool:
    """True si la solicitud es una sustitución de lo que ya hay en el
    servidor (flag "replace" de la web) -- nunca lanza."""
    try:
        return bool((entry or {}).get("replace"))
    except Exception:
        return False


def mark_replacement_launched(data: dict, req_id: str,
                              now: float | None = None) -> dict | None:
    """Devuelve un dict NUEVO con "replace_since" sellado en *req_id*
    (=ahora): a partir de ese instante una subida que case (ver
    uploaded_request_since) es LA sustitución. None si no existe.
    Nunca lanza."""
    import time as _time

    entry = _entry(data, req_id)
    if entry is None:
        return None
    ts = now if now is not None else _time.time()
    result = dict(data)
    result[req_id] = {**entry, "replace_since": ts, "updated_at": ts}
    return result


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
            # Los capítulos casi nunca llevan año en el nombre ("Reacher
            # 1x01 Titulo.mkv"): exigirlo hacía que NINGUNO casara (caso
            # real: sustitución de Reacher 1x01 subida y nunca dada por
            # hecha). Con año, debe coincidir; sin él, título + S/E.
            show = stem[:_episode_start(stem)].strip(" .-_")
            if year and _year_in_name(show) is None:
                return series_similarity(title, show, strict=True) >= 0.85
            return _title_ok(show)
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


def uploaded_request_since(history: list, entry: dict, since) -> bool:
    """Como uploaded_request, pero solo cuenta subidas con "ts" >=
    *since*: para sustituciones (flag "replace") lo que ya había no
    vale, solo la versión nueva subida tras lanzar la descarga. Sin
    *since* válido, False. Nunca lanza."""
    try:
        since = float(since)
    except (TypeError, ValueError):
        return False
    if since <= 0:
        return False
    def _ts(e):
        try:
            return float(e.get("ts") or 0)
        except (TypeError, ValueError):
            return 0.0

    recent = [e for e in history or []
              if isinstance(e, dict) and _ts(e) >= since]
    return uploaded_request(recent, entry)


#: Margen tras lanzar antes de dar una descarga por perdida en aMule
#: (recién añadida puede tardar en aparecer en la cola).
LOST_GRACE_SECONDS = 10 * 60


def set_amule_hashes(data: dict, req_id: str, hashes) -> dict | None:
    """Dict NUEVO con los hashes (MD4 hex) de lo que se lanzó en aMule
    para *req_id* -- para saber después si sigue en la cola (ver
    lost_in_amule). None si no existe. Nunca lanza."""
    entry = _entry(data, req_id)
    if entry is None:
        return None
    clean = sorted({str(h).lower() for h in (hashes or []) if h})
    result = dict(data)
    result[req_id] = {**entry, "amule_hashes": clean}
    return result


def _name_is_request(entry: dict, name: str) -> bool:
    """¿El nombre de archivo (cola de aMule o carpeta vigilada) es lo
    pedido? Capítulos: título + SxE (matches_upload); pelis: mismo
    título normalizado con año compatible (names_match). Nunca lanza."""
    try:
        if entry.get("media_type") == "tv" and entry.get("episode") is not None:
            return matches_upload(entry, name)
        stem = _remote_basename(name)
        return names_match(entry.get("title") or "", entry.get("year"), stem, _year_in_name(stem))
    except Exception:
        return False


def lost_in_amule(entry: dict, queue: list, local_names, now: float | None = None) -> bool:
    """True si una solicitud "downloading" ya NO está en aMule ni ha
    terminado: hay que liberarla para que se vuelva a lanzar (caso real:
    Obsession, descarga quitada de aMule y la solicitud colgada en
    "descargando" hasta los 7 días de STUCK).

    - *queue*: cola de aMule [{hash_hex, name}, ...] (quien llama solo
      pregunta si la pudo leer; sin cola no se decide nada).
    - *local_names*: archivos de la carpeta vigilada: si alguno es lo
      pedido, la descarga TERMINÓ y espera a subirse (no está perdida).
    - Con "amule_hashes" se busca por hash; las antiguas, sin ellos,
      por nombre. Recién lanzadas (LOST_GRACE_SECONDS) nunca.
    Solo capítulos sueltos y pelis (temporada/serie lanzan varias
    descargas y se resuelven por huecos). Nunca lanza."""
    try:
        if not isinstance(entry, dict) or entry.get("status") != "downloading":
            return False
        if entry.get("media_type") == "tv" and entry.get("episode") is None:
            return False
        ts = now if now is not None else time.time()
        if ts - float(entry.get("claimed_at") or 0) < LOST_GRACE_SECONDS:
            return False
        hashes = {str(h).lower() for h in (entry.get("amule_hashes") or [])}
        for it in queue or []:
            if not isinstance(it, dict):
                continue
            if hashes and str(it.get("hash_hex") or "").lower() in hashes:
                return False
            if not hashes and _name_is_request(entry, str(it.get("name") or "")):
                return False
        for name in local_names or []:
            if _name_is_request(entry, str(name)):
                return False
        return True
    except Exception:
        return False


STATUS_ES = {"pending": "Pendiente", "claimed": "Reclamada", "downloading": "Descargando",
             "done": "Lista", "failed": "Fallida", "cancelled": "Cancelada"}


def amule_hashes_to_cancel(entry: dict, queue: list) -> list:
    """Hashes de la cola de aMule [{hash_hex, name}] que son la descarga
    de *entry*: por sus "amule_hashes" o, si no los tiene (lanzadas
    antes de guardarlos), por nombre. Nunca lanza."""
    try:
        hashes = {str(h).lower() for h in (entry.get("amule_hashes") or [])}
        out = []
        for it in queue or []:
            if not isinstance(it, dict):
                continue
            h = str(it.get("hash_hex") or "").lower()
            if not h:
                continue
            if (h in hashes) if hashes else _name_is_request(entry, str(it.get("name") or "")):
                out.append(h)
        return out
    except Exception:
        return []


def history_rows(data: dict) -> list:
    """Solicitudes de la cola compartida como filas para Historial ("Ver
    todo el servidor" + "Solicitudes web"), la más reciente primero:
    {"kind": "solicitud", "ts", "name", "person", "status", "status_es",
    "detail"}. detail: quién la tiene (reclamada/descargando) o el último
    error. Nunca lanza con entradas raras."""
    rows = []
    for req_id, entry in (data or {}).items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        claimed_by = str(entry.get("claimed_by") or "").strip()
        error = str(entry.get("last_error") or "").strip()
        if status in ("claimed", "downloading") and claimed_by:
            detail = f"En el equipo de {claimed_by}"
        elif status == "cancelled":
            who = str(entry.get("cancelled_by") or "").strip()
            detail = f"Cancelada por {who}" if who else "Cancelada"
        elif error:
            detail = error
        else:
            detail = ""
        if is_replacement(entry):
            detail = ("Sustitución · " + detail) if detail else "Sustitución"
        try:
            ts = float(entry.get("requested_at") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        rows.append({"kind": "solicitud", "id": req_id, "ts": ts, "name": describe(entry),
                     "person": str(entry.get("requested_by") or "").strip(),
                     "status": status, "status_es": STATUS_ES.get(status, status or "¿?"),
                     "detail": detail})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows
