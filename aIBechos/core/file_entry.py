"""
Una entrada de la lista de Archivos (FileEntry) y su (de)serialización para
session.json. Sin interfaz (la usa gui_qt/).
"""

import time as _time
from pathlib import Path

from core.api_client import detect_episode
from core.fmt import fmt_size as _fmt_size
from core.renamer import get_extension, is_book_file, is_comic_file

#: Eventos que se guardan por archivo (los más recientes).
EVENT_LIMIT = 40


def _info_label(info) -> str:
    """"Título (año) 1x02" de un MediaInfo, para el registro y el tooltip."""
    if info is None:
        return ""
    g = lambda k: getattr(info, k, None)
    label = str(g("title") or "")
    if g("year"):
        label += f" ({g('year')})"
    season, episode = g("season"), g("episode")
    if isinstance(season, int) and isinstance(episode, int):
        label += f" {season}x{episode:02d}"
    elif episode is not None and g("media_type") == "libro":
        label += f" #{episode}"
    return label


def _info_key(info):
    if info is None:
        return None
    return tuple(getattr(info, k, None) for k in ("media_type", "tmdb_id", "title", "season", "episode"))         + (id(info) if not hasattr(info, "title") else 0,)


def detected_label(det) -> str:
    det = det or {}
    if det.get("season"):
        return f"{det.get('title', '')} S{det.get('season', 0):02d}E{det.get('episode', 0) or 0:02d}"
    if det.get("episode") and det.get("type") == "libro":
        return f"{det.get('title', '')} #{det.get('episode')}"
    return det.get("title", "") or ""


