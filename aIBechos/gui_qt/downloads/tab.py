"""
Pestaña "Descargas" en Qt: búsqueda manual en aMule (resultados en vivo, con
el mejor candidato destacado y explicado) y la cola de descargas activas.

La búsqueda/descarga usa core/amule_search_session.py (una sola conexión EC,
igual que la versión Tk); la elección del mejor candidato es la de
core/download_quality.py; los ayudantes (ETA, estado, alternativa, "ya en
completados") los de core/app_downloads_core.py vía el host.
"""

from __future__ import annotations

import binascii
import re
import time

import requests
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSplitter, QTableView,
                               QVBoxLayout, QWidget)

from core.amule_search_session import FILE_TYPES, NETWORKS, AmuleSearchSession
from core.api_client import TMDB_IMAGE, detect_episode
from core.download_quality import best_result, explain_score
from core.fmt import fmt_size, fmt_transfer, parse_size_human
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from gui_qt import theme
from gui_qt.actions import Action, ActionsDelegate, ActionsRole
from gui_qt.bridge import run_in_thread, ui
from gui_qt.files.model import ProgressDelegate, ProgressRole

_EP_RE = re.compile(r"(?:[Ss]\d{1,2}[Ee]\d{1,3}|\d{1,2}[xX]\d{1,3})")
BEST_ROW_COLOR = "#1e3a2c"
LIVE_COALESCE_MS = 300
ACTIVE_POLL_MS = 3000


def _hash_hex(res) -> str:
    try:
        raw = getattr(res, "_ec_hash", None)
        if raw and len(raw) == 16:
            return binascii.hexlify(raw).decode("ascii").lower()
    except Exception:
        pass
    return ""


def _key(r) -> str:
    from core.amule_download import downloads_key
    return downloads_key(r)


# ── Resultados de búsqueda ──

R_NAME, R_SIZE, R_SOURCES, R_TYPE, R_ACT = range(5)
R_HEADERS = ["Nombre de archivo", "Tamaño", "Fuentes", "Tipo", ""]
R_SORT = {R_NAME: "name", R_SIZE: "size", R_SOURCES: "sources", R_TYPE: "tipo"}


class ResultsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []
        self.best = None
        self.tooltip_fn = None   # callable(res) -> str
        self._tips: dict = {}
        self.busy: dict = {}     # clave -> color del ⬇

    def set_rows(self, rows, best):
        self.beginResetModel()
        self.rows = list(rows)
        self.best = best
        self._tips = {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(R_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return R_HEADERS[section]
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        res = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == R_NAME:
                return res.name
            if col == R_SIZE:
                return res.size_human
            if col == R_SOURCES:
                return str(res.sources)
            if col == R_TYPE:
                return "Completo" if res.complete else "Parcial"
        elif role == Qt.ForegroundRole and col == R_TYPE:
            return QBrush(QColor(SUCCESS_COLOR if res.complete else PENDING_COLOR))
        elif role == Qt.BackgroundRole and res is self.best:
            return QBrush(QColor(BEST_ROW_COLOR))
        elif role == Qt.ToolTipRole and col != R_ACT and self.tooltip_fn is not None:
            k = id(res)
            if k not in self._tips:
                self._tips[k] = self.tooltip_fn(res)
            return self._tips[k]
        elif role == ActionsRole and col == R_ACT:
            color = self.busy.get(_key(res), theme.ICON_DL_IDLE)
            return [Action("download", "⬇", color, "Descargar este resultado en aMule")]
        return None


# ── Descargas activas ──

A_NAME, A_SIZE, A_DONE, A_PCT, A_PROG, A_SPEED, A_SRC, A_ETA, A_STATUS, A_ACT = range(10)
A_HEADERS = ["Archivo", "Tamaño", "Descargado", "%", "Progreso", "Velocidad", "Fuentes", "Restante",
             "Estado", ""]


class ActiveModel(QAbstractTableModel):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.rows: list = []

    def set_rows(self, rows):
        same = [r.get("hash_hex") for r in rows] == [r.get("hash_hex") for r in self.rows]
        if same and rows:
            self.rows = list(rows)
            self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(A_HEADERS) - 1))
            return
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(A_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return A_HEADERS[section]
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        d = self.rows[index.row()]
        col = index.column()
        pct = float(d.get("percent", 0) or 0)
        if role == Qt.DisplayRole:
            if col == A_NAME:
                return d.get("name", "")
            if col == A_SIZE:
                return fmt_size(d.get("size_full", 0) or 0)
            if col == A_DONE:
                return fmt_size(d.get("size_done", 0) or 0)
            if col == A_PCT:
                return f"{int(pct * 10) / 10:.1f}%" if pct < 100 else "100.0%"
            if col == A_SPEED:
                sp = d.get("speed", 0) or 0
                return fmt_transfer(sp) + "/s" if sp else "—"
            if col == A_SRC:
                return str(d.get("sources", 0))
            if col == A_ETA:
                return self.host._eta_for_download(d)
            if col == A_STATUS:
                return self.host._status_label_for_download(d.get("status", 0))
        elif role == ProgressRole and col == A_PROG:
            return max(0.0, min(1.0, pct / 100.0))
        elif role == Qt.ToolTipRole and col == A_NAME:
            return d.get("name", "")
        elif role == ActionsRole and col == A_ACT:
            return [Action("alt_pick", "🔍", theme.ICON_AMULE, "Buscar alternativas para elegir manualmente"),
                    Action("alt_auto", "↻", theme.ICON_DL_IDLE,
                           "Buscar alternativa y descargar automáticamente (mejor candidato)"),
                    Action("cancel", "✕", theme.ICON_IGNORE, "Cancelar descarga")]
        return None


class DownloadsTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self.config = host.config_data
        self.session = AmuleSearchSession(host._amule_ec_lock, host._downloads_open_ec)
        self._results: list = []
        self._selected_key = None
        self._sort_key, self._sort_asc = None, True
        self._expected_year = None
        self._is_movie = False
        self._query_for_year = None
        self._typical = None
        self._typical_for = None
        self._pending = None
        self._visible = False
        self._detail_token = None
        self._restoring_selection = False
        self._build_ui()
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(LIVE_COALESCE_MS)
        self._coalesce.timeout.connect(self._flush_pending)
        self._poll = QTimer(self)
        self._poll.setInterval(ACTIVE_POLL_MS)
        self._poll.timeout.connect(self._poll_active)

    # ── Construcción ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Buscar en aMule… (p.ej. \"Serie 1x05\" o el título de una película)")
        self.query.returnPressed.connect(lambda: self.search(self.query.text()))
        top.addWidget(self.query, 1)
        top.addWidget(QLabel("Red:"))
        self.network = QComboBox()
        self.network.addItems(NETWORKS)
        cur = self.config.get("amule_search_type", "Kad")
        self.network.setCurrentText(cur if cur in NETWORKS else "Kad")
        self.network.currentTextChanged.connect(self._on_network_changed)
        top.addWidget(self.network)
        top.addWidget(QLabel("Tipo:"))
        self.ftype = QComboBox()
        self.ftype.addItems(list(FILE_TYPES))
        top.addWidget(self.ftype)
        self.search_btn = QPushButton("Buscar")
        self.search_btn.setProperty("accent", True)
        self.search_btn.clicked.connect(lambda: self.search(self.query.text()))
        self.stop_btn = QPushButton("Parar")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_search)
        top.addWidget(self.search_btn)
        top.addWidget(self.stop_btn)
        root.addLayout(top)
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(self.status_lbl)

        self.rmodel = ResultsModel(self)
        self.rmodel.tooltip_fn = self._result_tooltip
        self.results_view = self._table(self.rmodel, R_ACT, 44)
        rh = self.results_view.horizontalHeader()
        rh.setSectionResizeMode(R_NAME, QHeaderView.Stretch)
        for c, w in ((R_SIZE, 90), (R_SOURCES, 70), (R_TYPE, 80)):
            rh.resizeSection(c, w)
        rh.setSectionsClickable(True)
        rh.sectionClicked.connect(self._on_results_header)
        self.results_view.selectionModel().currentRowChanged.connect(self._on_result_current)
        self.rdelegate.actionTriggered.connect(self._on_result_action)

        self.detail = self._build_detail()
        upper = QSplitter(Qt.Horizontal)
        upper.addWidget(self.results_view)
        upper.addWidget(self.detail)
        upper.setStretchFactor(0, 1)
        upper.setSizes([1000, 280])

        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 4, 0, 0)
        head = QHBoxLayout()
        t = QLabel("Descargas en aMule")
        t.setStyleSheet("font-weight: bold;")
        head.addWidget(t)
        self.active_lbl = QLabel("")
        head.addWidget(self.active_lbl, 1)
        refresh = QPushButton("↻ Actualizar")
        refresh.clicked.connect(self._poll_active)
        head.addWidget(refresh)
        ll.addLayout(head)
        self.amodel = ActiveModel(self.host, self)
        self.active_view = self._table(self.amodel, A_ACT, 104, attr="adelegate")
        self.active_view.setItemDelegateForColumn(A_PROG, ProgressDelegate(self.active_view))
        ah = self.active_view.horizontalHeader()
        ah.setSectionResizeMode(A_NAME, QHeaderView.Stretch)
        for c, w in ((A_SIZE, 85), (A_DONE, 85), (A_PCT, 60), (A_PROG, 100), (A_SPEED, 85), (A_SRC, 60),
                     (A_ETA, 80), (A_STATUS, 100)):
            ah.resizeSection(c, w)
        self.adelegate.actionTriggered.connect(self._on_active_action)
        ll.addWidget(self.active_view, 1)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(upper)
        vsplit.addWidget(lower)
        vsplit.setSizes([520, 260])
        root.addWidget(vsplit, 1)

    def _table(self, model, act_col, act_w, attr="rdelegate"):
        v = QTableView()
        v.setModel(model)
        v.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.setSelectionMode(QAbstractItemView.SingleSelection)
        v.setAlternatingRowColors(True)
        v.setShowGrid(False)
        v.setWordWrap(False)
        v.verticalHeader().hide()
        v.verticalHeader().setDefaultSectionSize(26)
        v.setMouseTracking(True)
        delegate = ActionsDelegate(v)
        v.setItemDelegateForColumn(act_col, delegate)
        v.horizontalHeader().resizeSection(act_col, act_w)
        setattr(self, attr, delegate)
        return v

    def _build_detail(self):
        frame = QFrame()
        frame.setObjectName("card")
        frame.setMinimumWidth(240)
        lay = QVBoxLayout(frame)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        lay.addWidget(scroll)
        b = QVBoxLayout(body)
        self.poster = QLabel("Pulsa un resultado\npara ver su ficha")
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(170, 245)
        self.poster.setStyleSheet(f"color: {PENDING_COLOR}; background: {theme.BG_ALT}; border-radius: 6px;")
        b.addWidget(self.poster, 0, Qt.AlignHCenter)

        def lbl(bold=False, color=None):
            w = QLabel()
            w.setWordWrap(True)
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            w.setStyleSheet(("font-weight: bold; font-size: 11pt;" if bold else "font-size: 9pt;")
                            + (f" color: {color};" if color else ""))
            b.addWidget(w)
            return w
        self.d_title = lbl(bold=True)
        self.d_meta = lbl()
        self.d_cert = lbl(color=PENDING_COLOR)
        self.d_cast = lbl(color=PENDING_COLOR)
        self.d_local = lbl(color=PENDING_COLOR)
        self.d_overview = lbl()
        b.addStretch(1)
        return frame

    # ── Visibilidad: solo se sondea la cola con la pestaña a la vista ──

    def on_shown(self):
        self._visible = True
        self._poll_active()
        self._poll.start()

    def on_hidden(self):
        self._visible = False
        self._poll.stop()

    # ── Búsqueda ──

    def _on_network_changed(self, value):
        if self.config.get("amule_search_type") != value:
            self.config.set("amule_search_type", value)
            self.config.save()

    def search(self, query: str, expected_year=None, is_movie=None):
        """También lo usan otras pestañas (🔍 de Episodios que faltan)."""
        query = (query or "").strip()
        if self.query.text() != query:
            self.query.setText(query)
        if is_movie is not None:
            self._expected_year, self._is_movie, self._query_for_year = expected_year, bool(is_movie), query
        elif query != self._query_for_year:
            self._expected_year = None
            self._is_movie = not bool(_EP_RE.search(query))
        if not query:
            self._status("Introduce un término de búsqueda", ERROR_COLOR)
            return
        st = self.network.currentText()
        ft = FILE_TYPES.get(self.ftype.currentText(), "")
        self._results = []
        self._selected_key = None
        self._coalesce.stop()
        self._pending = None
        self._render()
        self._status(f'Buscando "{query}" en {st}... (los resultados se actualizan solos, hasta 1 minuto)')
        self.search_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._prepare_typical(query)
        self._token = self.session.start(
            query, st, ft,
            on_results=lambda r: ui(lambda rr=r: self._on_live(rr)),
            on_done=lambda: ui(self._finish_search),
            on_error=lambda m: ui(lambda mm=m: (self._status(f"Error: {mm}", ERROR_COLOR),
                                               self._enable_search())))

    def _prepare_typical(self, query: str):
        """Tamaño típico de la serie en el servidor (para penalizar calidades
        raras) -- red, así que en un hilo; se recalcula el mejor al llegar."""
        self._typical = None
        self._typical_for = query
        if self._is_movie:
            return

        def worker():
            typ = None
            try:
                from core.download_quality import _parse_season_episode, _series_title_before_episode
                sname = _series_title_before_episode(query)
                se = _parse_season_episode(query)
                if sname:
                    typ = self.host._typical_size_for_series(sname, se[0] if se else None)
            except Exception:
                typ = None

            def apply():
                if self._typical_for == query:
                    self._typical = typ
                    self._render()
            ui(apply)
        run_in_thread(worker)

    def stop_search(self):
        self.session.stop()
        self._coalesce.stop()
        self._enable_search()
        self._status("Búsqueda detenida")

    def _enable_search(self):
        self.search_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_live(self, results):
        if not results and self._results:
            return   # sondeo vacío: se conserva lo ya mostrado
        self._pending = results
        self._coalesce.start()

    def _flush_pending(self):
        results, self._pending = self._pending, None
        if results is None:
            return
        old = {_key(r): r for r in self._results}
        merged = []
        for nr in results:
            o = old.get(_key(nr))
            if o is not None:
                for attr in ("sources", "complete", "size_human"):
                    try:
                        setattr(o, attr, getattr(nr, attr, getattr(o, attr)))
                    except Exception:
                        pass
                merged.append(o)
            else:
                merged.append(nr)
        self._results = merged
        self._render()

    def _finish_search(self):
        self._flush_pending()
        self._enable_search()
        n = len(self._results)
        self._status(f"Búsqueda terminada: {n} resultado(s)" if n else "Búsqueda terminada sin resultados.",
                     SUCCESS_COLOR if n else PENDING_COLOR)

    def _best(self):
        if not self._results:
            return None
        try:
            return best_result(self._results, self.query.text(), expected_year=self._expected_year,
                               is_movie=self._is_movie or not _EP_RE.search(self.query.text()),
                               typical_size=self._typical)
        except Exception:
            return None

    def _render(self):
        rows = list(self._results)
        if self._sort_key == "name":
            rows.sort(key=lambda r: r.name.lower(), reverse=not self._sort_asc)
        elif self._sort_key == "size":
            rows.sort(key=lambda r: parse_size_human(r.size_human), reverse=not self._sort_asc)
        elif self._sort_key == "sources":
            rows.sort(key=lambda r: r.sources, reverse=not self._sort_asc)
        elif self._sort_key == "tipo":
            rows.sort(key=lambda r: r.complete, reverse=not self._sort_asc)
        best = self._best()
        if best is not None and best in rows:
            rows.remove(best)
            rows.insert(0, best)   # el recomendado, siempre arriba
        self.rmodel.set_rows(rows, best)
        if self._selected_key is not None:
            for i, r in enumerate(rows):
                if _key(r) == self._selected_key:
                    # Reponer el resaltado tras el reset del modelo SIN
                    # repintar la ficha: si no, cada lote de resultados en
                    # vivo re-dispara _show_result (ficha a "Cargando…",
                    # nueva búsqueda TMDB + descarga de póster que el
                    # siguiente lote invalida) y la ficha no termina de
                    # cargar hasta que acaba la búsqueda.
                    self._restoring_selection = True
                    try:
                        self.results_view.selectRow(i)
                    finally:
                        self._restoring_selection = False
                    break

    def _on_results_header(self, col):
        key = R_SORT.get(col)
        if key is None:
            return
        if key == self._sort_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key, self._sort_asc = key, True
        self.results_view.horizontalHeader().setSortIndicator(
            col, Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder)
        self.results_view.horizontalHeader().setSortIndicatorShown(True)
        self._render()

    def _result_tooltip(self, res) -> str:
        q = self.query.text()
        best = self.rmodel.best
        try:
            exp = explain_score(res, q, expected_year=self._expected_year,
                                is_movie=self._is_movie or not _EP_RE.search(q), typical_size=self._typical)
            head = "✓ Recomendado\n" if res is best else "✗ No recomendado\n"
            if res is not best and best is not None:
                head += f"Gana: {best.name[:50]} ({best.size_human}, {best.sources} fuentes)\n\n"
            return head + exp
        except Exception:
            return res.name

    # ── Ficha del resultado ──

    def _on_result_current(self, cur, _prev):
        if self._restoring_selection:
            return
        self._show_result(cur.row())

    def _show_result(self, row: int):
        if row < 0 or row >= len(self.rmodel.rows):
            return
        res = self.rmodel.rows[row]
        self._selected_key = _key(res)
        token = object()
        self._detail_token = token
        try:
            det = detect_episode(res.name) or {}
        except Exception:
            det = {}
        tipo = {"tv": "📺 Serie", "anime": "📺 Serie", "movie": "🎬 Película",
                "libro": "📚 Libro"}.get(det.get("media_type"), "🎬 Película")
        partes = [tipo] + ([f"T{det['season']}"] if det.get("season") else []) + \
                 ([f"E{det['episode']}"] if det.get("episode") else [])
        self.d_title.setText(det.get("title") or res.name)
        self.d_meta.setText(" · ".join(partes))
        self.d_cert.setText("")
        self.d_cast.setText("")
        self.d_overview.setText("Cargando…")
        self.d_local.setText("\n".join([f"Tamaño: {res.size_human}", f"Fuentes: {res.sources}",
                                        f"Estado: {'Completo' if res.complete else 'Parcial'}",
                                        f"Tipo: {det.get('media_type', 'movie')}"]))
        self.poster.setPixmap(QPixmap())
        self.poster.setText("…")
        tmdb = self.host.tmdb

        def worker():
            title = det.get("title") or res.name
            out = {}
            try:
                results = tmdb.search_multi(title, prefer_type=det.get("media_type") or "movie")
            except Exception:
                results = []
            if results:
                r = results[0]
                mt = r.get("media_type", "movie")
                try:
                    details = tmdb.get_tv_details(r["id"]) if mt == "tv" else tmdb.get_movie_details(r["id"])
                except Exception:
                    details = {}
                out.update(r=r, mt=mt, details=details)
                if mt == "movie":
                    try:
                        out["cert"] = tmdb.get_movie_certification(r["id"]) or ""
                    except Exception:
                        out["cert"] = ""
                try:
                    out["cast"] = tmdb.get_top_cast(mt, r["id"], 6)
                except Exception:
                    out["cast"] = []
                pp = details.get("poster_path") or r.get("poster_path")
                if pp:
                    try:
                        resp = requests.get(f"{TMDB_IMAGE}{pp}", timeout=8)
                        out["poster"] = resp.content if resp.ok else None
                    except Exception:
                        out["poster"] = None
            if not (details.get("overview") or r.get("overview") or "").strip():
                try:
                    from core.ai_synopsis import ai_key_if_enabled, es_overview
                    ai_key = ai_key_if_enabled(self.host.config_data)
                    tid = int(r.get("id", 0) or 0)
                    if ai_key and tid and mt in ("tv", "movie"):
                        out["ai_es"] = es_overview(mt, tid,
                                                   self.host.config_data.get("tmdb_api_key", ""), ai_key)
                except Exception:
                    pass
            ui(lambda: self._apply_detail(token, title, out))
        run_in_thread(worker)

    def _apply_detail(self, token, title, out):
        if token is not self._detail_token:
            return
        if not out:
            self.d_overview.setText("No se encontró en TMDB")
            self.poster.setText("Sin póster")
            return
        r, mt, details = out["r"], out["mt"], out.get("details") or {}
        year = (details.get("first_air_date") or details.get("release_date") or "")[:4]
        vote = details.get("vote_average") or r.get("vote_average") or 0
        genres = [g.get("name") for g in (details.get("genres") or []) if g.get("name")]
        meta = " · ".join(x for x in ["📺 Serie" if mt == "tv" else "🎬 Película", year, f"⭐ {vote:.1f}"] if x)
        if genres:
            meta += " · " + ", ".join(genres)
        self.d_title.setText(details.get("name") or details.get("title") or r.get("name") or r.get("title") or title)
        self.d_meta.setText(meta)
        self.d_cert.setText("Serie" if mt == "tv" else
                            (f"Clasificación: {out.get('cert')}" if out.get("cert") else "Clasificación: sin dato"))
        cast = out.get("cast") or []
        self.d_cast.setText(("Reparto: " + ", ".join(cast)) if cast else "")
        overview = details.get("overview") or r.get("overview") or out.get("ai_es") or ""
        self.d_overview.setText(overview or "Sin sinopsis disponible")
        raw = out.get("poster")
        pm = QPixmap()
        if raw and pm.loadFromData(raw):
            self.poster.setPixmap(pm.scaled(170, 245, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.poster.setText("")
        else:
            self.poster.setText("Sin póster")

    # ── Descargar un resultado ──

    def _on_result_action(self, index: QModelIndex, action_id: str):
        if action_id == "download" and 0 <= index.row() < len(self.rmodel.rows):
            self.download(self.rmodel.rows[index.row()])

    def download(self, res):
        if not getattr(res, "_ec_hash", None):
            self._status(f"Error: resultado #{res.number} sin hash MD4 para descargar", ERROR_COLOR)
            return
        k = _key(res)
        self.rmodel.busy[k] = theme.ICON_DL_BUSY
        self.results_view.viewport().update()
        self._status(f"Enviando descarga #{res.number} a aMule...")

        def worker():
            ok, raw = self.session.download(res)
            h = _hash_hex(res)
            already = bool(ok and h and self.host._download_hash_in_shared(h))

            def apply():
                if already:
                    self.rmodel.busy[k] = theme.ICON_DL_ALREADY
                    self._status(f"Ya está en completados de aMule (nº {res.number}): {res.name[:60]} "
                                 "(no se vuelve a bajar)", WARNING_COLOR)
                elif ok:
                    self.rmodel.busy[k] = theme.ICON_DL_OK
                    self._status(f"Descarga #{res.number} enviada a aMule (revísalo en la cola de aMule)",
                                 SUCCESS_COLOR)
                    self._poll_active()
                else:
                    self.rmodel.busy[k] = theme.ICON_DL_FAIL
                    debug = f" (raw: {str(raw)[:200]})" if raw else ""
                    self._status(f"Error al iniciar descarga #{res.number}.{debug}", ERROR_COLOR)
                self.results_view.viewport().update()
            ui(apply)
        run_in_thread(worker)

    # ── Cola de descargas activas ──

    def _poll_active(self):
        if not self._visible:
            return
        if self.session.active and time.monotonic() - self.session.started_ts < 70:
            return   # aMule solo admite una conexión: no interrumpir la búsqueda
        host = self.host

        def worker():
            try:
                with host._amule_ec_lock:
                    ec = host._downloads_open_ec()
                    if ec is None:
                        raise ConnectionError("aMule no responde")
                    try:
                        queue = ec.get_download_queue()
                    finally:
                        try:
                            ec.close()
                        except Exception:
                            pass
            except Exception as e:
                ui(lambda m=str(e): self._set_active_status(f"— ({m})", ERROR_COLOR))
                return
            ui(lambda q=queue: self._apply_active(q))
        run_in_thread(worker)

    def _apply_active(self, queue):
        for d in queue or []:
            try:
                sf = int(d.get("size_full", 0) or 0)
                sd = int(d.get("size_done", 0) or 0)
                pct = float(d.get("percent", 0) or 0)
                if sf > 0 and pct and sd == 0:
                    d["size_done"] = int(sf * pct / 100.0)
                elif sf > 0 and sd > 0 and not pct:
                    d["percent"] = sd / sf * 100.0
            except Exception:
                pass
        self.amodel.set_rows(queue or [])
        if not queue:
            self._set_active_status("Sin descargas", PENDING_COLOR)
            return
        speed = sum(int(d.get("speed", 0) or 0) for d in queue)
        full = sum(int(d.get("size_full", 0) or 0) for d in queue)
        done = [d for d in queue if float(d.get("percent", 0) or 0) >= 100.0]
        parts = [f"{len(queue) - len(done)} activa(s)"]
        if done:
            parts.append(f"{len(done)} completada(s)")
        if full:
            parts.append(fmt_size(full))
        parts.append(f"{fmt_transfer(speed)}/s")
        self._set_active_status(" · ".join(parts), SUCCESS_COLOR)

    def _set_active_status(self, text, color):
        self.active_lbl.setText(text)
        self.active_lbl.setStyleSheet(f"color: {color};")

    def _on_active_action(self, index: QModelIndex, action_id: str):
        if not (0 <= index.row() < len(self.amodel.rows)):
            return
        d = dict(self.amodel.rows[index.row()])
        if action_id == "cancel":
            self._cancel(d.get("hash_hex", ""))
        elif action_id == "alt_pick":
            q = self.host._alternative_query_for_download(d) or d.get("name", "")
            self.search(q)
            self.ctx.set_status(f"Buscando alternativas para: {q[:60]}")
        elif action_id == "alt_auto":
            self._alternative_auto(d)

    def _cancel(self, hash_hex: str):
        if not hash_hex:
            return
        host = self.host

        def worker():
            try:
                with host._amule_ec_lock:
                    ec = host._downloads_open_ec()
                    if ec is None:
                        raise ConnectionError("aMule no responde")
                    try:
                        ok, msg = ec.cancel_download(hash_hex)
                    finally:
                        ec.close()
            except Exception as e:
                ok, msg = False, str(e)
            ui(lambda: (self._set_active_status("Cancelada" if ok else f"Error: {msg}",
                                                SUCCESS_COLOR if ok else ERROR_COLOR), self._poll_active()))
        run_in_thread(worker)

    def _alternative_auto(self, d: dict):
        """Busca otra versión de una descarga atascada y lanza la mejor
        (mismo criterio best_result que el resto de la app)."""
        q = self.host._alternative_query_for_download(d) or d.get("name", "")
        try:
            det = detect_episode(d.get("name", "")) or {}
            is_movie = det.get("media_type") == "movie"
        except Exception:
            is_movie = False
        self._set_active_status(f"Buscando alternativa: {q[:40]}…", PENDING_COLOR)
        host = self.host

        def worker():
            from core.amule_download import auto_download, typical_size_for_query
            typical = None if is_movie else typical_size_for_query(host.config_data, q)
            ok, why, _h, name = auto_download(host.config_data, host._amule_ec_lock, q,
                                              typical_size=typical, is_movie=is_movie)

            def apply():
                if ok:
                    self._set_active_status(f"Alternativa lanzada: {name[:40]}", SUCCESS_COLOR)
                    self.ctx.set_status(f"Alternativa descargando: {name[:50]}", SUCCESS_COLOR)
                    self._poll_active()
                else:
                    self._set_active_status(f"No se encontró alternativa ({why})", WARNING_COLOR)
            ui(apply)
        run_in_thread(worker)

    # ── Estado ──

    def _status(self, text, color=PENDING_COLOR):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {color};")
