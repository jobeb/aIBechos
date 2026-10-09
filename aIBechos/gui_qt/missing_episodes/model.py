"""
Modelo en árbol de "Episodios que faltan": serie -> (avisos, temporadas) ->
episodios.

Es la pieza que arregla el cuelgue de la versión Tk: allí cada episodio eran
6-8 widgets nativos (miles al desplegar una temporada de anime larga); aquí un
QTreeView solo pinta las filas visibles y los nodos hijos se construyen la
primera vez que Qt los pide (al desplegar), así que una temporada de 1.000
episodios cuesta lo mismo que una de 10.

El modelo no decide QUÉ series se ven ni en qué orden -- eso lo calcula la
pestaña con core/missing_ep_rows.py (mismos filtros que la versión Tk) y se lo
pasa con set_rows(). Tampoco ejecuta acciones: solo describe qué botones tiene
cada fila (ActionsRole) y la pestaña reacciona al clic.
"""

from __future__ import annotations

import time
from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from core import missing_ep_rows as mer
from core.trending import trending_score, format_trending_score, explain_trending_score
from gui_qt import theme
from gui_qt.actions import ActionsRole

COL_NAME, COL_FAV, COL_LOCK, COL_PREMIERE, COL_SUMMARY, COL_TRENDING, COL_ACTIONS = range(7)
HEADERS = ["Serie / episodio", "★", "🔒", "Estreno", "Episodios que faltan", "Tendencia", "Acciones"]
SORT_KEYS = {COL_NAME: "name", COL_PREMIERE: "premiere", COL_SUMMARY: "summary", COL_TRENDING: "trending"}

NodeRole = Qt.UserRole + 1


class Node:
    __slots__ = ("kind", "parent", "row", "children", "r", "season", "line", "text", "lines")

    def __init__(self, kind, parent=None, row=0, r=None):
        self.kind = kind            # "series" | "info" | "season" | "episode"
        self.parent = parent
        self.row = row
        self.children = None        # None = todavía sin construir (perezoso)
        self.r = r                  # fila (dict) de la serie a la que pertenece
        self.season = None
        self.line = None            # episodio: (temporada, ep, título, nombre, fecha)
        self.text = ""              # info: texto del aviso
        self.lines = None           # temporada: líneas de sus episodios

    @property
    def tmdb_id(self):
        return self.r["tmdb_id"] if self.r else None

    def episode_key(self):
        return (self.tmdb_id, self.line[0], self.line[1]) if self.kind == "episode" else None