class FileEntry:
    """Además de sus datos, cada entrada apunta con la hora lo que le pasa
    (self.events: [ts, tipo, valor, motivo]): añadido, detección, cambios de
    estado (con el motivo de error_msg), identificación y nombre nuevo. Lo
    hacen solos los setters de abajo, así ninguno de los muchos sitios que
    cambian el estado tiene que acordarse; lo usa el tooltip de Archivos."""

    def __init__(self, path):
        self.events = []
        # Normalizar separadores (str(Path(...)) usa "\" en Windows) — sin
        # esto, la MISMA ruta con "/" y con "\" se trata como dos archivos
        # distintos en cualquier comparación por igualdad de texto (duplica
        # filas al detectar el mismo archivo dos veces, y hace fallar la
        # búsqueda en auto_processed.json, cuyas claves siempre usan "\").
        # La IDENTIDAD entre entradas/eventos/marcas va por path_key (ver
        # core/path_key.py: ignora además la caja en Windows) — self.path
        # se conserva tal cual para operar en disco. Propiedad calculada
        # (no atributo) para que no se quede obsoleta cuando entry.path
        # cambia (renombrados, post-proceso...).
        self.path         = str(Path(path))
        self.name         = Path(path).name
        self.ext          = get_extension(path)
        self.is_book      = is_book_file(self.path)
        self.is_comic     = is_comic_file(self.path)
        # folder_hint: apoyo para series/mangas cuyos archivos vienen
        # numerados a secas ("01.cbr") y el nombre real está en la carpeta
        # que los contiene -- ver core/api_client.py::detect_episode. Solo
        # se usa si el propio nombre de archivo no deja título aprovechable.
        self.detected     = detect_episode(self.name, is_book=self.is_book, is_comic=self.is_comic,
                                            folder_hint=Path(self.path).parent.name)
        self.media_info   = None
        self.new_name     = ""
        self.status       = "pendiente"
        self.error_msg    = ""
        self.ftp_progress = 0.0
        self.ftp_speed    = 0.0
        self.ftp_status   = ""
        self.confidence   = 0   # 0-100 porcentaje de confianza en la detección TMDB
        self.remote_dir_override = None   # carpeta remota elegida a mano, sustituye a la calculada por categoría/género
        self._last_known_size_text = ""   # ver App._file_size_text -- último tamaño leído con éxito
        self._last_known_size_bytes = None   # ver App._update_status_bar -- caché para no re-stat()ear todo self.files en cada fila actualizada
        self.is_slim = False   # viene de un adelgazamiento (ver evento "slim" del watcher): la fila muestra "Adelgazando"
        now = _time.time()
        self.events = [[now, "added", "", ""], [now, "detected", detected_label(self.detected), ""]]

    # ── Registro de eventos (ver docstring de la clase) ──

    def _log(self, kind: str, value: str = "", detail: str = "") -> None:
        try:
            self.events.append([_time.time(), kind, value, detail])
            if len(self.events) > EVENT_LIMIT:
                del self.events[:len(self.events) - EVENT_LIMIT]
        except Exception:
            pass

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        old = getattr(self, "_status", None)
        self._status = value
        if old is not None and value != old:
            detail = ""
            last = self.events[-1] if self.events else None
            # Motivo puesto justo antes del estado (algunos sitios lo hacen
            # en ese orden): pasa a ser el motivo de este cambio.
            if last and last[1] == "note" and _time.time() - last[0] < 2:
                detail = last[2]
                self.events.pop()
            self._log("status", value, detail)

    @property
    def error_msg(self):
        return self._error_msg

    @error_msg.setter
    def error_msg(self, value):
        old = getattr(self, "_error_msg", None)
        self._error_msg = value
        if old is None or not value or value == old:
            return
        last = self.events[-1] if self.events else None
        if last and last[1] == "status" and not last[3] and _time.time() - last[0] < 2:
            last[3] = value
        else:
            self._log("note", value)

    @property
    def media_info(self):
        return self._media_info

    @media_info.setter
    def media_info(self, value):
        old = getattr(self, "_media_info", "unset")
        self._media_info = value
        if old == "unset" or _info_key(value) == _info_key(old):
            return
        if value is None:
            self._log("unidentified")
        else:
            self._log("identified", _info_label(value))

    @property
    def new_name(self):
        return self._new_name

    @new_name.setter
    def new_name(self, value):
        old = getattr(self, "_new_name", None)
        self._new_name = value
        if old is not None and value and value != old:
            self._log("new_name", value)

    @property
    def detected(self):
        return self._detected

    @detected.setter
    def detected(self, value):
        old = getattr(self, "_detected", None)
        self._detected = value
        if old is not None and value != old:
            self._log("detected", detected_label(value))

    @property
    def path_key(self) -> str:
        """Clave canónica de identidad de esta entrada (ver
        core/path_key.py). Toda comparación entre entradas, eventos del
        watcher y marcas de auto_processed.json debe usar esto, nunca
        self.path directo (la caja varía según por dónde llegó la ruta)."""
        try:
            from core.path_key import canon_path
            return canon_path(self.path)
        except Exception:
            return self.path or ""

    def to_dict(self) -> dict:
        status = self.status
        # Persistir progreso de subida a medias para que al reabrir la app
        # la barra no fluctúe 0%→45%→0% (ver issue barra fluctuante). Antes se
        # demotaba subiendo/en_cola→pendiente y se perdía ftp_progress, así que
        # _begin_ftp_upload reseteaba a 0% y el resume a 45% alternaba cada 2 s
        # con los "Reintento…" que reaplicaban el 0% guardado.
        ftp_progress = float(getattr(self, "ftp_progress", 0.0) or 0.0)
        if status in ("buscando", "auto"):
            status = "pendiente"
        elif status in ("subiendo", "en_cola"):
            # Conservar como en_cola con progreso para que al reiniciar se vea
            # "En cola 45%" y no "pendiente 0%" y el siguiente upload no
            # parpadee. El usuario puede re-lanzar sin duplicar.
            status = "en_cola"
        return {
            "path":       self.path,
            "new_name":   self.new_name,
            "status":     status,
            # El motivo de "error"/"omitido": sin guardarlo, tras reiniciar
            # el tooltip solo podía decir que se omitió, no por qué.
            "error_msg":  self.error_msg if status in ("error", "omitido") else "",
            "confidence": self.confidence,
            "media_info": _mediainfo_to_dict(self.media_info) if self.media_info else None,
            "remote_dir_override": self.remote_dir_override,
            # Solo relevante para libros/cómics (self.is_book) -- persiste
            # la elección manual de _set_book_comic_type, que si no se
            # perdería al reiniciar la app (from_dict() recalcularía is_comic
            # solo por extensión, como al añadir el archivo por primera vez).
            "is_comic": self.is_comic,
            # Ver App._file_size_text -- sin esto, un archivo ya procesado
            # (movido a "procesados/" o borrado tras subir, según "Acción
            # tras subir") se queda con la columna "Peso" en blanco justo
            # tras reiniciar la app: entry.path ya no existe en disco, y
            # sin el último tamaño conocido guardado aquí no hay nada que
            # mostrar en su lugar (el caché en memoria no sobrevive un
            # reinicio si no se persiste).
            "last_known_size_bytes": self._last_known_size_bytes,
            "ftp_progress": ftp_progress,
            "is_slim": bool(getattr(self, "is_slim", False)),
            "events": [list(e) for e in self.events[-EVENT_LIMIT:]],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FileEntry":
        entry = cls(d["path"])
        entry.new_name   = d.get("new_name", "")
        entry.status     = d.get("status", "pendiente")
        entry.confidence = d.get("confidence", 0)
        entry.error_msg  = d.get("error_msg", "") or ""
        entry.is_slim    = bool(d.get("is_slim", False))
        entry.remote_dir_override = d.get("remote_dir_override")
        # Restaura la elección manual de _set_book_comic_type si difiere de
        # la que ya sale por extensión (is_comic_file en core/renamer.py) --
        # hay que recalcular entry.detected también, porque se calculó en
        # cls(d["path"]) con el is_comic por defecto, no con el guardado.
        saved_is_comic = d.get("is_comic")
        if entry.is_book and saved_is_comic is not None and saved_is_comic != entry.is_comic:
            entry.is_comic = saved_is_comic
            entry.detected = detect_episode(entry.name, is_book=entry.is_book, is_comic=entry.is_comic,
                                             folder_hint=Path(entry.path).parent.name)
        entry._last_known_size_bytes = d.get("last_known_size_bytes")
        if entry._last_known_size_bytes is not None:
            entry._last_known_size_text = _fmt_size(entry._last_known_size_bytes)
        # Restaurar progreso de subida previa (ver to_dict: evita fluctuación 0%↔45%)
        try:
            entry.ftp_progress = float(d.get("ftp_progress", 0.0) or 0.0)
        except Exception:
            entry.ftp_progress = 0.0
        entry.ftp_progress = max(0.0, min(1.0, entry.ftp_progress))
        if entry.status == "en_cola" and entry.ftp_progress > 0:
            entry.ftp_status = f"{entry.ftp_progress*100:.0f}% (pendiente de reanudar)"
        mi = d.get("media_info")
        if mi:
            from core.api_client import MediaInfo
            entry.media_info = MediaInfo(
                tmdb_id        = mi.get("tmdb_id", 0),
                media_type     = mi.get("media_type", "tv"),
                title          = mi.get("title", ""),
                original_title = mi.get("original_title", ""),
                year           = mi.get("year", ""),
                poster_url     = mi.get("poster_url"),
                season         = mi.get("season"),
                episode        = mi.get("episode"),
                episode_title  = mi.get("episode_title"),
                overview       = mi.get("overview", ""),
                genre_ids      = mi.get("genre_ids", []),
            )
        # El registro guardado sustituye al que han ido apuntando los setters
        # al reconstruir la entrada (con la hora de ahora, no la real). Una
        # sesión de antes de este campo se queda sin registro: mejor nada que
        # fechas inventadas.
        events = d.get("events")
        entry.events = [list(e) for e in events if isinstance(e, (list, tuple)) and len(e) == 4
                        and isinstance(e[0], (int, float))][-EVENT_LIMIT:] \
            if isinstance(events, list) else []
        return entry


_STATUS_RANK = {
    "subido": 5, "renombrado": 4, "listo": 3, "en_cola": 3, "subiendo": 3,
    "auto": 2, "omitido": 1, "error": 1, "pendiente": 0, "buscando": 0,
}


def _dedupe_entries(entries: list) -> list:
    """Colapsa entradas del mismo archivo (ver core/path_key.py:
    ignora separadores y, en Windows, la caja) en una sola — puede
    haber duplicados en session.json de antes de normalizar (la misma
    ruta con "/" y con "\\", o con distinta caja, se guardaba como dos
    archivos distintos). Se queda con la de estado más avanzado; en
    empate, con la que tenga media_info."""
    from core.path_key import canon_path
    best = {}
    order = []
    for e in entries:
        try:
            key = e.path_key
        except Exception:
            try:
                key = canon_path(e.path)
            except Exception:
                key = e.path
        if key not in best:
            best[key] = e
            order.append(key)
            continue
        cur = best[key]
        rank_new = _STATUS_RANK.get(e.status, 0)
        rank_cur = _STATUS_RANK.get(cur.status, 0)
        if rank_new > rank_cur or (rank_new == rank_cur and e.media_info and not cur.media_info):
            best[key] = e
    return [best[k] for k in order]


def _entries_from_dicts(dicts: list) -> tuple:
    """([FileEntry], descartados) a partir de los dicts crudos de
    session.json -- CADA entrada se aísla en su propio try/except: una
    entrada inválida (sin "path", forma inesperada de otra versión...)
    se cuenta y se salta, en vez de tumbar la carga entera y dejar
    Archivos vacío (real: Victor 2026-09-11, sin rastro en el log porque
    _load_session tragaba la excepción en silencio)."""
    entries, skipped = [], 0
    for d in dicts or []:
        try:
            entries.append(FileEntry.from_dict(d))
        except Exception:
            skipped += 1
    return entries, skipped


def _mediainfo_to_dict(info) -> dict:
    return {
        "tmdb_id":        info.tmdb_id,
        "media_type":     info.media_type,
        "title":          info.title,
        "original_title": info.original_title,
        "year":           info.year,
        "poster_url":     info.poster_url,
        "season":         info.season,
        "episode":        info.episode,
        "episode_title":  info.episode_title,
        "overview":       info.overview,
        "genre_ids":      info.genre_ids,
    }


STATUS_LABELS = {"en_cola": "En cola",
                  "esperando_confirmacion": "Espera"}   # textos de estado que no quedan bien con .capitalize()


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.capitalize())


