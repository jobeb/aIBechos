"""Diálogo "Ver todo" de una fila de Recomendado: parrilla completa con las
mismas cards (mismo delegate) y "Cargar más" página a página, como
openSectionList en solicitudes-web/app.js."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
                               QListView, QPushButton, QVBoxLayout)

from core.recommended_rows import apply_filters, filter_available, normalize_item
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui
from gui_qt.movies.cards import CARD_H, CARD_W, CardDelegate
from gui_qt.movies.tab import RowModel

SEE_ALL_MAX_PAGES = 25


class SeeAllDialog(QDialog):
    def __init__(self, parent, host, kind: str, rowdef: dict, filters: dict, watch_only: bool,
                 get_state, on_action, on_select):
        super().__init__(parent)
        self.host = host
        self.kind = kind
        self.rowdef = rowdef
        self.filters = filters
        self.watch_only = watch_only and not rowdef.get("no_avail")
        self._alive = True
        self._loading = False
        self._page = 0
        self._pages = 1
        self._seen = set()
        self.setWindowTitle(rowdef.get("title", "Ver todo"))
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        self.status = QLabel("Cargando…")
        self.status.setStyleSheet(f"color: {theme.PENDING_COLOR};")
        lay.addWidget(self.status)
        self.model = RowModel(self)
        self.model.set_skeleton(10)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.IconMode)
        self.view.setFlow(QListView.LeftToRight)
        self.view.setWrapping(True)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setSpacing(8)
        self.view.setUniformItemSizes(True)
        self.view.setGridSize(QSize(CARD_W + 10, CARD_H + 10))
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.delegate = CardDelegate(self.view)
        self.delegate.posters = parent.delegate.posters
        self.delegate.get_state = get_state
        self.delegate.cardAction.connect(on_action)
        self.delegate.cardSelected.connect(lambda item: (on_select(item), self.accept()))
        self.delegate.attach(self.view)
        self.view.setItemDelegate(self.delegate)
        lay.addWidget(self.view, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.more = QPushButton("Cargar más")
        self.more.clicked.connect(self._load_next)
        bottom.addWidget(self.more)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self.finished.connect(lambda _r: setattr(self, "_alive", False))
        self._load_next()

    def _load_next(self):
        if self._loading or not self._alive:
            return
        if self._page >= min(self._pages, SEE_ALL_MAX_PAGES):
            self.more.hide()
            return
        self._loading = True
        self.more.setEnabled(False)
        self.more.setText("Cargando…")
        token_page = self._page + 1
        client, kind, rowdef = self.host.tmdb, self.kind, self.rowdef
        filters = dict(self.filters)

        def worker():
            try:
                fetched, pages = client.list_endpoint(rowdef["path"], kind, page=token_page,
                                                      params=rowdef.get("params") or {})
                items = []
                for r in fetched:
                    key = (r.get("media_type") or kind, r.get("id"))
                    if r.get("id") is None or key in self._seen:
                        continue
                    self._seen.add(key)
                    items.append(normalize_item(r, kind))
                items = apply_filters(items, **filters)
                if self.watch_only:
                    items = filter_available(client, kind, items)
            except Exception as e:
                ui(lambda m=str(e): self._page_error(token_page, m))
                return
            ui(lambda: self._page_ready(token_page, pages, items))
        run_in_thread(worker)

    def _page_ready(self, page, pages, items):
        if not self._alive:
            return
        self._loading = False
        self._page, self._pages = page, pages
        if page == 1:
            self.model.set_items(items)
        else:
            self.model.set_items([i for i in self.model.items if i] + items)
        n = len([i for i in self.model.items if i])
        self.status.setText(f"{n} título(s)")
        if page >= min(pages, SEE_ALL_MAX_PAGES) or not items:
            self.more.hide()
        else:
            self.more.setEnabled(True)
            self.more.setText("Cargar más")

    def _page_error(self, page, msg):
        if not self._alive:
            return
        self._loading = False
        if page == 1:
            self.model.set_items([])
            self.status.setText(f"No se pudo cargar ({msg[:100]})")
        self.more.setEnabled(True)
        self.more.setText("Reintentar")
