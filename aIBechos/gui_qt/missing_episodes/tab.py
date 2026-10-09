"""
Pestaña "Episodios que faltan" en Qt.

Misma información y mismos filtros que la versión Tk (core/missing_ep_rows.py
los comparte), pero sobre un QTreeView virtual: desplegar una temporada de
cientos de episodios ya no crea ni un solo widget por episodio.

Novedad frente a Tk: selección múltiple (Ctrl/Mayús) y acciones sobre la
selección -- descargar, ignorar o copiar varios episodios/temporadas de una vez.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QProgressBar, QPushButton, QSplitter, QTreeView,
                               QVBoxLayout, QWidget)

from core import missing_ep_rows as mer
from core.amule_download import auto_download, typical_size_for_query, typical_size_for_series
from core.amule_search import build_amule_query
from core.applog import get_logger
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui
from gui_qt.dialogs import AmuleTemplateDialog, DubHiddenDialog, confirm
from gui_qt.actions import Action, ActionsDelegate
from gui_qt.missing_episodes.model import (COL_ACTIONS, COL_FAV, COL_LOCK, COL_NAME,
                                           COL_PREMIERE, COL_SUMMARY, COL_TRENDING,
                                           MissingEpisodesModel, SORT_KEYS)
from gui_qt.missing_episodes.scan_service import MissingEpScanService
from gui_qt.missing_episodes.side_panel import MissingEpSidePanel

_log = get_logger("aIBechos.qt", "app.log")

TABLE_ID = "episodios"   # misma clave de orden guardado que la versión Tk
_LIVE_RENDER_EVERY = 15   # reescaneo completo: repintar cada N filas nuevas, no en cada una

# (clave de config, texto, valor por defecto) -- mismos ajustes que Tk.
_SWITCHES = [
    ("missing_ep_hide_complete", "Ocultar completas", True),
    ("missing_ep_pin_favorites", "Favoritos/protegidos primero", True),
    ("missing_ep_hide_no_dub", "Ocultar sin doblaje ES", False),
    ("missing_ep_hide_ai_dismissed", "Ocultar descartados por IA", False),
    ("missing_ep_show_ignored", "Mostrar ignoradas", False),
]


class MissingEpisodesTab(QWidget):
    def __init__(self, ctx, parent=None, host=None):
        super().__init__(parent)
        self.ctx = ctx
        self.host = host   # QtAppCore (subidas en curso), opcional
        self.config = ctx.config
        self._results: list = []           # series con hueco (lo de siempre)
        self._complete_rows = None         # completas, cargadas bajo demanda
        self._dub_cache: dict = {}
        saved = (self.config.get("table_sort", {}) or {}).get(TABLE_ID)
        if isinstance(saved, dict) and "key" in saved:
            self._sort_key, self._sort_asc = saved.get("key"), bool(saved.get("asc", True))
        else:
            self._sort_key, self._sort_asc = "name", True
        self._switches: dict = {}
        self._scanning = False             # escaneo O comprobación de doblaje en curso
        self._cancel_event = None
        self._live_counter = 0
        self._rescanning: set = set()      # tmdb_ids con reescaneo individual en curso
        self._ai_busy = False
        from core.shared_dub_verdicts import load_local_cache as _load_shared_verdicts
        self._shared_dub_verdicts = _load_shared_verdicts() or {}
        self.scan = MissingEpScanService(ctx, lambda: self._dub_cache)
        self._build_ui()
        self._wire_context()
        self.reload_from_disk()

    # ── Construcción ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 Comprobar")
        self.scan_btn.setProperty("accent", True)
        self.scan_btn.setToolTip("Cruzar las series del servidor con TMDB y listar los episodios que "
                                 "faltan. Aprovecha la caché compartida, así que suele ser rápido.")
        self.scan_btn.clicked.connect(lambda: self.start_scan(force_full=False))
        self.full_btn = QPushButton("Reescaneo completo")
        self.full_btn.setToolTip("Rehacer el escaneo desde cero, ignorando la caché compartida. Tarda "
                                 "bastante más: úsalo si sospechas que los huecos que ves no son los de verdad.")
        self.full_btn.clicked.connect(lambda: self.start_scan(force_full=True))
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setProperty("danger", True)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.cancel_btn.hide()
        reload_btn = QPushButton("↻ Recargar")
        reload_btn.setToolTip("Volver a leer la lista guardada en disco y la compartida en el FTP")
        reload_btn.clicked.connect(self._reload_and_sync)
        self.reload_btn = reload_btn
        for b in (self.scan_btn, self.full_btn, self.cancel_btn, reload_btn):
            head.addWidget(b)
        head.addStretch(1)
        title = QLabel("Episodios que faltan")
        title.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(title)
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
        for key, text, default in _SWITCHES:
            cb = QCheckBox(text)
            cb.setChecked(bool(self.config.get(key, default)))
            cb.toggled.connect(lambda checked, k=key: self._on_switch(k, checked))
            self._switches[key] = (cb, text)
            filters.addWidget(cb)
            if key == "missing_ep_hide_no_dub":
                # Clic derecho: qué series se ocultaron por doblaje y por qué.
                cb.setContextMenuPolicy(Qt.CustomContextMenu)
                cb.customContextMenuRequested.connect(lambda _p: self._show_dub_hidden())
                cb.setToolTip("Clic derecho: ver qué series quedan ocultas por esto y por qué")
        root.addLayout(filters)

        self.status_lbl = QLabel("Sin comprobar todavía")
        self.status_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR};")
        root.addWidget(self.status_lbl)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        self.progress.hide()
        root.addWidget(self.progress)

        # Barra de acciones sobre la selección -- solo visible con algo
        # seleccionado que admita acciones en bloque.
        self.bulk_bar = QWidget()
        bulk = QHBoxLayout(self.bulk_bar)
        bulk.setContentsMargins(0, 0, 0, 0)
        self.bulk_lbl = QLabel()
        self.bulk_dl = QPushButton("⬇ Descargar")
        self.bulk_dl.clicked.connect(self._bulk_download)
        self.bulk_ignore = QPushButton("🚫 Ignorar")
        self.bulk_ignore.clicked.connect(lambda: self._bulk_ignore(True))
        self.bulk_restore = QPushButton("↺ Restaurar")
        self.bulk_restore.clicked.connect(lambda: self._bulk_ignore(False))
        self.bulk_copy = QPushButton("📋 Copiar nombres")
        self.bulk_copy.clicked.connect(self._bulk_copy)
        for w in (self.bulk_lbl, self.bulk_dl, self.bulk_ignore, self.bulk_restore, self.bulk_copy):
            bulk.addWidget(w)
        bulk.addStretch(1)
        self.bulk_bar.hide()
        root.addWidget(self.bulk_bar)

        self.model = MissingEpisodesModel(self.ctx, self)
        self.model.actions_provider = self._actions_for

        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setUniformRowHeights(True)      # clave para el rendimiento con miles de filas
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setMouseTracking(True)
        self.view.setExpandsOnDoubleClick(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.setIndentation(18)
        self.delegate = ActionsDelegate(self.view)
        self.delegate.actionTriggered.connect(self._on_action)
        self.view.setItemDelegateForColumn(COL_ACTIONS, self.delegate)
        hdr = self.view.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col, w in ((COL_FAV, 30), (COL_LOCK, 30), (COL_PREMIERE, 90), (COL_SUMMARY, 200),
                       (COL_TRENDING, 80), (COL_ACTIONS, 230)):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            hdr.resizeSection(col, w)
        hdr.setSortIndicatorShown(True)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header_click)
        self._update_sort_indicator()
        self.view.clicked.connect(self._on_clicked)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._on_selection_changed())

        self.side = MissingEpSidePanel(self.ctx)
        self.side.ai_requested.connect(self.ask_ai_current)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(self.side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 260])
        root.addWidget(splitter, 1)

    def _wire_context(self):
        self.ctx.favorites_changed.connect(self._on_marks_changed)
        self.ctx.reservations_changed.connect(self._on_marks_changed)
        self.ctx.missing_cache_changed.connect(self.reload_from_disk)

    # ── Datos ──

    def reload_from_disk(self):
        from core.missing_episodes_cache import load_cache
        from core.spanish_dub_cache import load_cache as load_dub
        cache = load_cache()
        self._results = mer.rows_from_cache(cache, complete=False)
        self._complete_rows = None
        self._complete_count = mer.complete_count_in_cache(cache)
        self._last_scan_ts = (cache.get("_meta") or {}).get("last_scan_ts")
        try:
            self._dub_cache = load_dub() or {}
        except Exception:
            self._dub_cache = {}
        self.render()

    def _reload_and_sync(self):
        self.ctx._last_sync.pop("episodios_que_faltan", None)
        self.reload_from_disk()
        self.ctx.sync_missing_episodes()

    def on_shown(self):
        """Al entrar en la pestaña: traer lo que hayan compartido otros
        equipos (con freno, ver AppContext._throttled)."""
        self.ctx.sync_missing_episodes()
        self.ctx.sync_favorites()
        self.ctx.sync_reservations()
        self.ctx.fetch_shared_dub_verdicts(self._apply_shared_dub_verdicts)
        if self.host is not None:
            # Dueños del rayo en otros equipos (solo informativo, ver core/auto_series.py)
            self.host._sync_auto_series_from_ftp()

    def _flag(self, key) -> bool:
        return self._switches[key][0].isChecked()

    def _filters(self) -> mer.MissingEpFilters:
        return mer.MissingEpFilters(
            query=self.search.text(),
            show_ignored=self._flag("missing_ep_show_ignored"),
            hide_ai_dismissed=self._flag("missing_ep_hide_ai_dismissed"),
            hide_no_dub=self._flag("missing_ep_hide_no_dub"),
            hide_complete=self._flag("missing_ep_hide_complete"))

    def _all_rows(self) -> list:
        hide_complete = self._flag("missing_ep_hide_complete")
        if not hide_complete and self._complete_rows is None:
            from core.missing_episodes_cache import load_cache
            self._complete_rows = mer.rows_from_cache(load_cache(), complete=True)
        return mer.merge_all_rows(self._results, self._complete_rows, hide_complete)

    def _visible_rows(self) -> list:
        filters = self._filters()
        rows = [v for v in (mer.visible_row(r, filters, self._dub_cache) for r in self._all_rows())
                if v is not None]
        rows.sort(key=mer.sort_key_fn(self._sort_key), reverse=not self._sort_asc)
        if self._flag("missing_ep_pin_favorites"):
            rows.sort(key=lambda r: not (self.ctx.is_favorite("tv", r["tmdb_id"])
                                          or self.ctx.is_reserved("tv", r["tmdb_id"])))
        return rows

    def render(self):
        """Recalcula qué series se ven y las vuelca al modelo, conservando
        qué series/temporadas estaban desplegadas, la selección y el scroll."""
        expanded = self._expanded_keys()
        current = self.side.current_row()
        scroll = self.view.verticalScrollBar().value()
        rows = self._visible_rows()
        self.model.set_rows(rows, self._dub_cache)
        self._restore_expanded(expanded)
        if current is not None:
            idx = self.model.series_index(current["tmdb_id"])
            if idx.isValid():
                self.view.setCurrentIndex(idx)
        self.view.verticalScrollBar().setValue(scroll)
        self._update_counters()
        self._update_status_text(len(rows))

    def _expanded_keys(self) -> set:
        keys = set()
        for i in range(self.model.rowCount()):
            sidx = self.model.index(i, 0)
            if not self.view.isExpanded(sidx):
                continue
            node = self.model.node(sidx)
            keys.add(node.tmdb_id)
            for j in range(len(node.children or [])):
                cidx = self.model.index(j, 0, sidx)
                child = self.model.node(cidx)
                if child.kind == "season" and self.view.isExpanded(cidx):
                    keys.add((node.tmdb_id, child.season))
        return keys

    def _restore_expanded(self, keys: set):
        if not keys:
            return
        for i in range(self.model.rowCount()):
            sidx = self.model.index(i, 0)
            node = self.model.node(sidx)
            if node.tmdb_id not in keys:
                continue
            self.view.setExpanded(sidx, True)
            for j in range(self.model.rowCount(sidx)):
                cidx = self.model.index(j, 0, sidx)
                child = self.model.node(cidx)
                if child.kind == "season" and (node.tmdb_id, child.season) in keys:
                    self.view.setExpanded(cidx, True)

    def _update_counters(self):
        n_ignored = sum(1 for r in self._all_rows() if r.get("ignored"))
        n_ai = len(mer.ai_dismissed_rows(self._results)) if self._flag("missing_ep_hide_ai_dismissed") else 0
        n_dub = (len(mer.dub_hidden_rows(self._results, self._dub_cache))
                 if self._flag("missing_ep_hide_no_dub") else 0)
        n_complete = (len(self._complete_rows) if self._complete_rows is not None
                      else self._complete_count)
        labels = {
            "missing_ep_show_ignored": f"Mostrar {n_ignored} ignoradas" if n_ignored else None,
            "missing_ep_hide_ai_dismissed": f"Ocultar {n_ai} descartados por IA" if n_ai else None,
            "missing_ep_hide_no_dub": f"Ocultar {n_dub} sin doblaje ES" if n_dub else None,
            "missing_ep_hide_complete": f"Ocultar {n_complete} completas" if n_complete else None,
        }
        for key, (cb, text) in self._switches.items():
            cb.setText(labels.get(key) or text)

    def _update_status_text(self, n_visible: int):
        when = ""
        if self._last_scan_ts:
            mins = int((time.time() - self._last_scan_ts) / 60)
            when = ("hace un momento" if mins < 1 else f"hace {mins} min" if mins < 60
                    else f"hace {mins // 60} h")
        pending = [r for r in self._results if not r.get("ignored")]
        if not self._results:
            text = "Sin comprobar todavía" if not when else f"Sin huecos conocidos -- último escaneo {when}"
        elif not pending:
            text = "🎉 No falta ningún episodio" + (f" -- último escaneo {when}" if when else "")
        else:
            text = f"{len(pending)} serie(s) con episodios que faltan" + (
                f" -- último escaneo {when}" if when else "")
        if not self._flag("missing_ep_hide_complete") and self._complete_count:
            text += f" -- mostrando además {self._complete_count} completa(s)"
        if n_visible != len(pending) and self.search.text().strip():
            text += f" -- {n_visible} coinciden con el filtro"
        self.status_lbl.setText(text)

    # ── Interruptores / orden ──

    def _on_switch(self, key: str, checked: bool):
        self.config.set(key, checked)
        self.config.save()
        if key == "missing_ep_hide_no_dub" and checked:
            self._on_enable_hide_no_dub()
            return
        self.render()

    def _on_enable_hide_no_dub(self):
        """Con episodios sin comprobar (o caducados), pregunta si comprobarlos
        ya o reutilizar lo guardado -- lanzarlo solo se sentía como si la app
        se pusiera a escanear el servidor sin pedirlo (ver la versión Tk)."""
        if not mer.dub_pending_checks(self._results, self._dub_cache):
            self.render()
            return
        if confirm(self, "Comprobar doblaje castellano",
                   "Hay episodios sin comprobar (o cuyo resultado ya caducó). ¿Comprobarlos ahora "
                   "(eldoblaje.com/wiki/Crunchyroll/RTVE) o reutilizar el último resultado guardado?",
                   "Comprobar ahora consulta eldoblaje.com, la wiki de doblaje, Crunchyroll y RTVE Play "
                   "por serie (puede tardar si faltan muchas). Reutilizar es inmediato, pero los "
                   "episodios aún sin comprobar se quedarán ocultos hasta que se comprueben de verdad.",
                   confirm_text="Comprobar ahora", cancel_text="Reutilizar lo que haya"):
            self.start_dub_check()
        else:
            self.render()

    def _on_header_click(self, col: int):
        key = SORT_KEYS.get(col)
        if key is None:
            self._update_sort_indicator()
            return
        if key == self._sort_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key, self._sort_asc = key, True
        all_saved = dict(self.config.get("table_sort", {}) or {})
        all_saved[TABLE_ID] = {"key": self._sort_key, "asc": self._sort_asc}
        self.config.set("table_sort", all_saved)
        self.config.save()
        self._update_sort_indicator()
        self.render()

    def _update_sort_indicator(self):
        col = next((c for c, k in SORT_KEYS.items() if k == self._sort_key), COL_NAME)
        self.view.header().setSortIndicator(col, Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder)

    def _on_marks_changed(self):
        if self._flag("missing_ep_pin_favorites"):
            self.render()
        else:
            self.model.refresh_series_columns()

    # ── Clics ──

    def _on_clicked(self, index: QModelIndex):
        node = self.model.node(index)
        if node is None:
            return
        if node.kind == "series":
            if index.column() == COL_FAV:
                self.ctx.toggle_favorite("tv", node.tmdb_id, node.r["name"])
                return
            if index.column() == COL_LOCK:
                size = self.ctx.best_known_size_bytes("tv", node.tmdb_id, 0)
                self.ctx.toggle_reservation(self, "tv", node.tmdb_id, node.r["name"], size)
                return
        if self.side.current_row() is None or self.side.current_row().get("tmdb_id") != node.tmdb_id:
            self.side.show_row(self._base_row(node.tmdb_id) or node.r)

    def _base_row(self, tmdb_id):
        """La fila ORIGINAL (no la copia filtrada que pinta el modelo) --
        las mutaciones (ignorar...) se hacen sobre esta."""
        for r in self._results + (self._complete_rows or []):
            if r.get("tmdb_id") == tmdb_id:
                return r
        return None

    def _actions_for(self, node) -> list:
        r = node.r
        if node.kind == "series":
            ignored = bool(r.get("ignored"))
            src = r.get("source") or ""
            auto = []
            if self.host is not None:
                tid = r["tmdb_id"]
                on = self.host._is_missing_ep_auto_enabled(tid)
                auto.append(Action("auto", "⚡", theme.ACCENT if on else theme.ICON_NEUTRAL,
                                   self.host._auto_btn_tooltip(tid)))
                if on:
                    auto.append(Action("force", "⤢", theme.ICON_DL_IDLE,
                                       "Forzar búsqueda ahora: ignora la espera de reintento y la gracia de "
                                       "24 h de este capítulo; si está descargando, busca alternativa (Unstuck)"))
            return auto + [
                Action("open_server", "J" if src == "jellyfin" else "P" if src == "plex" else "?",
                       "#4b3f8f" if src == "jellyfin" else "#9c7a10",
                       f"Abrir en {'Jellyfin' if src == 'jellyfin' else 'Plex'}", enabled=bool(src)),
                Action("ignore_series", "↺" if ignored else "🚫",
                       theme.ICON_RESTORE if ignored else theme.ICON_IGNORE,
                       "Restaurar serie (volver a contarla como faltante)" if ignored
                       else "Ignorar toda la serie (no contará como faltante)"),
                Action("rescan", "…" if r["tmdb_id"] in self._rescanning else "↻", theme.ICON_NEUTRAL,
                       "Volver a comprobar esta serie contra el servidor: recalcula qué episodios "
                       "faltan de verdad ahora mismo, sin esperar al siguiente escaneo completo, y "
                       "repasa su doblaje castellano.",
                       enabled=r["tmdb_id"] not in self._rescanning and not self._scanning),
                Action("delete_series", "🗑", theme.ICON_DL_FAIL,
                       "Borrar la serie DEL SERVIDOR, con todos sus episodios. Pide confirmación antes y "
                       "queda registrado en el historial de borrados."),
            ]
        if node.kind == "season":
            ignored = node.season in (r.get("ignored_seasons") or set())
            acts = [
                Action("ignore_season", "↺" if ignored else "🚫",
                       theme.ICON_RESTORE if ignored else theme.ICON_IGNORE,
                       "Restaurar temporada (volver a contarla como faltante)" if ignored
                       else "Ignorar toda la temporada (no contará como faltante)"),
                Action("search_season", "🔍", theme.ICON_AMULE,
                       "Buscar esta temporada en aMule (abre la pestaña Descargas)"),
                Action("dl_season", "⬇", self.model.dl_state.get((r["tmdb_id"], node.season), theme.ICON_DL_IDLE),
                       "Descargar temporada completa (solo episodios que faltan; respeta ignorados "
                       "salvo con Mostrar ignoradas)"),
            ]
            acts += self._link_actions("custom_links_season")
            return acts
        if node.kind == "episode":
            season, ep = node.line[0], node.line[1]
            ignored = ep in (r.get("ignored_episodes") or {}).get(season, set())
            acts = [
                Action("copy", "📋", theme.ICON_COPY, "Copiar nombre del episodio al portapapeles"),
                Action("dl_episode", "⬇", self.model.dl_state.get(node.episode_key(), theme.ICON_DL_IDLE),
                       "Buscar en aMule y descargar automáticamente el mejor candidato para este "
                       "episodio (en segundo plano).\nColor: azul=reposo, ámbar=en curso, "
                       "verde=lanzado, rojo=sin candidato/error"),
                Action("ignore_episode", "↺" if ignored else "🚫",
                       theme.ICON_RESTORE if ignored else theme.ICON_IGNORE,
                       "Restaurar episodio (volver a contarlo como faltante)" if ignored
                       else "Ignorar este episodio (no contará como faltante)"),
                Action("search_episode", "🔍", theme.ICON_AMULE,
                       "Buscar este episodio en aMule (abre la pestaña Descargas)"),
            ]
            acts += self._link_actions("custom_links_episode")
            return acts
        return []

    def _link_actions(self, config_key: str) -> list:
        out = []
        for i, link in enumerate(self.config.get(config_key, []) or []):
            if not link.get("url_template"):
                continue
            short = (link.get("name", "").split() or ["🔗"])[-1]
            out.append(Action(f"link:{config_key}:{i}", short[:1], theme.ICON_LINKS,
                              f"Enlace personalizado: {link.get('name', short)}"))
        return out

    def _on_action(self, index: QModelIndex, action_id: str):
        node = self.model.node(index)
        if node is None:
            return
        r = node.r
        if action_id == "auto":
            self.host._toggle_missing_ep_auto_complete(node.tmdb_id)
            self.refresh_auto(node.tmdb_id)
        elif action_id == "force":
            self.host._force_search_missing_series(node.tmdb_id)
        elif action_id == "open_server":
            self.ctx.open_in_media_server(r.get("source"), r.get("server_id"))
        elif action_id == "ignore_series":
            self._set_series_ignored(node.tmdb_id, not r.get("ignored"))
        elif action_id == "rescan":
            self.rescan_series(node.tmdb_id)
        elif action_id == "delete_series" and self.host is not None:
            self.host._confirm_delete_missing_ep_series(r)
        elif action_id == "ignore_season":
            self._set_seasons_ignored([(node.tmdb_id, node.season)],
                                      node.season not in (r.get("ignored_seasons") or set()))
        elif action_id == "ignore_episode":
            season, ep = node.line[0], node.line[1]
            self._set_episodes_ignored([node.episode_key()],
                                       ep not in (r.get("ignored_episodes") or {}).get(season, set()))
        elif action_id == "copy":
            self._copy([node.line[3]])
        elif action_id == "dl_episode":
            self._download_episodes([node])
        elif action_id == "search_episode":
            self._amule_search(self._query_for(r, node.line[0], node.line[1]))
        elif action_id == "search_season":
            from core.amule_search import build_amule_season_query
            self._amule_search(build_amule_season_query(
                r["name"], node.season, self.config.get("series_search_patterns", {}) or {},
                prefers_castellano=self.ctx_prefers_castellano(r["name"])))
        elif action_id == "dl_season":
            self._download_season(node)
        elif action_id.startswith("link:"):
            _, config_key, i = action_id.split(":")
            link = (self.config.get(config_key, []) or [])[int(i)]
            variables = {"serie": r["name"], "tmdb_id": r["tmdb_id"], "temporada": node.season}
            if node.kind == "episode":
                season, ep, title, name, _air = node.line
                variables.update(episodio=ep, titulo=title, nombre_archivo=name)
            self.side.open_link_with_ruta(link.get("url_template", ""), variables, r,
                                          link.get("background", False))

    def ctx_prefers_castellano(self, name: str) -> bool:
        return self.host._series_prefers_castellano(name) if self.host is not None else False

    def _amule_search(self, query: str):
        win = getattr(self.host, "window", None) if self.host is not None else None
        if win is not None:
            win.amule_search(query, is_movie=False)

    # ── Ignorar ──

    def _set_series_ignored(self, tmdb_id, ignored: bool):
        from core.missing_episodes_cache import load_cache, save_cache
        cache = dict(load_cache())
        if mer.set_series_ignored(cache, tmdb_id, ignored):
            save_cache(cache)
        for r in self._all_rows():
            if r["tmdb_id"] == tmdb_id:
                r["ignored"] = ignored
        self.render()

    def _set_seasons_ignored(self, keys: list, ignored: bool):
        from core.missing_episodes_cache import load_cache, save_cache
        cache = dict(load_cache())
        changed = False
        for tmdb_id, season in keys:
            changed |= mer.set_season_ignored(cache, tmdb_id, season, ignored)
            r = self._base_row(tmdb_id)
            if r is not None:
                mer.apply_season_ignored_to_row(r, season, ignored)
        if changed:
            save_cache(cache)
        self.render()

    def _set_episodes_ignored(self, keys: list, ignored: bool):
        from core.missing_episodes_cache import load_cache, save_cache
        cache = dict(load_cache())
        changed = False
        for tmdb_id, season, ep in keys:
            changed |= mer.set_episode_ignored(cache, tmdb_id, season, ep, ignored)
            r = self._base_row(tmdb_id)
            if r is not None:
                mer.apply_episode_ignored_to_row(r, season, ep, ignored)
        if changed:
            save_cache(cache)
        self.render()

    # ── Copiar ──

    def _copy(self, names: list):
        QGuiApplication.clipboard().setText("\n".join(names))
        shown = names[0] if len(names) == 1 else f"{len(names)} nombres"
        self.ctx.set_status(f"Copiado: {shown}", theme.SUCCESS_COLOR)

    # ── Descargas en aMule ──

    def _query_for(self, r: dict, season: int, ep: int) -> str:
        # Sin el título del episodio: recorta muchísimo los resultados de aMule
        # y best_result ya exige temporada×episodio (ver la versión Tk).
        return build_amule_query(r["name"], season, ep,
                                 templates=self.config.get("series_search_patterns", {}) or {},
                                 prefers_castellano=self.ctx_prefers_castellano(r["name"]))

    def _confirm_dub(self, r: dict, season: int, eps: list) -> bool:
        """Con "Ocultar sin doblaje ES" activo, avisa antes de descargar
        episodios sin doblaje confirmado. True = seguir."""
        if not self._flag("missing_ep_hide_no_dub"):
            return True
        warn = mer.dub_warning_eps(r, season, eps, self._dub_cache)
        if not warn:
            return True
        ep_list = ", ".join(f"{season}x{e:02d}" for e in warn)
        return confirm(self, "Sin doblaje ES confirmado",
                       f"{r['name']}: {ep_list} sin doblaje castellano confirmado. ¿Descargar igual?",
                       "Tienes activo «Ocultar sin doblaje ES» y estos episodios no tienen doblaje "
                       "confirmado (ni en TMDB/eldoblaje ni por la IA). Lo que se encuentre en aMule "
                       "probablemente sea V.O.S.",
                       confirm_text="Descargar igual")

    def _set_dl_state(self, key, color):
        self.model.dl_state[key] = color
        node = (self.model.find_episode_node(key) if len(key) == 3
                else self.model.find_season_node(*key))
        if node is not None:
            self.model.refresh_node(node)

    def _download_episodes(self, nodes: list):
        """Descarga uno o varios episodios, en serie (aMule solo admite una
        conexión EC a la vez) -- cada uno con su propio color en el ⬇."""
        nodes = [n for n in nodes if self.model.dl_state.get(n.episode_key()) != theme.ICON_DL_BUSY]
        if not nodes:
            return
        by_season: dict = {}
        for n in nodes:
            by_season.setdefault((n.tmdb_id, n.line[0]), []).append(n)
        for (tmdb_id, season), group in by_season.items():
            if not self._confirm_dub(group[0].r, season, [n.line[1] for n in group]):
                nodes = [n for n in nodes if (n.tmdb_id, n.line[0]) != (tmdb_id, season)]
        if not nodes:
            return
        jobs = [(n.episode_key(), n.r, self._query_for(n.r, n.line[0], n.line[1])) for n in nodes]
        for key, _r, _q in jobs:
            self._set_dl_state(key, theme.ICON_DL_BUSY)
        if len(jobs) > 1:
            self.ctx.set_status(f"Descargando {len(jobs)} episodio(s)…")

        def worker():
            ok_count = 0
            for i, (key, _r, query) in enumerate(jobs):
                typical = typical_size_for_query(self.config, query)
                ok, why, _h, chosen = auto_download(self.config, self.ctx.ec_lock, query, typical_size=typical)
                if ok:
                    ok_count += 1
                    color = theme.ICON_DL_OK
                    if len(jobs) == 1:
                        self.ctx.set_status(f"Descarga lanzada: {chosen[:60]}", theme.SUCCESS_COLOR)
                elif why.startswith("ya en completados"):
                    color = theme.ICON_DL_ALREADY
                    self.ctx.set_status(f"Ya en completados de aMule (no se vuelve a bajar): {chosen[:60]}",
                                        theme.WARNING_COLOR)
                else:
                    color = theme.ICON_DL_FAIL
                    if len(jobs) == 1:
                        self.ctx.set_status(f"{query}: {why}", theme.WARNING_COLOR)
                ui(lambda k=key, c=color: self._set_dl_state(k, c))
                if i < len(jobs) - 1:
                    time.sleep(1.2)   # no saturar EC
            if len(jobs) > 1:
                self.ctx.set_status(f"Lanzados {ok_count}/{len(jobs)} episodio(s)",
                                    theme.SUCCESS_COLOR if ok_count else theme.WARNING_COLOR)
        run_in_thread(worker)

    def _season_missing_eps(self, r: dict, season: int) -> list:
        from core.missing_episodes import apply_ignored_filter
        missing = r.get("missing", {}) or {}
        if not self._flag("missing_ep_show_ignored"):
            missing = apply_ignored_filter(missing, r.get("ignored_seasons"), r.get("ignored_episodes"))
        return sorted(missing.get(season, []) or [])

    def _download_season(self, node):
        self._download_seasons([(node.tmdb_id, node.season)])

    def _download_seasons(self, keys: list):
        jobs = []
        for tmdb_id, season in keys:
            r = self._base_row(tmdb_id)
            if r is None or self.model.dl_state.get((tmdb_id, season)) == theme.ICON_DL_BUSY:
                continue
            eps = self._season_missing_eps(r, season)
            if not eps:
                self.ctx.set_status(f"{r['name']} T{season} sin huecos", theme.PENDING_COLOR)
                continue
            if not self._confirm_dub(r, season, eps):
                continue
            jobs.append((tmdb_id, season, r, eps))
        if not jobs:
            return
        for tmdb_id, season, _r, _eps in jobs:
            self._set_dl_state((tmdb_id, season), theme.ICON_DL_BUSY)
        total = sum(len(j[3]) for j in jobs)
        self.ctx.set_status(f"Descargando {total} episodio(s) de {len(jobs)} temporada(s)…")

        def worker():
            ok_total = 0
            for tmdb_id, season, r, eps in jobs:
                try:
                    typical = typical_size_for_series(self.config, r["name"], season)
                except Exception:
                    typical = None
                ok_count = 0
                for ep in eps:
                    ok, _why, _h, _n = auto_download(self.config, self.ctx.ec_lock,
                                                     self._query_for(r, season, ep), typical_size=typical)
                    if ok:
                        ok_count += 1
                    time.sleep(1.2)
                ok_total += ok_count
                color = theme.ICON_DL_OK if ok_count == len(eps) else (
                    theme.ICON_DL_ALREADY if ok_count else theme.ICON_DL_FAIL)
                ui(lambda k=(tmdb_id, season), c=color: self._set_dl_state(k, c))
                self.ctx.set_status(f"{r['name']} T{season} lanzada: {ok_count}/{len(eps)} episodio(s)",
                                    theme.SUCCESS_COLOR if ok_count else theme.WARNING_COLOR)
            if len(jobs) > 1:
                self.ctx.set_status(f"Lanzados {ok_total}/{total} episodio(s)",
                                    theme.SUCCESS_COLOR if ok_total else theme.WARNING_COLOR)
        run_in_thread(worker)

    # ── Selección múltiple ──

    def _selected_nodes(self) -> list:
        seen, out = set(), []
        for idx in self.view.selectionModel().selectedRows(0):
            node = self.model.node(idx)
            if node is not None and id(node) not in seen:
                seen.add(id(node))
                out.append(node)
        return out

    def _on_selection_changed(self):
        nodes = self._selected_nodes()
        eps = [n for n in nodes if n.kind == "episode"]
        seasons = [n for n in nodes if n.kind == "season"]
        if len(nodes) < 2 or not (eps or seasons):
            self.bulk_bar.hide()
            return
        parts = []
        if eps:
            parts.append(f"{len(eps)} episodio(s)")
        if seasons:
            parts.append(f"{len(seasons)} temporada(s)")
        self.bulk_lbl.setText("Seleccionados: " + ", ".join(parts))
        self.bulk_copy.setEnabled(bool(eps))
        self.bulk_bar.show()

    def _bulk_download(self):
        nodes = self._selected_nodes()
        seasons = [(n.tmdb_id, n.season) for n in nodes if n.kind == "season"]
        season_set = set(seasons)
        eps = [n for n in nodes if n.kind == "episode" and (n.tmdb_id, n.line[0]) not in season_set]
        if seasons:
            self._download_seasons(seasons)
        if eps:
            self._download_episodes(eps)

    def _bulk_ignore(self, ignored: bool):
        nodes = self._selected_nodes()
        seasons = [(n.tmdb_id, n.season) for n in nodes if n.kind == "season"]
        eps = [n.episode_key() for n in nodes if n.kind == "episode"]
        if seasons:
            self._set_seasons_ignored(seasons, ignored)
        if eps:
            self._set_episodes_ignored(eps, ignored)

    def _bulk_copy(self):
        names = [n.line[3] for n in self._selected_nodes() if n.kind == "episode"]
        if names:
            self._copy(names)

    # ── Menú contextual ──

    def _context_menu(self, pos):
        index = self.view.indexAt(pos)
        node = self.model.node(index)
        if node is None:
            return
        # Clic derecho sobre algo no seleccionado: pasa a ser la selección.
        if not self.view.selectionModel().isSelected(index):
            self.view.selectionModel().select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        selected = self._selected_nodes()
        menu = QMenu(self)
        r = node.r

        def add(text, fn, enabled=True):
            act = QAction(text, menu)
            act.setEnabled(enabled)
            act.triggered.connect(fn)
            menu.addAction(act)

        eps = [n for n in selected if n.kind == "episode"]
        seasons = [n for n in selected if n.kind == "season"]
        if len(selected) > 1 and (eps or seasons):
            add(f"⬇ Descargar selección ({len(eps) + len(seasons)})", self._bulk_download)
            add("🚫 Ignorar selección", lambda: self._bulk_ignore(True))
            add("↺ Restaurar selección", lambda: self._bulk_ignore(False))
            if eps:
                add("📋 Copiar nombres", self._bulk_copy)
            menu.addSeparator()
        elif node.kind == "episode":
            add("📋 Copiar nombre", lambda: self._copy([node.line[3]]))
            add("⬇ Descargar en aMule", lambda: self._download_episodes([node]))
            ign = node.line[1] in (r.get("ignored_episodes") or {}).get(node.line[0], set())
            add("↺ Restaurar episodio" if ign else "🚫 Ignorar episodio",
                lambda: self._set_episodes_ignored([node.episode_key()], not ign))
            menu.addSeparator()
        elif node.kind == "season":
            add("⬇ Descargar temporada", lambda: self._download_season(node))
            ign = node.season in (r.get("ignored_seasons") or set())
            add("↺ Restaurar temporada" if ign else "🚫 Ignorar temporada",
                lambda: self._set_seasons_ignored([(node.tmdb_id, node.season)], not ign))
            menu.addSeparator()

        name = r["name"]
        tid = r["tmdb_id"]
        add("☆ Quitar de favoritos" if self.ctx.is_favorite("tv", tid) else "★ Añadir a favoritos",
            lambda: self.ctx.toggle_favorite("tv", tid, name))
        add("🔓 Quitar reserva" if self.ctx.is_reserved("tv", tid) else "🔒 Reservar (proteger del borrado)",
            lambda: self.ctx.toggle_reservation(self, "tv", tid, name,
                                                self.ctx.best_known_size_bytes("tv", tid, 0)))
        add("↺ Restaurar serie" if r.get("ignored") else "🚫 Ignorar serie",
            lambda: self._set_series_ignored(tid, not r.get("ignored")))
        src = r.get("source") or ""
        add(f"Abrir en {'Jellyfin' if src == 'jellyfin' else 'Plex'}",
            lambda: self.ctx.open_in_media_server(src, r.get("server_id")), enabled=bool(src))
        add("↻ Volver a comprobar esta serie", lambda: self.rescan_series(tid),
            enabled=not self._scanning and tid not in self._rescanning)
        add("🗑 Borrar la serie del servidor…", lambda: self.host._confirm_delete_missing_ep_series(r),
            enabled=self.host is not None)
        menu.addSeparator()
        add("🔍 Template aMule por serie…", lambda: AmuleTemplateDialog(self, self.config, name).exec())
        pats = self.config.get("series_search_patterns", {}) or {}
        cur = next((v for k, v in pats.items() if k.strip().lower() == name.strip().lower()), None)
        if cur:
            add(f"Actual: {cur[:42]}…", lambda: None, enabled=False)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _show_dub_hidden(self):
        hidden = mer.dub_hidden_rows(self._results, self._dub_cache) if self._flag("missing_ep_hide_no_dub") else []
        if not hidden:
            self.ctx.set_status("Ninguna serie queda oculta por «Ocultar sin doblaje ES»")
            return
        DubHiddenDialog(self, hidden, self._force_dub_recheck).exec()

    def _force_dub_recheck(self, tmdb_id):
        self._dub_cache.pop(str(tmdb_id), None)
        self.start_dub_check(only_tmdb_id=tmdb_id)

    # ── Escaneo ──

    def _set_scanning_ui(self, scanning: bool):
        self.scan_btn.setEnabled(not scanning)
        self.full_btn.setEnabled(not scanning)
        self.reload_btn.setEnabled(not scanning)
        self.cancel_btn.setVisible(scanning)
        self.progress.setVisible(scanning)
        if scanning:
            self.progress.setRange(0, 0)   # indeterminada hasta el primer avance

    def _update_progress(self, current: int, total: int, name: str):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
        self.status_lbl.setText(f"Comprobando ({current}/{total}): {name}")

    def cancel_scan(self):
        if self._cancel_event is not None:
            self._cancel_event.set()

    def start_scan(self, force_full: bool = False):
        if not self.config.get("jellyfin_enabled") and not self.config.get("plex_enabled"):
            self.ctx.set_status("Activa Plex o Jellyfin en Ajustes para usar el detector de huecos",
                                theme.WARNING_COLOR)
            return
        if self._scanning:
            return
        if self.host is not None and self.host._upload_running:
            # No competir con una subida en curso por las llamadas a TMDB.
            self.ctx.set_status("Espera a que termine la subida en curso antes de comprobar huecos",
                                theme.WARNING_COLOR)
            return
        self._scanning = True
        self._cancel_event = threading.Event()
        cancel = self._cancel_event
        self._set_scanning_ui(True)
        on_result = None
        if force_full:
            # La tabla vieja estaría mostrando datos obsoletos todo el rato:
            # se vacía y las series van apareciendo según se detectan.
            self._results = []
            self._live_counter = 0
            self.render()
            on_result = lambda row: ui(lambda r=row: self._append_live(r))

        def worker():
            try:
                results = self.scan._scan_missing_episodes(
                    progress_cb=lambda c, t, n: ui(lambda: self._update_progress(c, t, n)),
                    cancel_event=cancel, force_full=force_full, on_result_cb=on_result)
            except Exception:
                _log.exception("Episodios que faltan (Qt): fallo inesperado durante el escaneo")
                self.ctx.set_status("El escaneo terminó con un error -- revisa app.log", theme.ERROR_COLOR)
                ui(lambda: self._finish_scan(self._results))
                return
            ui(lambda: self._finish_scan(results))
        run_in_thread(worker)

    def _append_live(self, row: dict):
        self._results.append(row)
        self._live_counter += 1
        if self._live_counter >= _LIVE_RENDER_EVERY:
            self._live_counter = 0
            self.render()

    def _finish_scan(self, results: list):
        self._scanning = False
        self._set_scanning_ui(False)
        self._results = results
        self._refresh_cache_meta()
        self.render()
        if self.config.get("ai_fallback_enabled") and self.config.get("ai_api_key"):
            self.ask_ai_batch()

    def _refresh_cache_meta(self):
        """La caché en disco acaba de cambiar: completas y fecha del último
        escaneo se vuelven a leer."""
        from core.missing_episodes_cache import load_cache
        cache = load_cache()
        self._complete_rows = None
        self._complete_count = mer.complete_count_in_cache(cache)
        self._last_scan_ts = (cache.get("_meta") or {}).get("last_scan_ts")

    # ── Reescaneo de una sola serie ──

    def rescan_series(self, tmdb_id):
        r = self._base_row(tmdb_id)
        if r is None:
            return
        if self._scanning:
            self.ctx.set_status("Espera a que termine el escaneo en curso", theme.WARNING_COLOR)
            return
        if tmdb_id in self._rescanning:
            return
        name = r["name"]
        self._rescanning.add(tmdb_id)
        self.model.refresh_series_columns()
        self.view.viewport().update()
        self.ctx.set_status(f"Reescaneando \"{name}\"...")

        def worker():
            try:
                results, removed_reason = self.scan._rescan_single_series_worker(r)
            except Exception:
                _log.exception("Reescaneo de '%s' (tmdb_id=%s): fallo inesperado", name, tmdb_id)
                self.ctx.set_status(f"Error al reescanear \"{name}\" -- revisa app.log", theme.ERROR_COLOR)
                ui(lambda: self._finish_single_rescan(tmdb_id, name, None))
                return
            ui(lambda: self._finish_single_rescan(tmdb_id, name, results, removed_reason))
        run_in_thread(worker)

    def _finish_single_rescan(self, tmdb_id, name: str, results, removed_reason: str = None):
        """Sustituye la fila vieja por el resultado (0 o 1 filas). El tmdb_id
        nuevo puede ser distinto (releer la ficha corrige identificaciones
        equivocadas) -- se quitan ambos para no duplicar la serie."""
        self._rescanning.discard(tmdb_id)
        if results is None:
            self.view.viewport().update()
            return

        def _norm(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v
        new_ids = {_norm(row.get("tmdb_id")) for row in results} | {_norm(tmdb_id)}
        self._results = [r for r in self._results if _norm(r.get("tmdb_id")) not in new_ids]
        self._results.extend(results)
        self._refresh_cache_meta()
        if results:
            self.ctx.set_status(f"\"{name}\" actualizada", theme.SUCCESS_COLOR)
        elif removed_reason:
            self.ctx.set_status(f"\"{name}\" {removed_reason} -- se quitó de la lista", theme.WARNING_COLOR)
        elif not self._flag("missing_ep_hide_complete"):
            self.ctx.set_status(f"\"{name}\" ya no tiene huecos -- ahora figura como completa",
                                theme.SUCCESS_COLOR)
        else:
            self.ctx.set_status(f"\"{name}\" ya no tiene huecos -- se quitó de la lista", theme.SUCCESS_COLOR)
        self.render()
        # También repasa el doblaje de esta serie (si sigue con huecos).
        if results and not self._scanning:
            new_tmdb_id = results[0].get("tmdb_id") or tmdb_id
            if str(new_tmdb_id) != str(tmdb_id):
                self._dub_cache.pop(str(tmdb_id), None)
            self._dub_cache.pop(str(new_tmdb_id), None)
            self.start_dub_check(only_tmdb_id=new_tmdb_id)

    # ── Comprobación de doblaje castellano ──

    def start_dub_check(self, only_tmdb_id=None):
        if self._scanning:
            return
        known = self._dub_cache
        pending = mer.dub_pending_checks(self._results, known)
        if only_tmdb_id is not None:
            pending = [p for p in pending if str(p[0]) == str(only_tmdb_id)]
        if not pending:
            self.render()
            return
        self._scanning = True
        self._cancel_event = threading.Event()
        cancel = self._cancel_event
        self._set_scanning_ui(True)

        def worker():
            try:
                updates = self.scan._compute_dub_updates(
                    pending, known, cancel,
                    progress_cb=lambda c, t, n: ui(lambda: self._update_progress(c, t, n)))
            except Exception:
                _log.exception("Comprobación de doblaje (Qt): fallo inesperado")
                updates = {}
            ui(lambda: self._finish_dub_check(updates))
        run_in_thread(worker)

    def _finish_dub_check(self, updates: dict):
        self._scanning = False
        self._set_scanning_ui(False)
        mer.merge_dub_updates(self._dub_cache, updates)
        from core.spanish_dub_cache import save_cache
        save_cache(self._dub_cache)
        self.render()

    # ── IA ──

    def _persist_ai_verdicts(self, verdicts: dict):
        if not verdicts:
            return
        from core.missing_episodes_cache import load_cache, save_cache
        cache = dict(load_cache())
        if mer.persist_ai_verdicts(cache, verdicts):
            save_cache(cache)

    def ask_ai_batch(self):
        """Una sola llamada para todas las series con huecos (solo
        recuentos) -- nunca pregunta por el doblaje (eso solo a mano, por
        serie). Solo anota; ocultar es cosa del interruptor."""
        api_key = self.config.get("ai_api_key", "")
        if not api_key or not self._results:
            return
        payload = mer.batch_ai_payload(self._results)

        def worker():
            from core.missing_episodes_ai import analyze_missing_episodes
            verdicts = analyze_missing_episodes(payload, api_key)
            ui(lambda: self._apply_ai_verdicts(verdicts))
        run_in_thread(worker)

    def _apply_ai_verdicts(self, verdicts: dict):
        for r in self._results:
            if r["tmdb_id"] in verdicts:
                r["ai_verdict"] = verdicts[r["tmdb_id"]]
        self._persist_ai_verdicts(verdicts)
        current = self.side.current_row()
        if current is not None:
            base = self._base_row(current["tmdb_id"])
            if base is not None:
                self.side.update_ai_verdict(base)
        self.render()

    def ask_ai_current(self):
        """"🤖 Preguntar a la IA" del panel lateral: solo la serie a la vista.
        Con «Ocultar sin doblaje ES» activo pregunta también por el doblaje
        (el ÚNICO sitio donde se le pregunta eso), con el texto real de
        eldoblaje.com y los nombres reales de archivo del FTP como apoyo."""
        current = self.side.current_row()
        api_key = self.config.get("ai_api_key", "")
        if current is None or not api_key or self._ai_busy:
            return
        r = self._base_row(current["tmdb_id"]) or current
        tmdb_id = r["tmdb_id"]
        check_spanish_dub = self._flag("missing_ep_hide_no_dub")
        self._ai_busy = True
        self.side.ai_btn.setEnabled(False)
        self.side.set_ai_text("🤖 Preguntando a la IA...")
        ctx = self.ctx

        def worker():
            try:
                details = ctx.tmdb.get_tv_details(tmdb_id) or {}
            except Exception:
                details = {}
            info_doblaje = ""
            if check_spanish_dub:
                try:
                    from core.eldoblaje import search_series, get_dub_summary
                    # Nombre en español primero (eldoblaje.com indexa por el
                    # título con el que se estrenó aquí), original de respaldo.
                    candidates = search_series(r["name"]) or search_series(details.get("original_name") or "")
                    if candidates:
                        info_doblaje = get_dub_summary(candidates[0]["id"])
                except Exception:
                    info_doblaje = ""
            ftp_filenames = []
            ftp = ctx.connect_ftp()
            if ftp is not None:
                try:
                    path = ctx.existing_series_path(r, ftp=ftp)
                    if path:
                        ftp_filenames = ftp.list_files_recursive(path, max_depth=2)
                except Exception:
                    pass
                finally:
                    ftp.disconnect()
            payload = mer.single_show_ai_payload(r, details, info_doblaje, ftp_filenames)
            try:
                from core.missing_episodes_ai import analyze_missing_episodes
                verdicts = analyze_missing_episodes(payload, api_key, check_spanish_dub=check_spanish_dub)
            except Exception:
                verdicts = {}
            ui(lambda: self._apply_single_ai_verdict(r, verdicts.get(tmdb_id)))
        run_in_thread(worker)

    def _apply_single_ai_verdict(self, r: dict, verdict):
        self._ai_busy = False
        self.side.ai_btn.setEnabled(True)
        if verdict:
            r["ai_verdict"] = verdict
            self._persist_ai_verdicts({r["tmdb_id"]: verdict})
            # Compartir solo en esta consulta manual, nunca desde la de lotes.
            self.ctx.push_shared_dub_verdict(r["tmdb_id"], verdict, self._save_shared_dub_verdicts)
            self.side.update_ai_verdict(r)
        elif not r.get("ai_verdict"):
            self.side.set_ai_text("🤖 No se pudo obtener un veredicto (revisa la API Key de Groq o la conexión).")
        else:
            self.side.update_ai_verdict(r)
        self.render()

    def _save_shared_dub_verdicts(self, merged: dict):
        from core.shared_dub_verdicts import save_local_cache
        self._shared_dub_verdicts = merged
        save_local_cache(merged)

    def _apply_shared_dub_verdicts(self, merged: dict):
        """Veredictos de doblaje que otros equipos obtuvieron de la IA: se
        aplican solo los más recientes que la última sincronización."""
        previous = self._shared_dub_verdicts
        self._save_shared_dub_verdicts(merged)
        to_apply = mer.shared_verdicts_to_apply(self._results, merged, previous)
        if to_apply:
            self._persist_ai_verdicts(to_apply)
            self.render()

    # ── Llamado desde la lógica de subida (host) ──

    def remove_uploaded_episode(self, media_info):
        """Tras subir un episodio que estaba en la lista, quitarlo sin esperar
        a un escaneo (ver App._remove_uploaded_episode_from_missing_list)."""
        if media_info is None or media_info.media_type != "tv":
            return
        if media_info.season is None or media_info.episode is None:
            return
        tmdb_id, season, episode = media_info.tmdb_id, media_info.season, media_info.episode
        from core.missing_episodes import remove_missing_episode
        if not remove_missing_episode(self._results, tmdb_id, season, episode):
            return
        from core.missing_episodes_cache import load_cache, save_cache, remove_missing_episode_from_cache
        cache = load_cache()
        if remove_missing_episode_from_cache(cache, tmdb_id, season, episode):
            save_cache(cache)
            self.ctx.push_missing_episodes()
        self.render()

    def refresh_auto(self, tmdb_id):
        """El rayo de esa serie cambió (aquí o desde Archivos/la lógica)."""
        idx = self.model.series_index(tmdb_id)
        if idx.isValid():
            self.model.refresh_node(self.model.node(idx))
        self.view.viewport().update()
