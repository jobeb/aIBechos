"""
Tabla de la pestaña Archivos: un QAbstractTableModel sobre host.files (la
lista de FileEntry que comparte la lógica de core/app_files_core.py).

Sin paginación: la vista solo pinta las filas visibles, así que la tabla
aguanta miles de archivos (en Tk había que paginar para no agotar los
objetos de Windows). Las filas se actualizan una a una (update_entry) cuando
la lógica avisa con _update_row, sin rehacer la tabla entera.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from core.file_entry import _file_status_text
from core.fmt import fmt_speed
from gui_qt import theme
from gui_qt.actions import Action, ActionsRole

(COL_NAME, COL_DET, COL_NN, COL_DEST, COL_STAT, COL_BAR, COL_SPD, COL_SIZE,
 COL_FAV, COL_LOCK, COL_ACTIONS) = range(11)
HEADERS = ["Nombre original", "Detectado", "Nuevo nombre", "Destino", "Estado", "Subida FTP",
           "Vel.", "Peso", "★", "🔒", ""]
# Columnas ordenables -> clave de orden de la lógica (_sort_files_by_current_key)
SORT_KEYS = {COL_NAME: "name", COL_DET: "det", COL_NN: "nn", COL_DEST: "dest",
             COL_STAT: "stat", COL_SIZE: "size"}

EntryRole = Qt.UserRole + 1
ProgressRole = Qt.UserRole + 3

STATUS_COLORS = {
    "pendiente": theme.PENDING_COLOR, "buscando": theme.WARNING_COLOR, "listo": theme.SUCCESS_COLOR,
    "error": theme.ERROR_COLOR, "renombrado": theme.ACCENT, "en_cola": theme.QUEUED_COLOR,
    "subiendo": theme.WARNING_COLOR, "subido": theme.SUCCESS_COLOR, "auto": theme.ACCENT,
    "omitido": theme.WARNING_COLOR, "esperando_confirmacion": theme.WARNING_COLOR,
}


def detected_text(entry) -> str:
    det = entry.detected or {}
    if det.get("season"):
        return f"{det.get('title', '')} S{det.get('season', 0):02d}E{det.get('episode', 0) or 0:02d}"
    return det.get("title", "") or ""


class FilesModel(QAbstractTableModel):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._rows: list = []
        self._row_of: dict = {}
        self._dest_cache: dict = {}

    # ── Datos ──

    def reset(self):
        self.beginResetModel()
        self._rows = list(self.host.files)
        self._row_of = {id(e): i for i, e in enumerate(self._rows)}
        self._dest_cache = {}
        self.endResetModel()

    def entry_at(self, row: int):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, entry) -> int:
        return self._row_of.get(id(entry), -1)

    def update_entry(self, entry):
        row = self.row_of(entry)
        if row < 0:
            return False
        self._dest_cache.pop(id(entry), None)
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(HEADERS) - 1))
        return True

    def update_progress(self, entry):
        row = self.row_of(entry)
        if row >= 0:
            self.dataChanged.emit(self.index(row, COL_BAR), self.index(row, COL_SPD))

    def refresh_marks(self):
        if self._rows:
            self.dataChanged.emit(self.index(0, COL_FAV), self.index(len(self._rows) - 1, COL_LOCK))

    def _dest(self, entry) -> str:
        key = id(entry)
        if key not in self._dest_cache:
            try:
                self._dest_cache[key] = self.host._preview_remote_path(entry) or ""
            except Exception:
                self._dest_cache[key] = ""
        return self._dest_cache[key]

    # ── QAbstractTableModel ──

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return HEADERS[section]
        if role == Qt.ToolTipRole:
            return {COL_DET: "Lo que se dedujo del nombre del archivo -- doble clic para corregirlo",
                    COL_DEST: "Carpeta del servidor donde se subirá -- doble clic para cambiarla",
                    COL_FAV: "Favorito", COL_LOCK: "Reservado (protegido del borrado)"}.get(section)
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole):
        entry = self.entry_at(index.row())
        if entry is None:
            return None
        col = index.column()
        if role == EntryRole:
            return entry
        if role == ActionsRole:
            return self._actions(entry) if col == COL_ACTIONS else None
        if role == ProgressRole:
            if col != COL_BAR or not (entry.ftp_progress or entry.status in ("en_cola", "subiendo", "subido")):
                return None   # sin barra mientras no haya subida de por medio
            return float(entry.ftp_progress or 0.0)
        host = self.host
        if role == Qt.DisplayRole:
            if col == COL_NAME:
                return entry.name
            if col == COL_DET:
                return detected_text(entry)
            if col == COL_NN:
                return entry.new_name or ""
            if col == COL_DEST:
                return self._dest(entry)
            if col == COL_STAT:
                return _file_status_text(entry)
            if col == COL_SPD:
                return fmt_speed(entry.ftp_speed) if entry.status == "subiendo" and entry.ftp_speed > 0 else ""
            if col == COL_SIZE:
                try:
                    return host._file_size_text(entry)
                except Exception:
                    return ""
            if col == COL_FAV:
                return host._fav_symbol(entry) if entry.media_info else ""
            if col == COL_LOCK:
                return host._lock_symbol(entry) if entry.media_info and entry.status == "subido" else ""
            return None
        if role == Qt.ForegroundRole:
            if col == COL_DET:
                return QBrush(QColor(theme.PENDING_COLOR))
            if col == COL_NN:
                return QBrush(QColor(theme.ACCENT if entry.new_name else theme.PENDING_COLOR))
            if col == COL_DEST:
                return QBrush(QColor(theme.ACCENT if entry.remote_dir_override else theme.PENDING_COLOR))
            if col == COL_STAT:
                return QBrush(QColor(STATUS_COLORS.get(entry.status, theme.PENDING_COLOR)))
            if col in (COL_SPD, COL_SIZE):
                return QBrush(QColor(theme.PENDING_COLOR))
            if col == COL_FAV:
                return QBrush(QColor(theme.ACCENT if host._entry_is_favorite(entry) else theme.PENDING_COLOR))
            if col == COL_LOCK:
                return QBrush(QColor(theme.ACCENT if host._entry_is_reserved(entry) else theme.PENDING_COLOR))
            return None
        if role == Qt.TextAlignmentRole and col in (COL_FAV, COL_LOCK, COL_SPD):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if col == COL_NAME:
                return entry.path
            if col == COL_STAT and entry.error_msg and entry.status in ("error", "omitido"):
                return entry.error_msg
            if col == COL_DEST:
                return self._dest(entry) or None
            if col == COL_NN:
                return entry.new_name or None
            if col == COL_FAV:
                if not entry.media_info:
                    return "Identifica el archivo primero para poder añadirlo a favoritos"
                return (self.host.ctx.favorite_tooltip(entry.media_info.media_type, entry.media_info.tmdb_id)
                        if host._entry_is_favorite(entry)
                        else "Añadir a favoritos: lo verás en tu lista personal")
            if col == COL_LOCK:
                if not (entry.media_info and entry.status == "subido"):
                    return "Solo se puede reservar algo que ya esté identificado y subido."
                return self.host.ctx.reservation_tooltip(entry.media_info.media_type, entry.media_info.tmdb_id)
        return None

    def _actions(self, entry) -> list:
        uploading = entry.status in ("subiendo",)
        return [
            Action("skip", "⏹", "#c0392b", "Detener/saltar la subida de este archivo") if uploading else
            Action("upload", "▲", theme.ACCENT,
                   "Subir SOLO este archivo al servidor, a la carpeta que indica la columna \"Destino\".",
                   enabled=entry.status != "en_cola"),
            Action("play", "▶", theme.ICON_NEUTRAL,
                    "Reproducir directo en Jellyfin (ya está subido)."
                    if entry.status == "subido" else
                    "Abrir el archivo local con el reproductor predeterminado del sistema, para "
                    "comprobarlo antes de subirlo."),
            Action("remove", "✕", theme.ICON_IGNORE,
                   "Quitar el archivo de esta lista (pregunta si borrarlo también del disco o del servidor)."),
        ]


class ProgressDelegate(QStyledItemDelegate):
    """Barra de progreso de la subida dibujada (sin widget por fila)."""

    def paint(self, painter: QPainter, option, index):
        style = option.widget.style() if option.widget else None
        if style is not None:
            opt = type(option)(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, painter, option.widget)
        value = index.data(ProgressRole)
        if value is None:
            return
        r = option.rect.adjusted(6, 0, -6, 0)
        bar = QRect(r.left(), r.center().y() - 4, r.width(), 8)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(bar, 4, 4)
        if value > 0:
            fill = QRect(bar.left(), bar.top(), max(4, int(bar.width() * min(1.0, value))), bar.height())
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(fill, 4, 4)
        painter.restore()