def _file_status_text(entry) -> str:
    """Texto de la columna Estado para una fila de Archivos: "Adelgazando"
    en vez de "Subiendo"/"En cola" cuando el archivo viene de un
    adelgazamiento (ver FileEntry.is_slim, lo marca el watcher al emparejar
    el pendiente). El estado INTERNO no cambia ("subiendo"/"en_cola"):
    filtros, objetivos de subida, rankings y repos solo ven ese."""
    try:
        if getattr(entry, "is_slim", False) and getattr(entry, "status", "") in ("subiendo", "en_cola"):
            return "Adelgazando"
    except Exception:
        pass
    try:
        return _status_label(entry.status)
    except Exception:
        return ""


# ── Tooltip de Archivos: qué es, en qué estado está y por qué ──

#: Qué significa cada estado, para quien no sabe qué hace la app por dentro.
STATUS_HELP = {
    "pendiente": "Aún no está identificado: selecciónalo y busca el título, o espera a la búsqueda automática.",
    "buscando": "Buscando el título (TMDB, OpenLibrary/Google Books o ComicVine).",
    "listo": "Identificado y con nombre nuevo: listo para renombrar y subir.",
    "renombrado": "Renombrado en tu disco; falta subirlo al servidor.",
    "en_cola": "Esperando turno para subir (hay otras subidas delante, o se reanudará donde se quedó).",
    "subiendo": "Subiéndose al servidor ahora mismo.",
    "subido": "Subido al servidor.",
    "auto": "Lo está procesando el modo automático (carpeta vigilada).",
    "omitido": "No se ha subido: se ha saltado (abajo, el motivo).",
    "error": "Algo ha fallado y no se ha terminado.",
    "esperando_confirmacion": "Hay un aviso esperando tu respuesta (por ejemplo, si sobrescribir un archivo "
                              "que ya está en el servidor).",
}


