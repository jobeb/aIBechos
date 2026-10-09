"""Pestaña "Recomendado" en Qt: filas horizontales con cards estilo web
(tendencia, Top 10, plataformas, géneros... -- las mismas categorías que
solicitudes-web/app.js, ver core/recommended_rows.py), cada card con todos
los botones actuales (⬇⚡🔍📋🚫★🔒) y ficha lateral al pulsarla.

Las filas se cargan de TMDB en vivo y perezosamente (al hacerse visibles,
como el IntersectionObserver de la web); el cruce con el servidor
(Jellyfin/Plex) y la caché compartida siguen en core/app_movies_core.py, y
los botones 🔍/Reescaneo conservan su función (refrescar ese cruce).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date

import requests
from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QProgressBar, QPushButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from core.applog import get_logger
from core.recommended_rows import (apply_filters, build_params, fetch_row, filter_available,
                                   rows_for)
from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui
from gui_qt.movies.cards import CARD_H, CARD_W, CardDelegate, CardRole

_log = get_logger("aIBechos.qt", "app.log")

TIPOS = {"Todo": "all", "Películas": "movies", "Series": "series"}
YEARS = ["1", "2", "3", "5", "8", "10", "Todos"]
SKELETON_CARDS = 7
SEE_ALL_MAX_PAGES = 25


def _mtype(r) -> str:
    return r.get("media_type", "movie")


def _key(item) -> tuple:
    return (item.get("media_type", "movie"), item.get("tmdb_id", 0))


class RowModel(QAbstractListModel):
    """Ítems de una fila (dicts de card o None = esqueleto)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list = []

    def set_skeleton(self, n: int = SKELETON_CARDS):
        self.beginResetModel()
        self.items = [None] * n
        self.endResetModel()

    def set_items(self, items: list):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def refresh_key(self, key: tuple):
        for i, it in enumerate(self.items):
            if it is not None and _key(it) == key:
                self.dataChanged.emit(self.index(i), self.index(i))
                return

    def refresh_all(self):
        if self.items:
            self.dataChanged.emit(self.index(0), self.index(len(self.items) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        if role == CardRole:
            return self.items[index.row()]
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
        self._sections: list = []
        self._build_token = 0
        self._dl_state: dict = {}
        self._owned: set = set()
        self._genre_names: dict = {"movie": {}, "tv": {}}   # por tipo: nombre -> ids
        self._shown_key = None
        self._server_ids_ready = False
        self.delegate = CardDelegate(self)
        self.delegate.get_state = self._card_state
        self.delegate.cardAction.connect(self._on_card_action)
        self.delegate.cardSelected.connect(self._on_card_selected)
        self._build_ui()
        host.movies_view = self
        if not host._movies_results:
            host._movies_results = host._rows_from_movies_cache()
        self._snapshot_owned()
        self.refresh_genres()
        self.render()
        self.update_status()
        self.ctx.favorites_changed.connect(self._refresh_states)
        self.ctx.reservations_changed.connect(self._refresh_states)

    # ── Construcción ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 Recomendar")
        self.scan_btn.setProperty("accent", True)
        self.scan_btn.setToolTip("Cruzar las listas de TMDB con lo que ya hay en Jellyfin/Plex "
                                 "(marca 'En el servidor' sin esperar a reindexar)")
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
        self._search_timer.timeout.connect(self._apply_text)
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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.rows_lay = QVBoxLayout(inner)
        self.rows_lay.setContentsMargins(2, 2, 2, 2)
        self.rows_lay.setSpacing(10)
        self.rows_lay.addStretch(1)
        self.scroll.setWidget(inner)
        self.empty_lbl = QLabel("Nada que mostrar con los filtros actuales.", inner)
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 11pt;")
        self.empty_lbl.hide()
        self.rows_lay.insertWidget(0, self.empty_lbl)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(200)
        self._scroll_timer.timeout.connect(self._load_visible_rows)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._scroll_timer.start())

        self.detail = self._build_detail()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.scroll)
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

        def lbl(bold=False, color=None):
            w = QLabel()
            w.setWordWrap(True)
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            w.setStyleSheet(("font-weight: bold; font-size: 11pt;" if bold else "font-size: 9pt;")
                            + (f" color: {color};" if color else ""))
            b.addWidget(w)
            return w
        self.poster = QLabel("Pulsa una obra\npara ver su ficha")
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(170, 245)
        self.poster.setStyleSheet(f"color: {PENDING_COLOR}; background: {theme.BG_ALT}; border-radius: 6px;")
        b.addWidget(self.poster, 0, Qt.AlignHCenter)
        self.d_title = lbl(bold=True)
        self.d_meta = lbl()
        self.d_cert = lbl(color=PENDING_COLOR)
        self.d_cast = lbl(color=PENDING_COLOR)
        self.d_overview = lbl()
        b.addStretch(1)
        return frame

    # ── Ganchos (los llama la lógica vía host) ──

    def render(self):
        """Reconstruye las secciones según filtros (esqueletos) y carga las
        visibles. Conserva la posición de scroll entre reconstrucciones."""
        self._build_token += 1
        pos = self.scroll.verticalScrollBar().value()
        for sec in self._sections:
            sec["box"].deleteLater()
        self._sections = []
        self._snapshot_owned()
        kinds = self._visible_kinds()
        for kind in kinds:
            for rowdef in rows_for(kind):
                if rowdef.get("hide_if_unavail") and self._watch_only():
                    continue   # Próximamente nunca está en plataformas
                self._add_section(kind, rowdef)
        self.empty_lbl.hide()
        self._update_empty()
        self.scroll.verticalScrollBar().setValue(pos)
        QTimer.singleShot(50, self._load_visible_rows)

    def update_status(self):
        from core.missing_movies_cache import load_cache
        last_ts = (load_cache().get("_meta") or {}).get("last_scan_ts")
        when = ""
        if last_ts:
            mins = int((time.time() - last_ts) / 60)
            when = "hace un momento" if mins < 1 else f"hace {mins} min" if mins < 60 else f"hace {mins // 60} h"
        n_cards = sum(len([i for i in sec["model"].items if i]) for sec in self._sections)
        n_visible = sum(1 for sec in self._sections if not sec["box"].isHidden())
        n_total = len(self._sections)
        if n_cards:
            text = f"{n_cards} recomendada(s) en {n_visible} de {n_total} filas"
        elif n_visible < n_total:
            text = f"Cargando filas… ({n_total - n_visible} ocultas por los filtros)"
        else:
            text = "Cargando filas…"
        if when:
            text += f" -- cruce con el servidor {when}"
        self.status_lbl.setText(text)

    def refresh_genres(self):
        """Opciones del combo de género desde las listas de TMDB (una llamada
        por tipo, cacheada en el cliente), no del último escaneo."""
        def worker():
            names = {"Todos"}
            mapping = {"movie": {}, "tv": {}}
            try:
                for kind in ("movie", "tv"):
                    for g in self.host.tmdb.get_genres(kind) or []:
                        if g.get("name"):
                            names.add(g["name"])
                            mapping[kind].setdefault(g["name"], set()).add(g["id"])
            except Exception:
                pass
            ui(lambda: self._apply_genres(sorted(names), mapping))
        run_in_thread(worker)

    def _apply_genres(self, names, mapping):
        self._genre_names = mapping
        saved = self.config.get("missing_movies_genre_filter", "") or "Todos"
        current = saved if saved in names else "Todos"
        self.genre.blockSignals(True)
        self.genre.clear()
        self.genre.addItems(names)
        self.genre.setCurrentText(current)
        self.genre.blockSignals(False)

    def on_shown(self):
        self.host._movies_visible = True
        self.host._sync_missing_movies_from_ftp()
        if not self._server_ids_ready:
            self._refresh_server_ids()
        else:
            QTimer.singleShot(100, self._load_visible_rows)

    def on_hidden(self):
        self.host._movies_visible = False

    # ── Filtros ──

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

    def _visible_kinds(self):
        tipo = TIPOS.get(self.tipo.currentText(), "all")
        if tipo == "movies":
            return ["movie"]
        if tipo == "series":
            return ["tv"]
        return ["movie", "tv"]

    def _watch_only(self):
        return self.switches["missing_movies_watch_only"].isChecked()

    def _year_min(self):
        text = self.years.currentText().strip()
        if text == "Todos":
            return None
        m = re.match(r"^(\d+)", text)
        if not m:
            return None
        return date.today().year - int(m.group(1))

    def _genre_ids(self, kind: str) -> set:
        name = self.genre.currentText()
        if not name or name == "Todos":
            return set()
        return set(self._genre_names.get(kind, {}).get(name, set()))

    def _static_filters(self, kind: str) -> dict:
        return {"owned_ids": set(self._owned),
                "hide_owned": self.switches["missing_movies_hide_in_server"].isChecked(),
                "hide_asian": self.switches["missing_movies_hide_asian"].isChecked(),
                "genre_ids": self._genre_ids(kind), "year_min": self._year_min()}

    # ── Secciones y carga perezosa ──

    def _add_section(self, kind, rowdef):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QHBoxLayout()
        title = QLabel(rowdef["title"])
        title.setStyleSheet("font-size: 12pt; font-weight: bold;")
        head.addWidget(title)
        status = QLabel("Cargando…")
        status.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
        head.addWidget(status)
        head.addStretch(1)
        prev = QPushButton("‹")
        prev.setFixedWidth(34)
        prev.setToolTip("Anteriores")
        nxt = QPushButton("›")
        nxt.setFixedWidth(34)
        nxt.setToolTip("Siguientes")
        all_btn = QPushButton("Ver todo ›")
        all_btn.setToolTip(f"Toda la lista '{rowdef['title']}'")
        head.addWidget(prev)
        head.addWidget(nxt)
        head.addWidget(all_btn)
        lay.addLayout(head)
        model = RowModel(box)
        model.set_skeleton()
        view = QListView()
        view.setModel(model)
        view.setItemDelegate(self.delegate)
        self.delegate.attach(view)
        view.setViewMode(QListView.IconMode)
        view.setFlow(QListView.LeftToRight)
        view.setWrapping(False)
        view.setResizeMode(QListView.Adjust)
        view.setSpacing(8)
        view.setUniformItemSizes(True)
        view.setGridSize(QSize(CARD_W + 10, CARD_H + 10))
        view.setSelectionMode(QAbstractItemView.SingleSelection)
        view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        try:
            from PySide6.QtWidgets import QStyle as _QS
            sb_h = view.style().pixelMetric(_QS.PM_ScrollBarExtent, None, view) or 16
        except Exception:
            sb_h = 16
        view.setFixedHeight(CARD_H + 10 + sb_h + 2)
        prev.clicked.connect(lambda: view.horizontalScrollBar().setValue(
            view.horizontalScrollBar().value() - int(view.viewport().width() * 0.9)))
        nxt.clicked.connect(lambda: view.horizontalScrollBar().setValue(
            view.horizontalScrollBar().value() + int(view.viewport().width() * 0.9)))
        lay.addWidget(view)
        self.rows_lay.insertWidget(len(self._sections) + 1, box)
        sec = {"kind": kind, "rowdef": rowdef, "box": box, "view": view, "model": model,
               "status_lbl": status, "state": "idle", "base": [], "token": self._build_token}
        all_btn.clicked.connect(lambda: self._open_see_all(sec))
        self._sections.append(sec)

    def _update_empty(self):
        # isHidden (flag explícito), no isVisible: con la pestaña oculta
        # isVisible es falso aunque haya filas, y no debe salir el aviso.
        any_shown = any(not sec["box"].isHidden() for sec in self._sections)
        self.empty_lbl.setVisible(bool(self._sections) and not any_shown)

    def _load_visible_rows(self):
        if not self._sections:
            return
        try:
            origin = self.scroll.viewport().mapTo(self.scroll.widget(), QPoint(0, 0))
            visible = QRect(origin, self.scroll.viewport().size())
        except Exception:
            visible = None
        for sec in self._sections:
            if sec["state"] != "idle" or sec["token"] != self._build_token:
                continue
            if visible is not None and not visible.intersects(sec["box"].geometry()):
                continue
            self._load_section(sec)

    def _load_section(self, sec):
        sec["state"] = "loading"
        token, kind, rowdef = self._build_token, sec["kind"], sec["rowdef"]
        filters = self._static_filters(kind)
        watch_only = self._watch_only()
        client = self.host.tmdb
        # Género/años van en la consulta (build_params) ADEMÁS del
        # prefilter: en /discover TMDB filtra en servidor y la fila se
        # llena; en el resto solo filtra el cliente.
        query = build_params(rowdef, kind, filters["genre_ids"], filters["year_min"])
        prefilter = lambda batch: apply_filters(batch, **filters)

        def worker():
            try:
                items = fetch_row(client, kind, rowdef, prefilter=prefilter, params=query)
            except Exception as e:
                ui(lambda m=str(e): self._section_error(sec, token, m))
                return
            if watch_only and not rowdef.get("no_avail"):
                items = filter_available(client, kind, items)
            ui(lambda: self._section_ready(sec, token, items))
        run_in_thread(worker)

    def _section_error(self, sec, token, msg):
        if token != self._build_token or sec["token"] != token:
            return
        sec["state"] = "error"
        sec["status_lbl"].setText(f"No se pudo cargar ({msg[:80]}). Pulsa 'Ver todo' para reintentar.")
        sec["model"].set_items([])
        self.update_status()

    def _section_ready(self, sec, token, items):
        if token != self._build_token or sec["token"] != token:
            return   # filtros cambiados mientras cargaba: respuesta obsoleta
        sec["state"] = "ready"
        sec["base"] = items
        shown = self._with_text(items)
        sec["model"].set_items(shown)
        sec["box"].setVisible(bool(shown))
        sec["status_lbl"].setText(f"{len(shown)} título(s)" if shown else "")
        self._update_empty()
        self.update_status()
        sel = self.host._movies_selected_tmdb_id
        if sel and self._shown_key is None:
            from core.missing_movies_cache import cache_key
            for it in shown:
                if cache_key(_mtype(it), it["tmdb_id"]) == sel:
                    self._show_item(it, sec["rowdef"]["title"])
                    break

    def _with_text(self, items):
        needle = self.search.text().strip().lower()
        if not needle:
            return list(items)
        return [i for i in items if needle in (i.get("title") or "").lower()]

    def _apply_text(self):
        for sec in self._sections:
            if sec["state"] != "ready":
                continue
            shown = self._with_text(sec["base"])
            sec["model"].set_items(shown)
            sec["box"].setVisible(bool(shown))
        self._update_empty()
        self.update_status()

    # ── IDs en servidor (tinte "En el servidor") ──

    def _snapshot_owned(self):
        owned = set(self._owned_ids) if hasattr(self, "_owned_ids") else set()
        try:
            for r in self.host._movies_results or []:
                if r.get("in_server"):
                    owned.add((r.get("media_type", "movie"), r.get("tmdb_id")))
        except Exception:
            pass
        self._owned = owned

    def _refresh_server_ids(self):
        if not (self.config.get("jellyfin_enabled") or self.config.get("plex_enabled")):
            self._owned_ids = set()
            self._server_ids_ready = True
            self._snapshot_owned()
            self._refresh_states()
            return

        def worker():
            movie_ids, series_ids = set(), set()
            try:
                from core.media_server_refresh import (get_jellyfin_movies, get_jellyfin_series,
                                                       get_plex_movies, get_plex_series)
                cfg = self.config
                if cfg.get("jellyfin_enabled"):
                    for m in (get_jellyfin_movies(cfg.get("jellyfin_host", ""),
                                                  cfg.get("jellyfin_api_key", "")) or []):
                        if m.get("tmdb_id"):
                            movie_ids.add(m["tmdb_id"])
                    for s in (get_jellyfin_series(cfg.get("jellyfin_host", ""),
                                                  cfg.get("jellyfin_api_key", "")) or []):
                        if s.get("tmdb_id"):
                            series_ids.add(s["tmdb_id"])
                if cfg.get("plex_enabled"):
                    for m in (get_plex_movies(cfg.get("plex_host", ""), cfg.get("plex_token", "")) or []):
                        if m.get("tmdb_id"):
                            movie_ids.add(m["tmdb_id"])
                    for s in (get_plex_series(cfg.get("plex_host", ""), cfg.get("plex_token", "")) or []):
                        if s.get("tmdb_id"):
                            series_ids.add(s["tmdb_id"])
            except Exception:
                _log.warning("Recomendado: no se pudo leer la biblioteca del servidor de medios",
                             exc_info=True)
            ui(lambda: self._apply_server_ids(movie_ids, series_ids))
        run_in_thread(worker)

    def _apply_server_ids(self, movie_ids, series_ids):
        self._owned_ids = {("movie", i) for i in movie_ids} | {("tv", i) for i in series_ids}
        self._server_ids_ready = True
        self._snapshot_owned()
        self._refresh_states()

    def _refresh_states(self):
        """Repinta insignias/botones (★🔒⚡⬇) sin recargar filas: el delegate
        pregunta el estado en cada pintado, basta un dataChanged."""
        self._snapshot_owned()
        for sec in self._sections:
            if sec["state"] == "ready":
                sec["model"].refresh_all()

    def _refresh_card(self, key: tuple):
        for sec in self._sections:
            if sec["state"] == "ready":
                sec["model"].refresh_key(key)

    def _card_state(self, item: dict) -> dict:
        mt, tid = _mtype(item), item.get("tmdb_id", 0)
        return {"in_server": (mt, tid) in self._owned,
                "dl": self._dl_state.get((mt, tid)),
                "auto_on": mt == "tv" and self.host._is_missing_ep_auto_enabled(tid),
                "fav": self.ctx.is_favorite(mt, tid),
                "locked": self.ctx.is_reserved(mt, tid),
                # Tooltips con quién/cuándo (igual que la tabla: ver test_mark_attribution).
                "fav_tip": self.ctx.favorite_tooltip(mt, tid),
                "lock_tip": self.ctx.reservation_tooltip(mt, tid),
                "requested": False}

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
        self._refresh_server_ids()
        self.update_status()
        self.render()
        self.host._push_missing_movies_to_ftp()

    # ── Acciones de card ──

    def _on_card_selected(self, item: dict):
        sec_title = next((s["rowdef"]["title"] for s in self._sections
                          if any(i is not None and _key(i) == _key(item) for i in s["model"].items)),
                         "")
        self._show_item(item, sec_title)

    def _on_card_action(self, item: dict, action_id: str):
        mt, tid = _mtype(item), item.get("tmdb_id", 0)
        try:
            year = int(item.get("year") or 0) or None
        except (TypeError, ValueError):
            year = None
        is_tv = mt == "tv"
        if action_id == "auto":
            self.host._toggle_missing_ep_auto_complete(tid)
            self._refresh_card((mt, tid))
        elif action_id == "search":
            win = self.host.window
            if win is not None:
                win.amule_search(item.get("title", ""), expected_year=year, is_movie=not is_tv)
        elif action_id == "copy":
            QGuiApplication.clipboard().setText(f"{item.get('title', '')} ({item.get('year', '')})")
            self.ctx.set_status(f"Copiado: {item.get('title', '')} ({item.get('year', '')})", SUCCESS_COLOR)
        elif action_id == "download":
            self._download(item, year, is_tv)
        elif action_id == "dismiss":
            if self._shown_key == (mt, tid):
                self._shown_key = None
                self._clear_detail()
            self.host._dismiss_missing_movie({"media_type": mt, "tmdb_id": tid,
                                              "title": item.get("title", "")})
        elif action_id == "fav":
            self.ctx.toggle_favorite(mt, tid, item.get("title", ""))
            self._refresh_card((mt, tid))
        elif action_id == "lock":
            size = self.ctx.best_known_size_bytes(mt, tid, 0)
            self.ctx.toggle_reservation(self.window(), mt, tid, item.get("title", ""), size)
            self._refresh_card((mt, tid))

    def _download(self, r, year, is_tv):
        from core.amule_download import auto_download
        key = (_mtype(r), r.get("tmdb_id", 0))
        if self._dl_state.get(key) == "busy":
            return
        self._dl_state[key] = "busy"
        self._refresh_card(key)
        query = f"{r.get('title', '')} 1x01" if is_tv else r.get("title", "")
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
                    self._dl_state[key] = "ok"
                    self.ctx.set_status(f"Descarga lanzada: {name[:60]}", SUCCESS_COLOR)
                elif (why or "").startswith("ya en completados"):
                    self._dl_state[key] = "already"
                    self.ctx.set_status(f"Ya en completados de aMule (no se vuelve a bajar): {name[:60]}",
                                        WARNING_COLOR)
                else:
                    self._dl_state[key] = "fail"
                    self.ctx.set_status(f"{query}: {why}", WARNING_COLOR)
                self._refresh_card(key)
            ui(apply)
        run_in_thread(worker)

    # ── Ficha ──

    def _clear_detail(self):
        for w in (self.d_title, self.d_meta, self.d_cert, self.d_cast, self.d_overview):
            w.setText("")
        self.poster.setPixmap(QPixmap())
        self.poster.setText("Pulsa una obra\npara ver su ficha")

    def _show_item(self, item: dict, list_label: str = ""):
        from core.missing_movies_cache import cache_key
        self.host._movies_selected_tmdb_id = cache_key(_mtype(item), item.get("tmdb_id", 0))
        self._shown_key = _key(item)
        is_tv = _mtype(item) == "tv"
        self.d_title.setText(item.get("title", ""))
        meta = " · ".join(x for x in ["📺 Serie" if is_tv else "🎬 Película", item.get("year") or "",
                                      list_label or "",
                                      f"⭐ {(item.get('vote') or 0):.1f}"] if x)
        genres = [self._genre_name(gid) for gid in (item.get("genre_ids") or [])]
        genres = [g for g in genres if g]
        if genres:
            meta += " · " + ", ".join(genres)
        self.d_meta.setText(meta)
        self.d_cert.setText("Serie" if is_tv else "Clasificación: …")
        self.d_cast.setText("Reparto: …")
        self.d_overview.setText(item.get("overview") or "Sin sinopsis disponible")
        self.poster.setPixmap(QPixmap())
        self.poster.setText("…")
        token = object()
        self._poster_token = token
        tmdb = self.host.tmdb
        poster_url = item.get("poster_url")

        def worker():
            out = {"poster_pm": self.delegate.posters.get(poster_url), "poster_raw": None,
                   "cast": [], "cert": None}
            if out["poster_pm"] is None and poster_url:
                try:
                    resp = requests.get(poster_url, timeout=8)
                    out["poster_raw"] = resp.content if resp.ok else None
                except Exception:
                    out["poster_raw"] = None
            try:
                out["cast"] = tmdb.get_top_cast(_mtype(item), item.get("tmdb_id", 0), 6)
            except Exception:
                out["cast"] = []
            if not is_tv:
                try:
                    out["cert"] = tmdb.get_movie_certification(item.get("tmdb_id", 0)) or ""
                except Exception:
                    out["cert"] = ""
            ui(lambda: self._apply_detail(token, out))
        run_in_thread(worker)

    def _genre_name(self, gid):
        for kind in ("movie", "tv"):
            for name, ids in self._genre_names.get(kind, {}).items():
                if gid in ids:
                    return name
        return ""

    def _apply_detail(self, token, out):
        if token is not self._poster_token:
            return
        pm = out.get("poster_pm")
        raw = out.get("poster_raw")
        if raw:
            loaded = QPixmap()
            if loaded.loadFromData(raw):
                pm = loaded
        if pm is not None and not pm.isNull():
            self.poster.setPixmap(pm.scaled(170, 245, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.poster.setText("")
        else:
            self.poster.setText("Sin póster")
        cast = out.get("cast") or []
        self.d_cast.setText(("Reparto: " + ", ".join(cast)) if cast else "Reparto: sin datos")
        if "cert" in out:
            self.d_cert.setText(f"Clasificación: {out['cert']}" if out["cert"] else "Clasificación: sin dato")

    # ── Ver todo ──

    def _open_see_all(self, sec):
        from gui_qt.movies.see_all import SeeAllDialog
        dlg = SeeAllDialog(self, self.host, sec["kind"], sec["rowdef"], self._static_filters(sec["kind"]),
                           self._watch_only(), self._card_state, self._on_card_action,
                           self._on_card_selected)
        dlg.exec()
