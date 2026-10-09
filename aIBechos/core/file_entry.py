"""
Una entrada de la lista de Archivos (FileEntry) y su (de)serialización para
session.json. Sin interfaz (la usa gui_qt/).
"""

from pathlib import Path

from core.api_client import detect_episode
from core.fmt import fmt_size as _fmt_size
from core.renamer import get_extension, is_book_file, is_comic_file

class FileEntry:
    def __init__(self, path):
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
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FileEntry":
        entry = cls(d["path"])
        entry.new_name   = d.get("new_name", "")
        entry.status     = d.get("status", "pendiente")
        entry.confidence = d.get("confidence", 0)
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