def event_text(kind: str, value: str = "", detail: str = "") -> str:
    if kind == "added":
        return "Añadido a la lista"
    if kind == "detected":
        return f"Detectado del nombre: {value}" if value else "Sin título reconocible en el nombre"
    if kind == "status":
        text = _status_label(value) if value else ""
        if detail and detail.lower().startswith(text.lower()):
            return detail          # el motivo ya empieza por el estado
        return f"{text}: {detail}" if detail else text
    if kind == "identified":
        return f"Identificado como {value}"
    if kind == "unidentified":
        return "Identificación quitada"
    if kind == "new_name":
        return f"Nuevo nombre: {value}"
    return value or detail


def upload_record(history: list, entry):
    """El último registro de subida correcta de *entry* en upload_history.json
    (por ruta local o por nombre original), o None. Para las entradas de
    antes del registro de eventos, que no saben cuándo se subieron."""
    path = getattr(entry, "path", "")
    name = getattr(entry, "name", "")
    for rec in reversed(history or []):
        if not isinstance(rec, dict) or rec.get("status") != "ok":
            continue
        if (path and rec.get("local_path") == path) or (name and rec.get("filename") == name):
            return rec
    return None


def _last_event(entry, kind, value=None):
    for e in reversed(getattr(entry, "events", None) or []):
        if e[1] == kind and (value is None or e[2] == value):
            return e
    return None


