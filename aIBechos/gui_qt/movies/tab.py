"""
Pestaña "Recomendado" en Qt: películas y series de las listas de TMDB que aún
no están en el servidor (o sí, si se apaga el filtro), con su disponibilidad
en plataformas.

Lógica compartida con Tk: escaneo/caché/compartir en core/app_movies_core.py
(vía host), filtros en core/missing_movies.visible_movie_rows. El estado
(_movies_results) vive en el host, porque la lógica de subida también lo toca
(marca "en servidor" lo recién subido).
"""

from __future__ import annotations

import re
import threading
import time

import requests
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
                               QSplitter, QTableView, QVBoxLayout, QWidget)

from core.amule_download import auto_download
from core.applog import get_logger
from core.missing_movies import MOVIE_LIST_LABELS, format_watch_display, genre_options, visible_movie_rows
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from gui_qt import theme
from gui_qt.actions import Action, ActionsDelegate, ActionsRole
from gui_qt.bridge import run_in_thread, ui

_log = get_logger("aIBechos.qt", "app.log")

(C_FAV, C_LOCK, C_TITLE, C_YEAR, C_LIST, C_VOTE, C_WATCH, C_SERVER, C_ACT) = range(9)
HEADERS = ["★", "🔒", "Título", "Año", "Lista", "Nota", "Disponible en", "En servidor", ""]
# Columna -> clave de sort_movie_rows (ver App._movies_sort_key_for_column)
SORT_KEYS = {C_TITLE: "title", C_YEAR: "year", C_LIST: "list", C_VOTE: "vote_average"}
TIPOS = {"Todo": "all", "Películas": "movies", "Series": "series"}
YEARS = ["1", "2", "3", "5", "8", "10", "Todos"]


def _mtype(r) -> str:
    return r.get("media_type", "movie")