class MissingEpisodesModel(QAbstractItemModel):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._roots: list[Node] = []
        self._dub_cache: dict = {}
        self._tv_template = ctx.config.get("tv_template")
        self._summary_cache: dict = {}
        # tmdb_id/(tmdb_id, temporada, ep) -> color del botón ⬇ (en curso/ok/fallo)
        self.dl_state: dict = {}
        self.actions_provider = None    # callable(node) -> list[Action], lo fija la pestaña
        self._bold = QFont()
        self._bold.setBold(True)
        self._season_font = QFont()
        self._season_font.setBold(True)

    # ── Datos ──

    def set_rows(self, rows: list, dub_cache: dict) -> None:
        self.beginResetModel()
        self._dub_cache = dub_cache or {}
        self._tv_template = self.ctx.config.get("tv_template")
        self._summary_cache = {}
        self._roots = [Node("series", None, i, r) for i, r in enumerate(rows)]
        self.endResetModel()

    def series_rows(self) -> list:
        return [n.r for n in self._roots]

    def node(self, index: QModelIndex) -> Node | None:
        return index.internalPointer() if index.isValid() else None

    def _children(self, node: Node | None) -> list:
        if node is None:
            return self._roots
        if node.children is None:
            node.children = self._build_children(node)
        return node.children

    def _build_children(self, node: Node) -> list:
        out = []
        if node.kind == "series":
            r = node.r
            for text in mer.row_warnings(r):
                n = Node("info", node, len(out), r)
                n.text = text
                out.append(n)
            by_season: dict = {}
            for line in mer.episode_lines(r, self._tv_template):
                by_season.setdefault(line[0], []).append(line)
            for season in sorted(by_season):
                n = Node("season", node, len(out), r)
                n.season = season
                n.lines = by_season[season]
                out.append(n)
        elif node.kind == "season":
            for i, line in enumerate(node.lines):
                n = Node("episode", node, i, node.r)
                n.season = node.season
                n.line = line
                out.append(n)
        return out

    def summary_for(self, r: dict) -> tuple:
        key = id(r)
        if key not in self._summary_cache:
            self._summary_cache[key] = mer.row_summary(r, self._dub_cache)
        return self._summary_cache[key]

    # ── QAbstractItemModel ──

    def index(self, row, column, parent=QModelIndex()):
        children = self._children(self.node(parent))
        if 0 <= row < len(children) and 0 <= column < len(HEADERS):
            return self.createIndex(row, column, children[row])
        return QModelIndex()

    def parent(self, index):
        node = self.node(index)
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        node = self.node(parent)
        if node is not None and node.kind in ("info", "episode"):
            return 0
        return len(self._children(node))

    def hasChildren(self, parent=QModelIndex()):
        node = self.node(parent)
        if node is None:
            return bool(self._roots)
        if parent.column() != 0:
            return False
        if node.kind == "series":
            return True if node.children is None else bool(node.children)
        if node.kind == "season":
            return bool(node.lines)
        return False

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole:
            return {COL_FAV: "Favorito", COL_LOCK: "Reservada (protegida del borrado)",
                    COL_TRENDING: "Cuánto se ve últimamente en el servidor"}.get(section)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        node = self.node(index)
        if node is None:
            return None
        if role == NodeRole:
            return node
        col = index.column()
        if role == ActionsRole:
            if col == COL_ACTIONS and self.actions_provider is not None:
                return self.actions_provider(node)
            return None
        kind = node.kind
        if kind == "series":
            return self._series_data(node, col, role)
        if kind == "season":
            return self._season_data(node, col, role)
        if kind == "episode":
            return self._episode_data(node, col, role)
        if kind == "info" and col == COL_NAME:
            if role == Qt.DisplayRole:
                return node.text
            if role == Qt.ForegroundRole:
                return QBrush(QColor(theme.SUCCESS_COLOR if node.text.startswith("✓") else theme.WARNING_COLOR))
            if role == Qt.ToolTipRole:
                return node.text
        return None

    def _series_data(self, node, col, role):
        r = node.r
        tid = r["tmdb_id"]
        if role == Qt.DisplayRole:
            if col == COL_NAME:
                return r["name"]
            if col == COL_FAV:
                return "★" if self.ctx.is_favorite("tv", tid) else "☆"
            if col == COL_LOCK:
                return "🔒" if self.ctx.is_reserved("tv", tid) else "🔓"
            if col == COL_PREMIERE:
                return mer.fmt_air_date(r.get("first_air_date"))
            if col == COL_SUMMARY:
                return self.summary_for(r)[0]
            if col == COL_TRENDING:
                return format_trending_score(
                    trending_score(r.get("play_count", 0), r.get("last_played_ts"), time.time()))
        elif role == Qt.ForegroundRole:
            if col == COL_NAME and r.get("ignored"):
                return QBrush(QColor(theme.PENDING_COLOR))
            if col == COL_SUMMARY:
                return QBrush(QColor(theme.TONE_COLORS[self.summary_for(r)[1]]))
            if col == COL_FAV:
                return QBrush(QColor(theme.ACCENT if self.ctx.is_favorite("tv", tid) else theme.PENDING_COLOR))
            if col == COL_LOCK:
                return QBrush(QColor(theme.ACCENT if self.ctx.is_reserved("tv", tid) else theme.PENDING_COLOR))
            if col in (COL_PREMIERE, COL_TRENDING):
                return QBrush(QColor(theme.PENDING_COLOR))
        elif role == Qt.FontRole and col == COL_NAME:
            return self._bold
        elif role == Qt.TextAlignmentRole and col in (COL_FAV, COL_LOCK, COL_TRENDING):
            return int(Qt.AlignCenter)
        elif role == Qt.ToolTipRole:
            if col == COL_FAV:
                return self.ctx.favorite_tooltip("tv", tid)
            if col == COL_LOCK:
                return self.ctx.reservation_tooltip("tv", tid)
            if col == COL_SUMMARY:
                return self.summary_for(r)[2] or None
            if col == COL_TRENDING:
                return explain_trending_score(r.get("play_count", 0), r.get("last_played_ts"), time.time())
            if col == COL_NAME:
                return f"{r['name']} -- doble clic o flecha para desplegar; clic derecho para más opciones"
        return None

    def _season_data(self, node, col, role):
        r = node.r
        if role == Qt.DisplayRole:
            if col == COL_NAME:
                air = (r.get("season_air_dates") or {}).get(node.season, "")
                return mer.season_header_text(node.season, len(node.lines), air)
            if col == COL_SUMMARY:
                ignored = node.season in (r.get("ignored_seasons") or set())
                return "temporada ignorada" if ignored else None
        elif role == Qt.FontRole and col == COL_NAME:
            return self._season_font
        elif role == Qt.ForegroundRole and col == COL_SUMMARY:
            return QBrush(QColor(theme.PENDING_COLOR))
        return None

    def _episode_data(self, node, col, role):
        season, ep, title, name, air_date = node.line
        r = node.r
        if role == Qt.DisplayRole:
            if col == COL_NAME:
                return name
            if col == COL_PREMIERE:
                return mer.fmt_air_date(air_date)
            if col == COL_SUMMARY:
                ignored = ep in (r.get("ignored_episodes") or {}).get(season, set())
                return "ignorado" if ignored else None
        elif role == Qt.ForegroundRole:
            if col == COL_NAME:
                st = mer.episode_dub_status(r, season, ep, self._dub_cache)
                color = (theme.ERROR_COLOR if st == "absent"
                         else theme.WARNING_COLOR if st == "unverified" else theme.PENDING_COLOR)
                return QBrush(QColor(color))
            return QBrush(QColor(theme.PENDING_COLOR))
        elif role == Qt.ToolTipRole and col == COL_NAME:
            st = mer.episode_dub_status(r, season, ep, self._dub_cache)
            dub = {"absent": "sin doblaje castellano (confirmado)",
                   "unverified": "doblaje castellano sin verificar"}.get(st, "")
            return name + (f"\n{dub}" if dub else "")
        return None

    # ── Refrescos puntuales (sin reset: no pierde scroll/expansión) ──

    def refresh_series_columns(self) -> None:
        """Estrellas/candados: cambian por favoritos/reservas, no por datos."""
        if self._roots:
            self.dataChanged.emit(self.index(0, COL_FAV), self.index(len(self._roots) - 1, COL_LOCK))

    def refresh_node(self, node: Node) -> None:
        if node.parent is None:
            parent = QModelIndex()
        else:
            parent = self.createIndex(node.parent.row, 0, node.parent)
        self.dataChanged.emit(self.index(node.row, 0, parent), self.index(node.row, len(HEADERS) - 1, parent))

    def find_episode_node(self, key) -> Node | None:
        """Nodo YA construido de un episodio (None si su temporada nunca se
        desplegó o la fila ya no está -- p.ej. tras un refresco)."""
        tmdb_id, season, ep = key
        for s in self._roots:
            if s.tmdb_id != tmdb_id or not s.children:
                continue
            for c in s.children:
                if c.kind == "season" and c.season == season and c.children:
                    for e in c.children:
                        if e.line[1] == ep:
                            return e
        return None

    def find_season_node(self, tmdb_id, season) -> Node | None:
        for s in self._roots:
            if s.tmdb_id == tmdb_id and s.children:
                for c in s.children:
                    if c.kind == "season" and c.season == season:
                        return c
        return None

    def series_index(self, tmdb_id) -> QModelIndex:
        for n in self._roots:
            if n.tmdb_id == tmdb_id:
                return self.createIndex(n.row, 0, n)
        return QModelIndex()