def _status_reason(entry, status: str) -> str:
    """Por qué está en *status*: error_msg o, si no lo hay (sesiones de
    antes de guardarlo), el motivo apuntado en su último cambio a ese
    estado en el registro de eventos."""
    reason = getattr(entry, "error_msg", "") or ""
    if not reason:
        ev = _last_event(entry, "status", status)
        reason = (ev[3] if ev else "") or ""
    if not reason and status == "omitido":
        reason = ("No consta (se omitió antes de que la app guardara el motivo); "
                  "mira el registro del modo automático o vuelve a procesarlo.")
    return reason


def entry_details(entry, dest: str = "", size_text: str = "", record=None) -> dict:
    """Lo que enseña el tooltip de una fila de Archivos (sin formato):
    {"title", "status", "status_help", "reason", "rows": [(etiqueta, valor)],
     "events": [(ts, texto)]}. *record*: ver upload_record."""
    status = getattr(entry, "status", "") or ""
    reason = _status_reason(entry, status) if status in ("error", "omitido")         else getattr(entry, "error_msg", "") or ""
    rows = [("Archivo", getattr(entry, "path", ""))]
    if size_text:
        rows.append(("Peso", size_text))
    det = detected_label(getattr(entry, "detected", None))
    rows.append(("Detectado del nombre", det or "—"))
    info = getattr(entry, "media_info", None)
    if info is not None:
        label = _info_label(info)
        kind = getattr(info, "media_type", "")
        genres = getattr(info, "genre_ids", None) or []
        source = ("cómic" if "comic" in genres else "libro") if kind == "libro" else \
            {"tv": "serie · TMDB", "movie": "película · TMDB"}.get(kind, kind)
        conf = getattr(entry, "confidence", 0) or 0
        extra = f" · confianza {conf}%" if conf else ""
        rows.append(("Identificado como", f"{label} ({source}{extra})" if source else label + extra))
        ev = _last_event(entry, "identified")
        if ev:
            rows.append(("Identificado el", ev[0]))
    else:
        rows.append(("Identificado como", "sin identificar"))
    if getattr(entry, "new_name", ""):
        rows.append(("Nuevo nombre", entry.new_name))
    if dest:
        rows.append(("Destino", dest))
    if status == "subido":
        ev = _last_event(entry, "status", "subido")
        when = ev[0] if ev else (record or {}).get("ts")
        if when:
            rows.append(("Subido el", when))
        if record:
            if record.get("remote"):
                rows.append(("En el servidor", record["remote"]))
            if record.get("person"):
                rows.append(("Subido por", record["person"]))
    added = _last_event(entry, "added")
    if added:
        rows.append(("Añadido el", added[0]))
    events = [(e[0], event_text(e[1], e[2], e[3])) for e in (getattr(entry, "events", None) or [])]
    return {
        "title": getattr(entry, "new_name", "") or getattr(entry, "name", ""),
        "status": _file_status_text(entry),
        "status_help": STATUS_HELP.get(status, ""),
        "reason": reason if status in ("error", "omitido", "auto", "esperando_confirmacion") else "",
        "rows": rows,
        "events": events,
    }


# ── Filtro de la lista de Archivos ──

#: Grupos del desplegable de estado -> estados internos (None = todos).
FILTER_GROUPS = {
    "todos": None,
    "pendientes": {"pendiente", "buscando", "listo", "renombrado", "esperando_confirmacion"},
    "en_curso": {"en_cola", "subiendo", "auto"},
    "subidos": {"subido"},
    "errores": {"error"},
    "omitidos": {"omitido"},
}


def _fold(text) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def entry_matches(entry, text: str = "", group: str = "todos") -> bool:
    """¿Se ve *entry* con el filtro? *text*: todas sus palabras deben estar
    en el nombre, el nombre nuevo, lo detectado, el título identificado o el
    estado (sin distinguir mayúsculas ni acentos). *group*: FILTER_GROUPS."""
    wanted = FILTER_GROUPS.get(group)
    if wanted is not None and getattr(entry, "status", "") not in wanted:
        return False
    words = _fold(text).split()
    if not words:
        return True
    info = getattr(entry, "media_info", None)
    hay = _fold(" ".join([
        getattr(entry, "name", ""), getattr(entry, "new_name", "") or "",
        detected_label(getattr(entry, "detected", None)),
        _info_label(info) if info is not None else "", _file_status_text(entry)]))
    return all(w in hay for w in words)