class MoviesModel(QAbstractTableModel):
    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.rows: list = []
        self.dl_state: dict = {}

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def refresh_all(self):
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(HEADERS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        r = self.rows[index.row()]
        col = index.column()
        ctx = self.tab.ctx
        mt, tid = _mtype(r), r["tmdb_id"]
        is_tv = mt == "tv"
        if role == Qt.DisplayRole:
            if col == C_FAV:
                return "★" if ctx.is_favorite(mt, tid) else "☆"
            if col == C_LOCK:
                return "🔒" if ctx.is_reserved(mt, tid) else "🔓"
            if col == C_TITLE:
                return f"📺 {r['title']}" if is_tv else f"🎬 {r['title']}"
            if col == C_YEAR:
                return r.get("year", "") or "—"
            if col == C_LIST:
                return MOVIE_LIST_LABELS.get(r.get("list"), r.get("list", ""))
            if col == C_VOTE:
                return f"{(r.get('vote_average') or 0):.1f}"
            if col == C_WATCH:
                return "—" if is_tv else format_watch_display(r)
            if col == C_SERVER:
                return "✓ En servidor" if r.get("in_server") else "No"
        elif role == Qt.ForegroundRole:
            if col == C_FAV:
                return QBrush(QColor(theme.ACCENT if ctx.is_favorite(mt, tid) else PENDING_COLOR))
            if col == C_LOCK:
                return QBrush(QColor(theme.ACCENT if ctx.is_reserved(mt, tid) else PENDING_COLOR))
            if col == C_WATCH and not is_tv:
                return QBrush(QColor(ERROR_COLOR if format_watch_display(r) == "Solo en cines" else SUCCESS_COLOR))
            if col == C_SERVER:
                return QBrush(QColor(SUCCESS_COLOR if r.get("in_server") else PENDING_COLOR))
            if col in (C_YEAR, C_LIST, C_VOTE):
                return QBrush(QColor(PENDING_COLOR))
        elif role == Qt.TextAlignmentRole and col in (C_FAV, C_LOCK, C_VOTE):
            return int(Qt.AlignCenter)
        elif role == Qt.ToolTipRole:
            if col == C_FAV:
                return ctx.favorite_tooltip(mt, tid)
            if col == C_LOCK:
                return ctx.reservation_tooltip(mt, tid)
            if col == C_TITLE:
                return r.get("overview") or r["title"]
        elif role == ActionsRole and col == C_ACT:
            return self.tab.actions_for(r)
        return None


class MoviesTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self.config = host.config_data
        self._scanning = False
        self._cancel_event = None
        self._poster_token = None
        saved = (self.config.get("table_sort", {}) or {}).get("peliculas")
        if isinstance(saved, dict) and "key" in saved:
            self._sort_key, self._sort_asc = saved.get("key"), bool(saved.get("asc", True))
        else:
            self._sort_key, self._sort_asc = "title", True
        self._build_ui()
        host.movies_view = self
        if not host._movies_results:
            host._movies_results = host._rows_from_movies_cache()
        self.refresh_genres()
        self.render()
        self.update_status()
        self.ctx.favorites_changed.connect(self.model.refresh_all)
        self.ctx.reservations_changed.connect(self.model.refresh_all)

    # ── Construcción ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 Recomendar")
        self.scan_btn.setProperty("accent", True)
        self.scan_btn.setToolTip("Pedir a TMDB sus listas (tendencias, populares, estrenos...) y cruzarlas "
                                 "con lo que ya hay en Jellyfin/Plex")
        self.scan_btn.clicked.connect(lambda: self.start_scan(False))
        self.full_btn = QPushButton("Reescaneo completo")
        self.full_btn.clicked.connect(lambda: self.start_scan(True))
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setProperty("danger", True)
        self.cancel_btn.clicked.connect(lambda: self._cancel_event and self._cancel_event.set())
        self.cancel_btn.hide()
        for b in (self.scan_btn, self.full_btn, self.cancel_btn):
            head.addWidget(b)
        head.addStretch(1)
        t = QLabel("Recomendado")
        t.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(t)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por nombre...")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.render)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        head.addWidget(self.search)
        root.addLayout(head)

        filters = QHBoxLayout()
        filters.addStretch(1)
        filters.addWidget(QLabel("Género:"))
        self.genre = QComboBox()
        self.genre.setMinimumWidth(130)
        self.genre.currentTextChanged.connect(self._on_genre)
        filters.addWidget(self.genre)
        filters.addWidget(QLabel("Años:"))
        self.years = QComboBox()
        self.years.setEditable(True)
        self.years.addItems(YEARS)
        saved_year = self.config.get("missing_movies_year_filter", "1")
        self.years.setCurrentText(saved_year if (saved_year == "Todos" or re.match(r"^\d+\s*años?$", saved_year)
                                                 or saved_year.isdigit()) else "1")
        self.years.currentTextChanged.connect(self._on_years)
        filters.addWidget(self.years)
        self.tipo = QComboBox()
        self.tipo.addItems(list(TIPOS))
        inv = {v: k for k, v in TIPOS.items()}
        self.tipo.setCurrentText(inv.get(self.config.get("missing_movies_type_filter", "all"), "Todo"))
        self.tipo.currentTextChanged.connect(self._on_tipo)
        filters.addWidget(self.tipo)
        self.switches = {}
        for key, text in (("missing_movies_hide_in_server", "Ocultar ya en el servidor"),
                          ("missing_movies_watch_only", "Disponibles en plataformas"),
                          ("missing_movies_hide_asian", "Ocultar asiáticas")):
            cb = QCheckBox(text)
            cb.setChecked(bool(self.config.get(key, True)))
            cb.toggled.connect(lambda c, k=key: self._on_switch(k, c))
            self.switches[key] = cb
            filters.addWidget(cb)
        root.addLayout(filters)

        self.status_lbl = QLabel("Sin comprobar todavía")
        self.status_lbl.setStyleSheet(f"color: {PENDING_COLOR};")
        root.addWidget(self.status_lbl)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        self.progress.hide()
        root.addWidget(self.progress)

        self.model = MoviesModel(self, self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(26)
        self.view.setMouseTracking(True)
        self.delegate = ActionsDelegate(self.view)
        self.delegate.actionTriggered.connect(self._on_action)
        self.view.setItemDelegateForColumn(C_ACT, self.delegate)
        hdr = self.view.horizontalHeader()
        hdr.setSectionResizeMode(C_TITLE, QHeaderView.Stretch)
        for c, w in ((C_FAV, 28), (C_LOCK, 28), (C_YEAR, 60), (C_LIST, 120), (C_VOTE, 55), (C_WATCH, 160),
                     (C_SERVER, 100), (C_ACT, 172)):
            hdr.resizeSection(c, w)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header)
        self._update_sort_indicator()
        self.view.clicked.connect(self._on_clicked)
        self.view.selectionModel().currentRowChanged.connect(lambda cur, _p: self._show_detail(cur.row()))

        self.detail = self._build_detail()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.view)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 270])
        root.addWidget(split, 1)

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
        self.poster = QLabel("Pulsa una obra\npara ver su ficha")
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
        self.d_overview = lbl()
        b.addStretch(1)
        return frame

    # ── Ganchos (los llama la lógica vía host) ──

    def render(self, reset_page: bool = True):
        rows = visible_movie_rows(
            self.host._movies_results, tipo=TIPOS.get(self.tipo.currentText(), "all"),
            year_sel=self.years.currentText(), genre_sel=self.genre.currentText(),
            text=self.search.text(),
            hide_in_server=self.switches["missing_movies_hide_in_server"].isChecked(),
            watch_only=self.switches["missing_movies_watch_only"].isChecked(),
            hide_asian=self.switches["missing_movies_hide_asian"].isChecked(),
            sort_key=self._sort_key, sort_asc=self._sort_asc)
        sel = self.host._movies_selected_tmdb_id
        self.model.set_rows(rows)
        if sel:
            from core.missing_movies_cache import cache_key
            for i, r in enumerate(rows):
                if cache_key(_mtype(r), r["tmdb_id"]) == sel:
                    self.view.selectRow(i)
                    break

    def update_status(self):
        from core.missing_movies_cache import load_cache
        last_ts = (load_cache().get("_meta") or {}).get("last_scan_ts")
        when = ""
        if last_ts:
            mins = int((time.time() - last_ts) / 60)
            when = "hace un momento" if mins < 1 else f"hace {mins} min" if mins < 60 else f"hace {mins // 60} h"
        results = self.host._movies_results
        if not results:
            text = "Sin comprobar todavía" if not when else f"Sin recomendaciones aún -- último escaneo {when}"
        else:
            n_movies = sum(1 for r in results if _mtype(r) != "tv")
            n_series = len(results) - n_movies
            parts = []
            if n_movies:
                parts.append(f"{n_movies} película{'s' if n_movies != 1 else ''}")
            if n_series:
                parts.append(f"{n_series} serie{'s' if n_series != 1 else ''}")
            text = " · ".join(parts) + " recomendada(s)" + (f" -- último escaneo {when}" if when else "")
        self.status_lbl.setText(text)

    def refresh_genres(self):
        options = genre_options(self.host._movies_results)
        saved = self.config.get("missing_movies_genre_filter", "") or "Todos"
        current = saved if saved in options else "Todos"
        self.genre.blockSignals(True)
        self.genre.clear()
        self.genre.addItems(options)
        self.genre.setCurrentText(current)
        self.genre.blockSignals(False)
        if current != saved and saved != "Todos":
            self.config.set("missing_movies_genre_filter", current)
            self.config.save()

    def on_shown(self):
        self.host._movies_visible = True
        self.host._sync_missing_movies_from_ftp()

    def on_hidden(self):
        self.host._movies_visible = False

    # ── Filtros y orden ──

    def _save(self, key, value):
        self.config.set(key, value)
        self.config.save()

    def _on_switch(self, key, checked):
        self._save(key, checked)
        self.render()

    def _on_tipo(self, text):
        self._save("missing_movies_type_filter", TIPOS.get(text, "all"))
        self.render()

    def _on_years(self, text):
        self._save("missing_movies_year_filter", text)
        self.render()

    def _on_genre(self, text):
        if not text:
            return
        self._save("missing_movies_genre_filter", text)
        self.render()

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
        all_saved["peliculas"] = {"key": self._sort_key, "asc": self._sort_asc}
        self._save("table_sort", all_saved)
        self._update_sort_indicator()
        self.render()

    def _update_sort_indicator(self):
        col = next((c for c, k in SORT_KEYS.items() if k == self._sort_key), C_TITLE)
        hdr = self.view.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(col, Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder)

    # ── Escaneo ──

    def start_scan(self, force_full: bool):
        host = self.host
        if not self.config.get("jellyfin_enabled") and not self.config.get("plex_enabled"):
            self.ctx.set_status("Activa Plex o Jellyfin en Ajustes para recomendar películas", WARNING_COLOR)
            return
        if self._scanning:
            return
        if host._upload_running:
            self.ctx.set_status("Espera a que termine la subida en curso antes de recomendar películas",
                                WARNING_COLOR)
            return
        self._scanning = True
        self._cancel_event = threading.Event()
        cancel = self._cancel_event
        self._set_scanning(True)
        if force_full:
            host._movies_results = []
            self.refresh_genres()
            self.render()

        def worker():
            try:
                results = host._scan_missing_movies(
                    progress_cb=lambda c, t, n: ui(lambda: self._progress(c, t, n)),
                    cancel_event=cancel, force_full=force_full)
            except Exception:
                _log.exception("Recomendador (Qt): fallo inesperado durante el escaneo")
                self.ctx.set_status("El escaneo terminó con un error -- revisa app.log", ERROR_COLOR)
                results = host._movies_results
            ui(lambda: self._finish_scan(results))
        run_in_thread(worker)

    def _progress(self, c, t, n):
        if t:
            self.progress.setRange(0, t)
            self.progress.setValue(c)
        self.status_lbl.setText(f"Comprobando ({c}/{t}): {n}")

    def _set_scanning(self, on: bool):
        self.scan_btn.setEnabled(not on)
        self.full_btn.setEnabled(not on)
        self.cancel_btn.setVisible(on)
        self.progress.setVisible(on)
        if on:
            self.progress.setRange(0, 0)

    def _finish_scan(self, results):
        self._scanning = False
        self.host._movies_results = results
        self.refresh_genres()
        self._set_scanning(False)
        self.update_status()
        self.render()
        self.host._push_missing_movies_to_ftp()

    # ── Acciones de fila ──

    def actions_for(self, r) -> list:
        is_tv = _mtype(r) == "tv"
        in_server = bool(r.get("in_server"))
        acts = []
        if is_tv:
            on = self.host._is_missing_ep_auto_enabled(r["tmdb_id"])
            acts.append(Action("auto", "⚡", theme.ACCENT if on else theme.ICON_NEUTRAL,
                               self.host._auto_btn_tooltip(r["tmdb_id"])))
        else:
            acts.append(Action("noop", "", theme.BG, "", enabled=False))
        acts += [
            Action("search", "🔍", theme.ICON_AMULE, "Buscar en aMule (abre la pestaña Descargas)"),
            Action("copy", "📋", theme.ICON_COPY, "Copiar nombre de la obra al portapapeles"),
            Action("download", "⬇", self.model.dl_state.get((_mtype(r), r["tmdb_id"]), theme.ICON_DL_IDLE),
                   ("Ya está en el servidor" if in_server else
                    ("Descargar el PILOTO (1x01) de esta serie en aMule (en segundo plano). Para completar "
                     "la serie entera usa ⚡." if is_tv else
                     "Buscar en aMule y descargar el mejor candidato para esta película (en segundo plano).")),
                   enabled=not in_server),
            Action("dismiss", "🚫", theme.ICON_IGNORE, "Quitar recomendación"),
        ]
        return acts

    def _row(self, index: QModelIndex):
        return self.model.rows[index.row()] if 0 <= index.row() < len(self.model.rows) else None

    def _on_clicked(self, index: QModelIndex):
        r = self._row(index)
        if r is None:
            return
        mt, tid = _mtype(r), r["tmdb_id"]
        if index.column() == C_FAV:
            self.ctx.toggle_favorite(mt, tid, r["title"])
        elif index.column() == C_LOCK:
            size = self.ctx.best_known_size_bytes(mt, tid, 0)
            self.ctx.toggle_reservation(self.window(), mt, tid, r["title"], size)

    def _on_action(self, index: QModelIndex, action_id: str):
        r = self._row(index)
        if r is None:
            return
        try:
            year = int(r.get("year") or 0) or None
        except (TypeError, ValueError):
            year = None
        is_tv = _mtype(r) == "tv"
        if action_id == "auto":
            self.host._toggle_missing_ep_auto_complete(r["tmdb_id"])
            self.model.refresh_all()
        elif action_id == "search":
            win = self.host.window
            if win is not None:
                # Solo el título: aMule rechaza consultas con paréntesis; el
                # año va aparte para exigirlo al elegir candidato.
                win.amule_search(r["title"], expected_year=year, is_movie=not is_tv)
        elif action_id == "copy":
            QGuiApplication.clipboard().setText(f"{r['title']} ({r['year']})")
            self.ctx.set_status(f"Copiado: {r['title']} ({r['year']})", SUCCESS_COLOR)
        elif action_id == "download":
            self._download(r, year, is_tv)
        elif action_id == "dismiss":
            self.host._dismiss_missing_movie(r)

    def _download(self, r, year, is_tv):
        key = (_mtype(r), r["tmdb_id"])
        if self.model.dl_state.get(key) == theme.ICON_DL_BUSY:
            return
        self.model.dl_state[key] = theme.ICON_DL_BUSY
        self.view.viewport().update()
        query = f"{r['title']} 1x01" if is_tv else r["title"]
        host = self.host

        def worker():
            typical = None
            if is_tv:
                from core.amule_download import typical_size_for_query
                typical = typical_size_for_query(host.config_data, query)
            ok, why, _h, name = auto_download(host.config_data, host._amule_ec_lock, query,
                                              typical_size=typical, is_movie=not is_tv,
                                              expected_year=None if is_tv else year)

            def apply():
                if ok:
                    self.model.dl_state[key] = theme.ICON_DL_OK
                    self.ctx.set_status(f"Descarga lanzada: {name[:60]}", SUCCESS_COLOR)
                elif why.startswith("ya en completados"):
                    self.model.dl_state[key] = theme.ICON_DL_ALREADY
                    self.ctx.set_status(f"Ya en completados de aMule (no se vuelve a bajar): {name[:60]}",
                                        WARNING_COLOR)
                else:
                    self.model.dl_state[key] = theme.ICON_DL_FAIL
                    self.ctx.set_status(f"{query}: {why}", WARNING_COLOR)
                self.view.viewport().update()
            ui(apply)
        run_in_thread(worker)

    # ── Ficha ──

    def _show_detail(self, row: int):
        if not (0 <= row < len(self.model.rows)):
            return
        r = self.model.rows[row]
        from core.missing_movies_cache import cache_key
        self.host._movies_selected_tmdb_id = cache_key(_mtype(r), r["tmdb_id"])
        is_tv = _mtype(r) == "tv"
        self.d_title.setText(r.get("title", ""))
        meta = " · ".join(x for x in ["📺 Serie" if is_tv else "🎬 Película", r.get("year") or "",
                                      MOVIE_LIST_LABELS.get(r.get("list"), r.get("list", "")),
                                      f"⭐ {(r.get('vote_average') or 0):.1f}"] if x)
        genres = [g for g in (r.get("genres") or []) if g]
        if genres:
            meta += " · " + ", ".join(genres)
        self.d_meta.setText(meta)
        self.d_cert.setText("Serie" if is_tv else (r.get("certification") or "Clasificación: …"))
        self.d_cast.setText("Reparto: …")
        self.d_overview.setText(r.get("overview") or "Sin sinopsis disponible")
        self.poster.setPixmap(QPixmap())
        self.poster.setText("…")
        token = object()
        self._poster_token = token
        tmdb = self.host.tmdb

        def worker():
            out = {}
            if r.get("poster_url"):
                try:
                    resp = requests.get(r["poster_url"], timeout=8)
                    out["poster"] = resp.content if resp.ok else None
                except Exception:
                    out["poster"] = None
            try:
                out["cast"] = tmdb.get_top_cast(_mtype(r), r["tmdb_id"], 6)
            except Exception:
                out["cast"] = []
            if not is_tv and not r.get("certification"):
                try:
                    out["cert"] = tmdb.get_movie_certification(r["tmdb_id"]) or ""
                except Exception:
                    out["cert"] = ""
            ui(lambda: self._apply_detail(token, out))
        run_in_thread(worker)

    def _apply_detail(self, token, out):
        if token is not self._poster_token:
            return
        raw = out.get("poster")
        pm = QPixmap()
        if raw and pm.loadFromData(raw):
            self.poster.setPixmap(pm.scaled(170, 245, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.poster.setText("")
        else:
            self.poster.setText("Sin póster")
        cast = out.get("cast") or []
        self.d_cast.setText(("Reparto: " + ", ".join(cast)) if cast else "Reparto: sin datos")
        if "cert" in out:
            self.d_cert.setText(f"Clasificación: {out['cert']}" if out["cert"] else "Clasificación: sin dato")
