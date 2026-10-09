"""
Pestaña "Liberar espacio" en Qt: candidatas a borrar del servidor (lo que menos
se ve), con filtros, y "adelgazar" (bajar versiones más ligeras de capítulos
que pesan mucho más de lo normal). Solo un informe: nunca borra nada sin
confirmación explícita, elemento a elemento.

Toda la lógica (análisis, episodios por carpeta, candidatas a adelgazar,
borrado, reescaneo, lista compartida) es la de core/app_cleanup_core.py vía el
host; los filtros, los de core/cleanup_candidates.py. Esta clase es la vista e
implementa los ganchos que el host le reenvía (apply_filters, render...).
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSplitter, QTreeView, QVBoxLayout, QWidget)

from core.fmt import fmt_size
from core.status_colors import ERROR_COLOR, PENDING_COLOR, WARNING_COLOR
from core.trending import explain_trending_score, format_trending_score, trending_score
from gui_qt import theme
from gui_qt.actions import Action, ActionsDelegate, ActionsRole
from gui_qt.bridge import run_in_thread, ui
from gui_qt.ficha import TmdbFicha

C_NAME, C_SIZE, C_TREND, C_FAV, C_LOCK, C_ACT = range(6)
HEADERS = ["Candidata", "Tamaño", "Tendencia", "★", "🔒", ""]
SORT_KEYS = {C_NAME: "candidata", C_SIZE: "tamano", C_TREND: "tendencia"}
WATCHED = ["(sin filtro)", "Nunca vista", "Vista, sin repetir en", "Pocas reproducciones",
           "Sin datos de visionado"]
SRC_TXT = {"desired": "deseado", "typical": "mediana", "resolution_cap": "techo resolución"}
FILES_PER_SEASON = 30


class _Text:
    """Para _save_cleanup_desired, que lee el valor con .get() (en Tk, un CTkEntry)."""

    def __init__(self, text):
        self._t = text

    def get(self):
        return self._t


class Node:
    __slots__ = ("kind", "parent", "row", "children", "item", "data", "text", "season")

    def __init__(self, kind, item, parent=None, row=0, text="", data=None, season=None):
        self.kind = kind          # item | flat | info | desired | season | file
        self.item = item
        self.parent = parent
        self.row = row
        self.children = []
        self.text = text
        self.data = data          # dict de candidata a adelgazar (file/flat)
        self.season = season


def _ep_prefix(name: str) -> str:
    try:
        from core.download_quality import _parse_season_episode
        se = _parse_season_episode(name or "")
        if se:
            low = (name or "").lower()
            nxnn, sxey = f"{se[0]}x{se[1]:02d}", f"s{se[0]:02d}e{se[1]:02d}"
            return "" if (nxnn in low or sxey in low) else f"{nxnn} "
    except Exception:
        pass
    return ""


def _slim_line(d: dict) -> str:
    src = SRC_TXT.get(d.get("source") or "", "")
    return (f"{_ep_prefix(d.get('name', ''))}{d.get('name', '')} — {fmt_size(d.get('size') or 0)} "
            f"({(d.get('ratio') or 0):.1f}× objetivo {fmt_size(d.get('target') or 0)}"
            f"{', ' + src if src else ''} · ahorra {fmt_size(d.get('saving') or 0)})")


class CleanupModel(QAbstractItemModel):
    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.roots: list = []
        self._bold = QFont()
        self._bold.setBold(True)

    def set_roots(self, roots):
        self.beginResetModel()
        self.roots = roots
        self.endResetModel()

    def node(self, index):
        return index.internalPointer() if index.isValid() else None

    def _kids(self, node):
        return self.roots if node is None else node.children

    def index(self, row, column, parent=QModelIndex()):
        kids = self._kids(self.node(parent))
        if 0 <= row < len(kids):
            return self.createIndex(row, column, kids[row])
        return QModelIndex()

    def parent(self, index):
        n = self.node(index)
        if n is None or n.parent is None:
            return QModelIndex()
        return self.createIndex(n.parent.row, 0, n.parent)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self._kids(self.node(parent)))

    def hasChildren(self, parent=QModelIndex()):
        n = self.node(parent)
        if n is None:
            return bool(self.roots)
        if parent.column() != 0:
            return False
        if n.kind == "item":
            return not getattr(n.item, "loose_file_paths", None)   # carpeta: desplegable
        return bool(n.children) or n.kind == "season"

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole):
        n = self.node(index)
        if n is None:
            return None
        col = index.column()
        if role == ActionsRole:
            return self.tab.actions_for(n) if col == C_ACT else None
        it = n.item
        host = self.tab.host
        if n.kind == "item":
            if role == Qt.DisplayRole:
                if col == C_NAME:
                    icon = "📺" if it.media_type == "tv" else "🎬"
                    return f"{icon} {it.name} ({it.year})" if it.year else f"{icon} {it.name}"
                if col == C_SIZE:
                    return fmt_size(it.size_bytes)
                if col == C_TREND:
                    return format_trending_score(trending_score(it.play_count, it.last_played_ts, time.time()))
                if col == C_FAV:
                    return "★" if host._is_favorite(it.media_type, it.tmdb_id) else "☆"
                if col == C_LOCK:
                    return "🔒" if host._is_reserved(it.media_type, it.tmdb_id) else "🔓"
            elif role == Qt.ToolTipRole:
                if col == C_NAME:
                    return f"{it.name}\n{host._cleanup_item_reason_text(it)}\n{it.ftp_path}"
                if col == C_TREND:
                    return explain_trending_score(it.play_count, it.last_played_ts, time.time())
                if col == C_FAV:
                    return ("Quitar de favoritos: volverá a aparecer como candidata a borrar."
                            + host.ctx.favorite_attribution(it.media_type, it.tmdb_id)
                            if host._is_favorite(it.media_type, it.tmdb_id) else
                            "Marcar como favorito: deja de salir en esta lista de candidatas a borrar.")
                if col == C_LOCK:
                    return ("Liberar la reserva: deja de estar protegida."
                            + host.ctx.reservation_attribution(it.media_type, it.tmdb_id)
                            if host._is_reserved(it.media_type, it.tmdb_id)
                            else "Reservar: la protege del borrado para todos. Ocupa cuota de reserva.")
            elif role == Qt.FontRole and col == C_NAME:
                return self._bold
            elif role == Qt.ForegroundRole:
                if col in (C_SIZE, C_TREND):
                    return QBrush(QColor(PENDING_COLOR))
                if col == C_FAV:
                    return QBrush(QColor(theme.ACCENT if host._is_favorite(it.media_type, it.tmdb_id) else PENDING_COLOR))
                if col == C_LOCK:
                    return QBrush(QColor(theme.ACCENT if host._is_reserved(it.media_type, it.tmdb_id) else PENDING_COLOR))
            elif role == Qt.TextAlignmentRole and col in (C_FAV, C_LOCK, C_TREND):
                return int(Qt.AlignCenter)
            return None
        if col != C_NAME:
            if n.kind == "flat" and role == Qt.DisplayRole and col == C_SIZE:
                return fmt_size(n.data.get("size") or 0)
            return None
        if role == Qt.DisplayRole:
            return n.text
        if role == Qt.ToolTipRole:
            return n.text
        if role == Qt.ForegroundRole and n.kind in ("info", "desired"):
            return QBrush(QColor(WARNING_COLOR if n.text.startswith("No se pudo") else PENDING_COLOR))
        if role == Qt.FontRole and n.kind in ("season", "flat"):
            return self._bold
        return None


class CleanupTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self.config = host.config_data
        self._scanning = False
        self._syncing = False
        saved = (self.config.get("table_sort", {}) or {}).get("limpiar")
        if isinstance(saved, dict) and "key" in saved:
            self._sort_key, self._sort_asc = saved.get("key"), bool(saved.get("asc", True))
        else:
            self._sort_key, self._sort_asc = "candidata", True
        self._category_boxes = {}
        self._build_ui()
        host.cleanup_view = self
        self._load_cached()

    # ── Construcción ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        hint = QLabel("Solo un informe -- nunca borra nada por su cuenta.")
        hint.setStyleSheet(f"color: {PENDING_COLOR};")
        head.addWidget(hint)
        head.addStretch(1)
        t = QLabel("Liberar espacio")
        t.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(t)
        head.addStretch(1)
        self.scan_btn = QPushButton("🔍 Analizar servidor")
        self.scan_btn.setProperty("accent", True)
        self.scan_btn.clicked.connect(self.start_scan)
        head.addWidget(self.scan_btn)
        root.addLayout(head)
        self.status_lbl = QLabel("Sin analizar todavía -- pulsa \"Analizar servidor\"")
        self.status_lbl.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(self.status_lbl)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        self.progress.hide()
        root.addWidget(self.progress)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_filters())

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.results_lbl = QLabel("")
        cl.addWidget(self.results_lbl)
        self.model = CleanupModel(self, self)
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setMouseTracking(True)
        self.view.setIndentation(18)
        self.delegate = ActionsDelegate(self.view)
        self.delegate.actionTriggered.connect(self._on_action)
        self.view.setItemDelegateForColumn(C_ACT, self.delegate)
        hdr = self.view.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        for c, w in ((C_SIZE, 85), (C_TREND, 80), (C_FAV, 28), (C_LOCK, 28), (C_ACT, 104)):
            hdr.setSectionResizeMode(c, QHeaderView.Interactive)
            hdr.resizeSection(c, w)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header)
        self._update_sort_indicator()
        self.view.expanded.connect(self._on_expanded)
        self.view.collapsed.connect(self._on_collapsed)
        self.view.clicked.connect(self._on_clicked)
        self.view.selectionModel().currentRowChanged.connect(lambda cur, _p: self._on_current(cur))
        cl.addWidget(self.view, 1)
        split.addWidget(center)

        self.ficha = TmdbFicha(self.host.tmdb, "Pulsa una candidata\npara ver su ficha")
        split.addWidget(self.ficha)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 800, 270])
        root.addWidget(split, 1)

    def _build_filters(self):
        frame = QFrame()
        frame.setObjectName("card")
        frame.setMinimumWidth(240)
        outer = QVBoxLayout(frame)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        f = QVBoxLayout(body)

        def title(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: bold;")
            f.addWidget(lbl)

        def note(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {PENDING_COLOR}; font-size: 8pt;")
            f.addWidget(lbl)

        def num_row(check_text, default, unit):
            row = QHBoxLayout()
            cb = QCheckBox(check_text) if check_text else None
            if cb:
                row.addWidget(cb)
            e = QLineEdit(default)
            e.setFixedWidth(50)
            row.addWidget(e)
            row.addWidget(QLabel(unit))
            row.addStretch(1)
            f.addLayout(row)
            return cb, e

        note("Los filtros activos se combinan entre sí -- sin ninguno marcado se ve la lista completa.")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por nombre...")
        self.search.returnPressed.connect(self.apply_filters)
        f.addWidget(self.search)
        self.age_cb, self.age_e = num_row("Añadida +", "12", "meses")
        title("Visionado")
        self.watched = QComboBox()
        self.watched.addItems(WATCHED)
        self.watched.currentTextChanged.connect(self._on_watched_mode)
        f.addWidget(self.watched)
        self.not_rewatched_w = QWidget()
        r1 = QHBoxLayout(self.not_rewatched_w)
        r1.setContentsMargins(0, 0, 0, 0)
        self.not_rewatched_e = QLineEdit("12")
        self.not_rewatched_e.setFixedWidth(50)
        r1.addWidget(self.not_rewatched_e)
        r1.addWidget(QLabel("meses sin repetir"))
        r1.addStretch(1)
        f.addWidget(self.not_rewatched_w)
        self.max_play_w = QWidget()
        r2 = QHBoxLayout(self.max_play_w)
        r2.setContentsMargins(0, 0, 0, 0)
        self.max_play_e = QLineEdit("1")
        self.max_play_e.setFixedWidth(50)
        r2.addWidget(self.max_play_e)
        r2.addWidget(QLabel("máx. veces vista"))
        r2.addStretch(1)
        f.addWidget(self.max_play_w)
        self.not_rewatched_w.hide()
        self.max_play_w.hide()
        self.size_cb, self.size_e = num_row("Más de", "10", "GB")
        title("Tipo")
        self.type_tv = QCheckBox("Series")
        self.type_tv.setChecked(True)
        self.type_movie = QCheckBox("Películas")
        self.type_movie.setChecked(True)
        self.duplicates = QCheckBox("Solo duplicados")
        for w in (self.type_tv, self.type_movie, self.duplicates):
            f.addWidget(w)
        note("El mismo contenido identificado en 2 o más sitios del servidor.")
        title("Adelgazar")
        self.slim_only = QCheckBox("Solo adelgazables")
        self.flat = QCheckBox("Por capítulo")
        self.flat.toggled.connect(lambda _c: self.apply_filters())
        f.addWidget(self.slim_only)
        f.addWidget(self.flat)
        _cb, self.slim_ratio_e = num_row("", "2", "× el objetivo")
        note("Capítulos muy por encima de su peso objetivo (deseado, mediana de la serie o techo por resolución).")
        title("Protegidas")
        self.show_fav = QCheckBox("Mostrar favoritos")
        self.show_res = QCheckBox("Mostrar reservados")
        f.addWidget(self.show_fav)
        f.addWidget(self.show_res)
        self.quota_lbl = QLabel("")
        self.quota_lbl.setWordWrap(True)
        f.addWidget(self.quota_lbl)
        title("Categoría")
        self.cat_box = QVBoxLayout()
        f.addLayout(self.cat_box)
        apply_btn = QPushButton("Aplicar filtros")
        apply_btn.setProperty("accent", True)
        apply_btn.setToolTip("Aplicar los filtros de arriba a la lista. Sin ningún filtro marcado se "
                             "ve la lista COMPLETA, no una preselección.")
        apply_btn.clicked.connect(self.apply_filters)
        f.addWidget(apply_btn)
        f.addStretch(1)
        return frame

    def _on_watched_mode(self, text):
        self.not_rewatched_w.setVisible(text == "Vista, sin repetir en")
        self.max_play_w.setVisible(text == "Pocas reproducciones")

    # ── Datos ──

    def _load_cached(self):
        from core.cleanup_candidates_cache import load_cache
        cached = load_cache()
        if cached.get("items"):
            self.host._cleanup_raw_items = cached["items"]
            self.host._cleanup_last_scan_ts = cached.get("last_scan_ts")
            self.host._cleanup_scanned_by = cached.get("scanned_by", "")
            self._populate_categories()
            self.apply_filters()
            self._show_scan_age()

    def _show_scan_age(self):
        h = self.host
        age = time.time() - h._cleanup_last_scan_ts if h._cleanup_last_scan_ts else None
        by = f" (por {h._cleanup_scanned_by})" if h._cleanup_scanned_by else ""
        self.set_status(f"Último análisis: {h._fmt_cleanup_scan_age(age)}{by} -- pulsa \"Analizar servidor\" "
                        "para actualizar")

    def _populate_categories(self):
        while self.cat_box.count():
            w = self.cat_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._category_boxes = {}
        names = sorted({it.category_name for it in self.host._cleanup_raw_items if it.category_name})
        for name in names:
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.cat_box.addWidget(cb)
            self._category_boxes[name] = cb
        if not names:
            lbl = QLabel("(se rellena tras analizar)")
            lbl.setStyleSheet(f"color: {PENDING_COLOR}; font-size: 8pt;")
            self.cat_box.addWidget(lbl)

    def slim_ratio(self) -> float:
        try:
            r = float((self.slim_ratio_e.text() or "").strip().replace(",", "."))
        except ValueError:
            return 2.0
        return r if r > 1.0 else 2.0

    @staticmethod
    def _int(edit, default=None):
        try:
            return int(edit.text().strip())
        except (ValueError, AttributeError):
            return default

    def apply_filters(self):
        """Mismo criterio que App._apply_cleanup_filters (core/cleanup_candidates)."""
        from core.cleanup_candidates import (CleanupFilters, WATCHED_LOW_PLAYCOUNT, WATCHED_NEVER,
                                             WATCHED_NO_DATA, WATCHED_NOT_REWATCHED, filter_candidates)
        h = self.host
        min_age = self._int(self.age_e) if self.age_cb.isChecked() else None
        choice = self.watched.currentText()
        watched_mode = not_rewatched = max_play = None
        if choice == "Nunca vista":
            watched_mode = WATCHED_NEVER
        elif choice == "Vista, sin repetir en":
            watched_mode, not_rewatched = WATCHED_NOT_REWATCHED, self._int(self.not_rewatched_e, 12)
        elif choice == "Pocas reproducciones":
            watched_mode, max_play = WATCHED_LOW_PLAYCOUNT, self._int(self.max_play_e, 1)
        elif choice == "Sin datos de visionado":
            watched_mode = WATCHED_NO_DATA
        min_size = None
        if self.size_cb.isChecked():
            try:
                min_size = float(self.size_e.text().strip().replace(",", "."))
            except ValueError:
                min_size = None
        types = set()
        if self.type_tv.isChecked():
            types.add("tv")
        if self.type_movie.isChecked():
            types.add("movie")
        if types == {"tv", "movie"}:
            types = None
        cats = {n for n, cb in self._category_boxes.items() if cb.isChecked()}
        if not self._category_boxes or cats == set(self._category_boxes):
            cats = None
        filters = CleanupFilters(min_age_months=min_age, watched_mode=watched_mode,
                                 not_rewatched_months=not_rewatched, max_play_count=max_play,
                                 min_size_gb=min_size, media_types=types, category_names=cats,
                                 name_query=self.search.text().strip(), only_duplicates=self.duplicates.isChecked())
        items = filter_candidates(h._cleanup_raw_items, filters)
        if not self.show_fav.isChecked():
            items = [it for it in items if not h._is_favorite(it.media_type, it.tmdb_id)]
        if not self.show_res.isChecked():
            items = [it for it in items if not h._is_reserved(it.media_type, it.tmdb_id)]
        if self.slim_only.isChecked():
            items = h._filter_slim_only(items)
        h._cleanup_filtered_items = items
        if self.flat.isChecked():
            flat = h._build_cleanup_flat_rows(items)
            if flat is None:
                self.flat.blockSignals(True)
                self.flat.setChecked(False)
                self.flat.blockSignals(False)
                self.ctx.set_status("«Por capítulo» necesita un análisis reciente: pulsa «Analizar servidor».",
                                    WARNING_COLOR)
                h._cleanup_flat_rows = []
            else:
                h._cleanup_flat_rows = flat
        else:
            h._cleanup_flat_rows = []
        h._cleanup_filtered_items.sort(key=h._cleanup_sort_key_fn(self._sort_key), reverse=not self._sort_asc)
        self._update_quota()
        self.render()

    def _flat_mode(self) -> bool:
        return self.flat.isChecked()

    def render(self):
        """(Re)construye el árbol a partir del estado del host, conservando
        qué candidatas/temporadas estaban desplegadas."""
        h = self.host
        roots = []
        if self._flat_mode():
            for i, d in enumerate(h._cleanup_flat_rows or []):
                it = d.get("item")
                if it is None:
                    continue
                icon = "📺" if it.media_type == "tv" else "🎬"
                s, e = d.get("season"), d.get("episode")
                try:
                    head = f"{icon} {it.name} — {int(s)}x{int(e):02d}" if s and e else f"{icon} {it.name}"
                except (TypeError, ValueError):
                    head = f"{icon} {it.name}"
                roots.append(Node("flat", it, None, len(roots), text=f"{head}   ·   {_slim_line(d)}", data=d))
            total = sum(d.get("saving") or 0 for d in h._cleanup_flat_rows or [])
            self.results_lbl.setText(f"{len(roots)} capítulo(s) por encima del objetivo -- {fmt_size(total)} "
                                     "ahorrables re-descargando" if roots else "")
        else:
            for it in h._cleanup_filtered_items:
                n = Node("item", it, None, len(roots))
                if it.ftp_path in h._cleanup_expanded:
                    n.children = self._children_for(n)
                roots.append(n)
            total = sum(it.size_bytes for it in h._cleanup_filtered_items)
            self.results_lbl.setText(f"{len(roots)} candidata(s) -- {fmt_size(total)} liberables" if roots else
                                     ("Sin candidatas con estos filtros." if h._cleanup_raw_items else ""))
        scroll = self.view.verticalScrollBar().value()
        self._syncing = True
        try:
            self.model.set_roots(roots)
            for i, n in enumerate(roots):
                if n.kind == "item" and n.item.ftp_path in h._cleanup_expanded:
                    idx = self.model.index(i, 0)
                    self.view.setExpanded(idx, True)
                    for j, c in enumerate(n.children):
                        if c.kind == "season" and (n.item.ftp_path, c.season) in h._cleanup_expanded_seasons:
                            self.view.setExpanded(self.model.index(j, 0, idx), True)
        finally:
            self._syncing = False
        self.view.verticalScrollBar().setValue(scroll)

    def _children_for(self, parent: Node) -> list:
        """Desglose para adelgazar (ver App._render_cleanup_slim_children)."""
        h = self.host
        item = parent.item
        out = []

        def add(kind, text="", **kw):
            n = Node(kind, item, parent, len(out), text=text, **kw)
            out.append(n)
            return n

        files = h._cleanup_ep_files(item)
        if files is None:
            if item.ftp_path in h._cleanup_slim_loading:
                add("info", "Listando capítulos…")
            else:
                add("info", "No se pudo listar todavía.", data={"retry": True})
            return out
        try:
            cands, target = h._slim_candidates_for_item(item, files)
        except Exception:
            add("info", "No se pudo calcular el objetivo.")
            return out
        try:
            from core.slim_candidates import get_desired_bytes
            want = get_desired_bytes(self.config.get("slim_desired_sizes", {}) or {}, item.name or "")
        except Exception:
            want = None
        des = f"Deseado: {want / 1024 / 1024:g} MB" if want else "Deseado: automático"
        add("desired", f"{des}   ·   Objetivo: {fmt_size(target) if target else '—'}",
            data={"cands": list(cands), "want_mb": f"{want / 1024 / 1024:g}" if want else ""})
        if not cands:
            src = "deseado" if want else "mediana/resolución"
            add("info", f"Nada que adelgazar: ningún capítulo supera {h._cleanup_slim_ratio():g}× su objetivo ({src}).")
            return out
        from core.slim_candidates import group_by_season
        grouped = group_by_season(cands)
        multi = item.media_type == "tv" and len(grouped) > 1
        for season in sorted(grouped):
            sc = grouped[season]
            holder_parent = parent
            if multi:
                label = f"Temporada {season}" if season else "Sin temporada reconocible"
                sn = add("season", f"{label} — {len(sc)} adelgazable(s)", data={"cands": list(sc)}, season=season)
                holder = sn.children
                holder_parent = sn
            else:
                holder = out
            for d in sc[:FILES_PER_SEASON]:
                holder.append(Node("file", item, holder_parent, len(holder), text=_slim_line(d), data=dict(d)))
            if len(sc) > FILES_PER_SEASON:
                holder.append(Node("info", item, holder_parent, len(holder),
                                   text=f"…y {len(sc) - FILES_PER_SEASON} más en esta temporada."))
        try:
            from core.renamer import is_video_file
            n_video = sum(1 for name, size, _f in files if name and size and is_video_file(name))
        except Exception:
            n_video = len(cands)
        if n_video > len(cands):
            add("info", f"… +{n_video - len(cands)} archivo(s) en peso normal (no se muestran).")
        return out

    def _update_quota(self):
        user = self.config.get("app_user_name", "").strip()
        if not user:
            self.quota_lbl.setText("Configura \"Tu nombre\" en Ajustes → Conexión FTP para poder reservar espacio.")
            self.quota_lbl.setStyleSheet(f"color: {PENDING_COLOR}; font-size: 8pt;")
            return
        used_gb, quota_gb, color, aviso = self.host._quota_status(user)
        self.quota_lbl.setText(f"Reservado por {user}: {used_gb:.1f} de {quota_gb:.0f}GB{aviso}")
        self.quota_lbl.setStyleSheet(f"color: {color}; font-size: 8pt;")

    def set_status(self, text, color=PENDING_COLOR):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {color};")

    def on_new_candidates(self):
        self._populate_categories()
        self.apply_filters()
        self._show_scan_age()

    def after_delete(self):
        self.render()
        self._show_scan_age()

    def on_shown(self):
        self.host._cleanup_visible = True
        self.host._sync_cleanup_candidates_from_ftp()
        self.ctx.sync_reservations()
        self.ctx.sync_favorites()

    def on_hidden(self):
        self.host._cleanup_visible = False

    # ── Orden ──

    def _on_header(self, col):
        key = SORT_KEYS.get(col)
        if key is None:
            self._update_sort_indicator()
            return
        if key == self._sort_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key, self._sort_asc = key, True
        all_saved = dict(self.config.get("table_sort", {}) or {})
        all_saved["limpiar"] = {"key": self._sort_key, "asc": self._sort_asc}
        self.config.set("table_sort", all_saved)
        self.config.save()
        self._update_sort_indicator()
        self.apply_filters()

    def _update_sort_indicator(self):
        col = next((c for c, k in SORT_KEYS.items() if k == self._sort_key), C_NAME)
        hdr = self.view.header()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(col, Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder)

    # ── Desplegar (la lógica decide y lista; esto solo lo refleja) ──

    def _on_expanded(self, index):
        if self._syncing:
            return
        n = self.model.node(index)
        h = self.host
        if n.kind == "item" and n.item.ftp_path not in h._cleanup_expanded:
            h._toggle_cleanup_expand(n.item)
        elif n.kind == "season" and (n.item.ftp_path, n.season) not in h._cleanup_expanded_seasons:
            h._cleanup_expanded_seasons.add((n.item.ftp_path, n.season))

    def _on_collapsed(self, index):
        if self._syncing:
            return
        n = self.model.node(index)
        h = self.host
        if n.kind == "item" and n.item.ftp_path in h._cleanup_expanded:
            h._toggle_cleanup_expand(n.item)
        elif n.kind == "season":
            h._cleanup_expanded_seasons.discard((n.item.ftp_path, n.season))

    # ── Escaneo ──

    def start_scan(self):
        h = self.host
        if self._scanning:
            return
        if not self.config.get("ftp_host", ""):
            self.ctx.set_status("Configura el servidor FTP en Ajustes primero", WARNING_COLOR)
            return
        self._scanning = True
        self.scan_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        h._cleanup_trees = {}
        h._cleanup_ep_cache = {}

        def worker():
            try:
                items = h._scan_cleanup_candidates(progress_cb=lambda c, t, n: ui(lambda: self._progress(c, t, n)))
            except Exception:
                from core.applog import get_logger
                get_logger("aIBechos.qt", "app.log").exception("Liberar espacio (Qt): fallo en el análisis")
                self.ctx.set_status("El análisis terminó con un error -- revisa app.log", ERROR_COLOR)
                items = h._cleanup_raw_items
            ui(lambda: self._finish_scan(items))
        run_in_thread(worker)

    def _progress(self, c, t, name):
        if t > 0:
            self.progress.setRange(0, t)
            self.progress.setValue(c)
            self.set_status(f"Analizando ({c}/{t}): {name}")
        else:
            self.set_status(name)

    def _finish_scan(self, items):
        h = self.host
        self._scanning = False
        self.scan_btn.setEnabled(True)
        self.progress.hide()
        h._cleanup_raw_items = items
        h._cleanup_last_scan_ts = time.time()
        h._cleanup_scanned_by = self.config.get("app_user_name", "")
        total = sum(it.size_bytes for it in items)
        ftp_count = getattr(h, "_cleanup_last_ftp_size_count", 0)
        extra = f" ({ftp_count} calculados por FTP, más lentos)" if ftp_count else ""
        self.set_status(f"Análisis completo: {len(items)} elemento(s), {fmt_size(total)} en total{extra}")
        self._populate_categories()
        self.apply_filters()
        from core.cleanup_candidates_cache import save_cache
        try:
            save_cache(items, h._cleanup_last_scan_ts, h._cleanup_scanned_by)
        except Exception:
            pass
        h._push_cleanup_candidates_to_ftp(items, h._cleanup_last_scan_ts)

    # ── Acciones ──

    def actions_for(self, n: Node) -> list:
        h = self.host
        it = n.item
        if n.kind == "item":
            prot = h._is_favorite(it.media_type, it.tmdb_id) or h._is_reserved(it.media_type, it.tmdb_id)
            deleting = it.ftp_path in h._cleanup_deleting_paths
            acts = []
            if not getattr(it, "loose_file_paths", None):
                busy = it.ftp_path.rstrip("/") in h._cleanup_rescanning
                acts.append(Action("rescan", "…" if busy else "↻", theme.ICON_NEUTRAL,
                                   "Volver a listar esta serie en el servidor: recalcula su peso y sus "
                                   "capítulos adelgazables ahora mismo.", enabled=not busy))
            if it.source:
                acts.append(Action("open_server", "J" if it.source == "jellyfin" else "P",
                                   "#4b3f8f" if it.source == "jellyfin" else "#9c7a10",
                                   f"Abrir en {'Jellyfin' if it.source == 'jellyfin' else 'Plex'}"))
            acts.append(Action("delete", "⏳" if deleting else "🗑", theme.ICON_IGNORE,
                               "Borrado en curso." if deleting else
                               ("Protegida (favorita o reservada): no se puede borrar." if prot else
                                "Borrar esto DEL SERVIDOR de forma definitiva. Pide confirmación y queda "
                                "registrado en el historial de borrados."),
                               enabled=not prot and not deleting))
            return acts
        if n.kind in ("file", "flat"):
            return [Action("search", "🔍", theme.ICON_AMULE, "Buscar este capítulo en eMule para elegir la versión a mano."),
                    Action("slim_one", "🪶", theme.ICON_DL_IDLE,
                           "Descarga automática de una versión más ligera (<85% del peso actual). Cuando "
                           "llegue, el gordo se borra solo del servidor.")]
        if n.kind == "season":
            return [Action("slim_many", "🪶", theme.ICON_DL_IDLE,
                           f"Adelgazar esta temporada ({len(n.data['cands'])})")]
        if n.kind == "desired":
            acts = [Action("desired", "✎", theme.ICON_NEUTRAL,
                           "Peso objetivo por capítulo de ESTA serie (MB). Manda sobre mediana y resolución. "
                           "Vacío/0 = automático.")]
            if n.data.get("cands"):
                acts.append(Action("slim_many", "🪶", theme.ICON_DL_IDLE,
                                   f"Adelgazar serie ({len(n.data['cands'])})"))
            return acts
        if n.kind == "info" and (n.data or {}).get("retry"):
            return [Action("retry", "↻", theme.ICON_NEUTRAL, "Reintentar el listado")]
        return []

    def _on_clicked(self, index):
        n = self.model.node(index)
        if n is None or n.kind != "item":
            return
        it = n.item
        h = self.host
        if index.column() == C_FAV:
            h._toggle_cleanup_item_favorite(it)
            self.apply_filters()
        elif index.column() == C_LOCK:
            h._toggle_cleanup_item_reservation(it)

    def _on_current(self, index):
        n = self.model.node(index)
        if n is None:
            return
        it = n.item
        self.host._cleanup_selected_item = it
        mt = "tv" if it.media_type == "tv" else "movie"
        self.ficha.show(mt, it.tmdb_id, it.name)
        self.ficha.set_extra(f"{fmt_size(it.size_bytes)} -- {self.host._cleanup_item_reason_text(it)}\n📁 {it.ftp_path}")

    def _on_action(self, index, action_id):
        n = self.model.node(index)
        if n is None:
            return
        h = self.host
        it = n.item
        if action_id == "rescan":
            h._rescan_cleanup_item(it)
            self.view.viewport().update()
        elif action_id == "open_server":
            self.ctx.open_in_media_server(it.source, it.server_id)
        elif action_id == "delete":
            self._delete(it)
        elif action_id == "search":
            h._slim_search_on_amule(it, n.data.get("name", ""))
        elif action_id == "slim_one":
            d = n.data
            h._slim_download_one(it, d.get("name", ""), d.get("size") or 0, d.get("target") or 0, None,
                                 d.get("folder"))
        elif action_id == "slim_many":
            label = f"'{it.name}' T{n.season}" if n.kind == "season" else f"'{it.name}'"
            h._slim_download_many(it, list(n.data["cands"]), label)
        elif action_id == "desired":
            text, ok = QInputDialog.getText(self, "Peso deseado", f"Peso objetivo por capítulo de «{it.name}» (MB).\n"
                                            "Vacío o 0 = automático:", text=n.data.get("want_mb", ""))
            if ok:
                h._save_cleanup_desired(it.name, _Text(text))
        elif action_id == "retry":
            h._fetch_cleanup_ep_files(it)
            self.render()

    def _delete(self, item):
        h = self.host
        if h._is_favorite(item.media_type, item.tmdb_id) or h._is_reserved(item.media_type, item.tmdb_id):
            QMessageBox.warning(self, "Protegida",
                                f"\"{item.name}\" está marcada como favorita o reservada -- no se puede borrar.")
            return
        reason = h._cleanup_item_reason_text(item)
        if not h._make_confirm_delete_dialog(item.name, item.ftp_path, item.size_bytes, reason).result:
            return
        h._cleanup_deleting_paths.add(item.ftp_path)
        self.view.viewport().update()
        self.set_status(f"Eliminando {item.name}...")
        threading.Thread(target=h._delete_cleanup_item_worker, args=(item, reason), daemon=True).start()
