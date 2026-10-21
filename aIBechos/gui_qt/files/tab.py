"""
Pestaña "Archivos" en Qt: añadir archivos (botones o arrastrar y soltar),
identificarlos, asignar resultados (a uno o a toda la selección) y subirlos.

Toda la lógica es la de core/app_files_core.py, compartida con la versión Tk
a través del host (gui_qt/core_host.py); esta clase es la vista y además
implementa los ganchos de interfaz que el host le reenvía (refresh_table,
update_row, update_detail, prompt_same_series...).
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QSplitter, QTableView, QVBoxLayout,
                               QWidget)

from core.file_entry import entry_matches
from core.fmt import fmt_size, fmt_speed
from core.renamer import is_archive_file, is_book_file, is_video_file
from core.status_colors import SUCCESS_COLOR, WARNING_COLOR
from gui_qt import theme
from gui_qt.ui_state import remember_splitter
from gui_qt.columns import fill_columns
from gui_qt.actions import ActionsDelegate
from gui_qt.dialogs import AmuleTemplateDialog
from gui_qt.files.detail_panel import FilesDetailPanel
from gui_qt.files.dialogs import ClearDialog, EditDetectedDialog, EditRemoteDirDialog, SameSeriesDialog
from gui_qt.files.model import (COL_ACTIONS, COL_BAR, COL_DEST, COL_DET, COL_FAV, COL_LOCK, COL_NAME,
                                COL_NN, COL_SIZE, COL_SPD, COL_STAT, SORT_KEYS, FilesModel,
                                ProgressDelegate)

_MEDIA_FILTER = ("Vídeo, libros y comprimidos (*.mkv *.mp4 *.avi *.mov *.m4v *.wmv *.flv *.ts *.m2ts *.webm "
                 "*.pdf *.epub *.mobi *.azw3 *.cbz *.cbr *.zip *.7z *.rar *.tar *.tgz *.tbz2 *.txz);;"
                 "Vídeo (*.mkv *.mp4 *.avi *.mov *.m4v *.wmv *.flv *.ts *.m2ts *.webm);;"
                 "Libros/Cómics (*.pdf *.epub *.mobi *.azw3 *.cbz *.cbr);;"
                 "Archivos comprimidos (*.zip *.7z *.rar *.tar *.tgz *.tbz2 *.txz);;Todos (*.*)")


def _is_media(p: str) -> bool:
    return is_video_file(p) or is_book_file(p) or is_archive_file(p)


class _FilesTable(QTableView):
    """Tabla que acepta archivos/carpetas soltados encima."""

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        if ev.mimeData().hasUrls():
            self._on_drop([u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()])
            ev.acceptProposedAction()
        else:
            super().dropEvent(ev)


class FilesTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self._syncing_selection = False
        self._build_ui()
        self._setup_shortcuts()
        host.view = self
        self.refresh_table()
        self.update_assign_button_label()

    # ── Construcción ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        add_files = QPushButton("+ Archivos")
        add_files.setProperty("accent", True)
        add_files.setToolTip("Añadir archivos sueltos a la lista (Ctrl+O). Los .zip, .7z, .rar y .tar "
                             "se descomprimen solos, incluidos los anidados.")
        add_files.clicked.connect(self.add_files)
        add_folder = QPushButton("+ Carpeta")
        add_folder.setToolTip("Añadir una carpeta entera, con sus subcarpetas (Ctrl+Mayús+O). Los comprimidos que "
                              "encuentre se descomprimen solos. Si detecta 2 o más libros/cómics pregunta "
                              "si son de la misma colección, para identificarlos de una sola búsqueda.")
        add_folder.clicked.connect(self.add_folder)
        self.select_all_btn = QPushButton("Seleccionar todos")
        self.select_all_btn.setToolTip("Selecciona todos los archivos de la lista (los que deja ver el filtro), "
                                       "para asignarles el mismo resultado de búsqueda de una vez "
                                       "('Asignar a la selección'). Ctrl+A.")
        self.select_all_btn.clicked.connect(self.toggle_select_all)
        for b in (add_files, add_folder, self.select_all_btn):
            head.addWidget(b)
        head.addStretch(1)
        title = QLabel("Archivos")
        title.setStyleSheet("font-size: 13pt; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        self.clear_btn = QPushButton("Limpiar")
        self.clear_btn.setToolTip("Vaciar la lista. Los archivos siguen en tu disco y en el historial: "
                                  "solo desaparecen de esta tabla.")
        self.clear_btn.clicked.connect(self.on_clear_clicked)
        self.upload_btn = QPushButton("Subir todo")
        self.upload_btn.setProperty("accent", True)
        self.upload_btn.setToolTip("Subir al servidor todos los archivos de la lista, cada uno a la carpeta "
                                   "de su columna \"Destino\". Ctrl+U sube solo lo seleccionado.")
        self.upload_btn.clicked.connect(lambda: self.host._on_header_upload_clicked())
        head.addWidget(self.clear_btn)
        head.addWidget(self.upload_btn)
        root.addLayout(head)

        # Filtro: solo cambia qué filas se ven (no toca la lista ni el orden).
        filt = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar por nombre, serie o estado…  (Ctrl+F)")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setToolTip("Enseña solo los archivos que contienen todas estas palabras en el "
                                    "nombre, el nombre nuevo, lo detectado, el título o el estado. "
                                    "Esc lo borra.")
        self.filter_edit.textChanged.connect(lambda _t: self.apply_filter())
        self.filter_status = QComboBox()
        for label, key in (("Todos los estados", "todos"), ("Pendientes", "pendientes"),
                           ("Subiendo o en cola", "en_curso"), ("Subidos", "subidos"),
                           ("Con error", "errores"), ("Omitidos", "omitidos")):
            self.filter_status.addItem(label, key)
        self.filter_status.currentIndexChanged.connect(lambda _i: self.apply_filter())
        filt.addWidget(self.filter_edit, 1)
        filt.addWidget(self.filter_status)
        root.addLayout(filt)

        self.model = FilesModel(self.host, self)
        self.table = _FilesTable(self.add_paths)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.clicked.connect(self._on_clicked)
        self.actions_delegate = ActionsDelegate(self.table)
        self.actions_delegate.actionTriggered.connect(self._on_action)
        self.table.setItemDelegateForColumn(COL_ACTIONS, self.actions_delegate)
        self.table.setItemDelegateForColumn(COL_BAR, ProgressDelegate(self.table))
        hdr = self.table.horizontalHeader()
        # Nombre original y Nuevo nombre se reparten el sitio sobrante; el
        # resto tiene ancho fijo de partida. Todas arrastrables.
        fill_columns(self.table, COL_NAME, COL_NN, key="files")
        for col, w in ((COL_DET, 140), (COL_DEST, 150), (COL_STAT, 78), (COL_BAR, 90),
                       (COL_SPD, 64), (COL_SIZE, 64), (COL_FAV, 28), (COL_LOCK, 28), (COL_ACTIONS, 100)):
            hdr.resizeSection(col, w)
        hdr.setMinimumSectionSize(24)
        hdr.setSectionsClickable(True)
        hdr.setSortIndicatorShown(bool(self.host._files_sort_key))
        hdr.sectionClicked.connect(self._on_header_click)
        self._update_sort_indicator()
        self.table.selectionModel().selectionChanged.connect(self._on_view_selection_changed)

        self.empty_lbl = QLabel("Arrastra archivos aquí\no usa + Archivos")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 13pt;")

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.empty_lbl, 1)

        self.detail = FilesDetailPanel(self.host)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([1000, 290])
        remember_splitter(splitter, "files")
        root.addWidget(splitter, 1)

        self.summary_lbl = QLabel()
        self.summary_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR};")
        root.addWidget(self.summary_lbl)

    # ── Ganchos que pide la lógica (vía host) ──

    def refresh_table(self):
        self.model.reset()
        empty = not self.host.files
        self.table.setVisible(not empty)
        self.empty_lbl.setVisible(empty)
        self.apply_filter(update_status=False)
        self._sync_view_selection_from_host()
        self.update_status_bar()

    # ── Filtro ──

    def filter_active(self) -> bool:
        return bool(self.filter_edit.text().strip()) or self.filter_status.currentData() != "todos"

    def _row_visible(self, entry) -> bool:
        return entry_matches(entry, self.filter_edit.text(), self.filter_status.currentData() or "todos")

    def apply_filter(self, update_status: bool = True):
        for row in range(self.model.rowCount()):
            entry = self.model.entry_at(row)
            self.table.setRowHidden(row, entry is not None and not self._row_visible(entry))
        if update_status:
            self.update_status_bar()

    def visible_entries(self) -> list:
        return [e for r in range(self.model.rowCount())
                if not self.table.isRowHidden(r) and (e := self.model.entry_at(r)) is not None]

    # ── Atajos de teclado ──

    def _setup_shortcuts(self):
        def key(seq, fn, widget=None):
            sc = QShortcut(QKeySequence(seq), widget or self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
            return sc

        key("Ctrl+O", self.add_files)
        key("Ctrl+Shift+O", self.add_folder)
        key(QKeySequence.Find, self._focus_filter)
        key("Ctrl+B", self._focus_search)
        key("Ctrl+U", self._upload_selection)
        key(QKeySequence.SelectAll, self._select_all, self.table)
        key(QKeySequence.Delete, self._remove_selection, self.table)
        key("Esc", lambda: self.filter_edit.clear(), self.filter_edit)

    def _focus_filter(self):
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    def _focus_search(self):
        q = getattr(self.detail, "query", None)
        if q is not None:
            q.setFocus()
            q.selectAll()

    def _select_all(self):
        if len(self.host._current_selection()) != len(self.visible_entries()):
            self.toggle_select_all()

    def _upload_selection(self):
        sel = self.host._current_selection()
        if len(sel) > 1:
            self.host._upload_selected_ftp()
        elif sel:
            self.host._upload_one(sel[0])

    def _remove_selection(self):
        sel = self.host._current_selection()
        if len(sel) == 1:
            self.host._remove_entry(sel[0])
        elif sel:
            # _remove_entry pregunta por cada archivo (borrar del disco o del
            # servidor): con varios serían varios diálogos seguidos.
            self.host._set_status("Supr quita los archivos de uno en uno: selecciona solo uno.",
                                  theme.WARNING_COLOR)

    def update_row(self, entry):
        if not self.model.update_entry(entry):
            # Entrada nueva (p.ej. añadida por el vigilante): la tabla entera.
            if entry in self.host.files:
                self.refresh_table()
                return
        elif self.filter_active():
            # Con el filtro puesto, un cambio de estado puede meterla o sacarla.
            row = self.model.row_of(entry)
            if row >= 0:
                self.table.setRowHidden(row, not self._row_visible(entry))
        if entry is self.host._selected_entry:
            self.detail.show_entry(entry)
        self.update_status_bar()

    def update_progress(self, entry):
        self.model.update_progress(entry)
        self.update_status_bar()

    def update_detail(self, entry):
        if entry is not None:
            self.detail.show_entry(entry)

    def clear_detail(self):
        self.detail.clear()
        self._sync_view_selection_from_host()
        self.update_assign_button_label()

    def reset_search_panel(self, entry=None):
        self.detail.reset_search(entry)

    def update_assign_button_label(self):
        n = len(self.host._current_selection())
        self.detail.update_assign_labels(n)
        all_sel = bool(self.host.files) and n == len(self.host.files)
        self.select_all_btn.setText("Deseleccionar todos" if all_sel else "Seleccionar todos")
        self.upload_btn.setText(f"Subir seleccionados ({n})" if n > 1 else "Subir todo")
        self.clear_btn.setText(f"Limpiar seleccionados ({n})" if n > 1 else "Limpiar")

    def update_status_bar(self):
        files = self.host.files
        n = len(files)
        if n == 0:
            self.summary_lbl.setText("Sin archivos en la lista")
            return
        total = 0
        counts = {}
        for e in files:
            counts[e.status] = counts.get(e.status, 0) + 1
            if e._last_known_size_bytes is None:
                try:
                    e._last_known_size_bytes = Path(e.path).stat().st_size
                except OSError:
                    pass
            if e._last_known_size_bytes is not None:
                total += e._last_known_size_bytes
        labels = {"pendiente": "pendientes", "buscando": "buscando", "listo": "listos",
                  "renombrado": "renombrados", "en_cola": "en cola para subir", "subiendo": "subiendo",
                  "subido": "subidos", "error": "con error", "auto": "en automático", "omitido": "omitidos"}
        parts = [f"{n} archivo{'s' if n != 1 else ''}", fmt_size(total)]
        if self.filter_active():
            parts.insert(0, f"Filtro: se ven {len(self.visible_entries())} de {n}")
        breakdown = " · ".join(f"{c} {labels.get(st, st)}" for st, c in counts.items() if c)
        if breakdown:
            parts.append(breakdown)
        speed = sum(e.ftp_speed for e in files if e.status == "subiendo")
        if speed > 0:
            parts.append(f"Subiendo: {fmt_speed(speed)}")
        sel = self.host._selected_entry
        if sel is not None:
            remote = self.model._dest(sel)
            parts.append(f"Seleccionado: {sel.name}" + (f"  →  {remote}" if remote else ""))
        self.summary_lbl.setText("   ·   ".join(parts))

    def prompt_same_series(self, book_comic_entries, video_entries):
        n = len(book_comic_entries)
        same = SameSeriesDialog.ask(self.window(), n).result
        if not same:
            self.host.after(50, self.host._search_new_entries, book_comic_entries + video_entries)
            return
        if video_entries:
            self.host.after(50, self.host._search_new_entries, video_entries)
        anchor = book_comic_entries[0]
        self.host._selected_entry = anchor
        self.host._multi_selected = set(book_comic_entries) - {anchor}
        self.detail.reset_search(anchor)
        self.detail.show_entry(anchor)
        self.refresh_table()
        self.update_assign_button_label()
        self.detail.query.setFocus()
        self.ctx.set_status(f"{n} archivos añadidos como misma serie -- busca una vez y pulsa Asignar",
                            SUCCESS_COLOR)

    # ── Selección: la vista manda; el host guarda ancla + resto ──

    def _on_view_selection_changed(self, *_):
        if self._syncing_selection:
            return
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        entries = [self.model.entry_at(r) for r in rows if self.model.entry_at(r) is not None]
        cur = self.model.entry_at(self.table.currentIndex().row())
        prev = self.host._selected_entry
        if not entries:
            self.host._selected_entry = None
            self.host._multi_selected = set()
            self.detail.clear()
        else:
            anchor = cur if cur in entries else entries[0]
            self.host._selected_entry = anchor
            self.host._multi_selected = set(entries) - {anchor}
            if anchor is not prev:
                self.detail.reset_search(anchor)
                self.detail.show_entry(anchor)
        self.update_assign_button_label()
        self.update_status_bar()

    def _sync_view_selection_from_host(self):
        """Refleja en la vista la selección del host (p.ej. tras "misma
        serie" o "Seleccionar todos")."""
        self._syncing_selection = True
        try:
            sm = self.table.selectionModel()
            sm.clearSelection()
            wanted = self.host._current_selection()
            sel = QItemSelection()
            for e in wanted:
                r = self.model.row_of(e)
                if r >= 0:
                    sel.select(self.model.index(r, 0), self.model.index(r, self.model.columnCount() - 1))
            sm.select(sel, QItemSelectionModel.Select)
            anchor = self.host._selected_entry
            r = self.model.row_of(anchor) if anchor is not None else -1
            if r >= 0:
                sm.setCurrentIndex(self.model.index(r, COL_NAME), QItemSelectionModel.NoUpdate)
        finally:
            self._syncing_selection = False

    def toggle_select_all(self):
        files = self.visible_entries()
        if not files:
            return
        if len(self.host._current_selection()) == len(files):
            self.host._clear_detail()
        else:
            anchor = self.host._selected_entry if self.host._selected_entry in files else files[0]
            self.host._selected_entry = anchor
            self.host._multi_selected = set(files) - {anchor}
            self.detail.reset_search(anchor)
            self.detail.show_entry(anchor)
            self._sync_view_selection_from_host()
        self.update_assign_button_label()

    # ── Añadir archivos ──

    def add_files(self):
        start = self.host.config_data.get("last_dir") or os.path.expanduser("~")
        paths, _f = QFileDialog.getOpenFileNames(self, "Seleccionar archivos de vídeo, libro o comprimido",
                                                 start, _MEDIA_FILTER)
        if paths:
            self.host.config_data["last_dir"] = str(Path(paths[0]).parent)
            self.host._add_paths_extracting_archives([str(Path(p)) for p in paths])

    def add_folder(self):
        start = self.host.config_data.get("last_dir") or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", start)
        if folder:
            self.host.config_data["last_dir"] = str(Path(folder))
            files = [str(f) for f in Path(folder).rglob("*") if f.is_file() and _is_media(str(f))]
            self.host._add_paths_extracting_archives(files, same_series_prompt=True)

    def add_paths(self, paths: list):
        """Arrastrar y soltar: archivos y carpetas (recursivo)."""
        files = [str(Path(p)) for p in paths if os.path.isfile(p) and _is_media(p)]
        for folder in (p for p in paths if os.path.isdir(p)):
            files += [str(f) for f in Path(folder).rglob("*") if f.is_file() and _is_media(str(f))]
        if files:
            self.host._add_paths_extracting_archives(files)
        elif paths:
            self.ctx.set_status("No se encontraron archivos de vídeo, libro o comprimidos en lo que soltaste",
                                WARNING_COLOR)

    # ── Limpiar ──

    def on_clear_clicked(self):
        host = self.host
        sel = host._current_selection()
        if len(sel) > 1:
            self._clear_selected(sel)
        else:
            self._clear_all()

    def _ask_clear(self, pendientes: int):
        return ClearDialog.ask(self.window(), pendientes).result

    def _clear_all(self):
        host = self.host
        if host._upload_running or host.watcher_running():
            pendientes = [e for e in host.files if e.status != "subido"]
            if pendientes:
                res = self._ask_clear(len(pendientes))
                if res == "solo_subidos":
                    host.files = [e for e in host.files if e.status != "subido"]
                elif res == "todo":
                    if host._upload_running:
                        host._upload_cancel.set()
                    host.files.clear()
                else:
                    return
                host._clear_detail()
                self.refresh_table()
                return
        host.files.clear()
        host._clear_detail()
        self.refresh_table()

    def _clear_selected(self, sel: list):
        host = self.host
        sel_ids = {id(e) for e in sel}
        pendientes = [e for e in sel if e.status != "subido"]
        if (host._upload_running or host.watcher_running()) and pendientes:
            res = self._ask_clear(len(pendientes))
            if res == "solo_subidos":
                sel_ids = {id(e) for e in sel if e.status == "subido"}
            elif res == "todo":
                if host._upload_running:
                    host._upload_cancel.set()
            else:
                return
        host.files = [e for e in host.files if id(e) not in sel_ids]
        host._multi_selected = {e for e in host._multi_selected if id(e) not in sel_ids}
        if host._selected_entry is not None and id(host._selected_entry) in sel_ids:
            host._selected_entry = None
        self.refresh_table()
        if host._selected_entry is None:
            host._clear_detail()
        self.update_assign_button_label()

    # ── Orden ──

    def _on_header_click(self, col: int):
        key = SORT_KEYS.get(col)
        if key is None:
            self._update_sort_indicator()
            return
        host = self.host
        if key == host._files_sort_key:
            host._files_sort_asc = not host._files_sort_asc
        else:
            host._files_sort_key, host._files_sort_asc = key, True
        all_saved = dict(host.config_data.get("table_sort", {}) or {})
        all_saved["archivos"] = {"key": host._files_sort_key, "asc": host._files_sort_asc}
        host.config_data.set("table_sort", all_saved)
        host.config_data.save()
        host._sort_files_by_current_key()
        self._update_sort_indicator()
        self.refresh_table()

    def _update_sort_indicator(self):
        hdr = self.table.horizontalHeader()
        col = next((c for c, k in SORT_KEYS.items() if k == self.host._files_sort_key), None)
        hdr.setSortIndicatorShown(col is not None)
        if col is not None:
            hdr.setSortIndicator(col, Qt.AscendingOrder if self.host._files_sort_asc else Qt.DescendingOrder)

    # ── Clics y acciones de fila ──

    def _entry(self, index: QModelIndex):
        return self.model.entry_at(index.row()) if index.isValid() else None

    def _on_clicked(self, index: QModelIndex):
        entry = self._entry(index)
        if entry is None:
            return
        if index.column() == COL_FAV and entry.media_info:
            self.host._toggle_entry_favorite(entry)
            self.model.update_entry(entry)
        elif index.column() == COL_LOCK and entry.media_info and entry.status == "subido":
            self.host._toggle_entry_reservation(entry)
            self.model.update_entry(entry)

    def _on_double_click(self, index: QModelIndex):
        entry = self._entry(index)
        if entry is None:
            return
        if index.column() == COL_DET:
            self.edit_detected(entry)
        elif index.column() == COL_DEST:
            self.edit_remote_dir(entry)

    def _on_action(self, index: QModelIndex, action_id: str):
        entry = self._entry(index)
        if entry is None:
            return
        if action_id == "upload":
            self.host._upload_one(entry)
        elif action_id == "skip":
            self.host._queue_skip_entry(entry)
        elif action_id == "play":
            self.host._play_file(entry)
        elif action_id == "remove":
            self.host._remove_entry(entry)

    def edit_detected(self, entry):
        if EditDetectedDialog(self.window(), entry).exec():
            self.host._update_row(entry)
            if self.host._selected_entry is entry:
                self.detail.reset_search(entry)

    def edit_remote_dir(self, entry):
        auto_dir = self.host._preview_remote_path(entry).rsplit("/", 1)[0]
        if EditRemoteDirDialog(self.window(), entry, auto_dir).exec():
            self.host._update_row(entry)

    def _copy(self, text: str):
        try:
            cb = QGuiApplication.clipboard()
            if cb is None:
                return
            cb.setText(text)
        except Exception:
            return
        self.ctx.set_status(f"Copiado: {text}", SUCCESS_COLOR)

    def _context_menu(self, pos):
        index = self.table.indexAt(pos)
        entry = self._entry(index)
        if entry is None:
            return
        if entry not in self.host._current_selection():
            self.table.selectRow(index.row())
        host = self.host
        files = host.files
        try:
            idx = files.index(entry)
        except ValueError:
            return
        n = len(files)
        menu = QMenu(self)

        def add(text, fn, enabled=True):
            a = QAction(text, menu)
            a.setEnabled(enabled)
            a.triggered.connect(fn)
            menu.addAction(a)

        sel = host._current_selection()
        if len(sel) > 1:
            add(f"▲  Subir la selección ({len(sel)})", host._upload_selected_ftp)
            add(f"🔍 Buscar de nuevo ({len(sel)})", lambda: host._search_new_entries(
                [e for e in sel if e.status != "subido"]))
            menu.addSeparator()
        if entry.status != "subido":
            add(f"🔍 Buscar de nuevo en {host._search_source_name(entry)}",
                lambda: host._search_new_entries([entry]))
        if entry.is_book and entry.status != "subido":
            if entry.is_comic:
                add("📖 Tratar como libro de texto (Google Books)",
                    lambda: host._set_book_comic_type(entry, is_comic=False))
            else:
                add("📕 Tratar como cómic/manga (ComicVine)",
                    lambda: host._set_book_comic_type(entry, is_comic=True))
        if entry.media_info and entry.status not in ("subiendo", "en_cola"):
            add("▲  Subir este archivo", lambda: host._upload_one(entry))
        add("✎  Editar título/episodio detectado…", lambda: self.edit_detected(entry))
        add("✎  Editar carpeta de destino…", lambda: self.edit_remote_dir(entry))
        series = (entry.media_info.title if entry.media_info and getattr(entry.media_info, "title", "")
                  else (entry.detected or {}).get("title", "") or entry.name)
        auto_tid = host._entry_series_tmdb_id(entry)
        if auto_tid is not None:
            on = host._is_missing_ep_auto_enabled(auto_tid)
            add("⚡ Quitar autocompletado de la serie" if on else "⚡ Autocompletar esta serie",
                lambda: (host._toggle_missing_ep_auto_complete(auto_tid),
                         host._refresh_missing_ep_auto_button(auto_tid)))
        if series.strip():
            add("🔍 Template aMule por serie…",
                lambda _checked=False, s=series: AmuleTemplateDialog(self.window(), host.config_data,
                                                                     s).exec())
        menu.addSeparator()
        add("▶  Reproducir en Jellyfin" if entry.status == "subido" else "▶  Reproducir",
            lambda: host._play_file(entry))
        add("📂 Abrir carpeta contenedora", lambda: host._open_containing_folder(entry))
        add("📋 Copiar nombre original", lambda: self._copy(entry.name))
        if entry.new_name:
            add("📋 Copiar nuevo nombre", lambda: self._copy(entry.new_name))
        menu.addSeparator()
        if idx > 0:
            add("▲  Mover arriba", lambda: host._move_entry(entry, -1))
            add("⏫ Ir al principio", lambda: host._move_entry_to(entry, 0))
        if idx < n - 1:
            add("▼  Mover abajo", lambda: host._move_entry(entry, 1))
            add("⏬ Ir al final", lambda: host._move_entry_to(entry, n - 1))
        if entry.status in ("subido", "renombrado", "error"):
            menu.addSeparator()
            add("↺  Restablecer (volver a pendiente)", lambda: host._reset_entry(entry))
        menu.addSeparator()
        add("✕  Quitar de la lista", lambda: host._remove_entry(entry))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ── Favoritos/reservas cambiados desde otro sitio ──

    def refresh_marks(self):
        self.model.refresh_marks()
