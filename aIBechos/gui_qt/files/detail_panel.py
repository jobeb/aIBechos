"""
Panel lateral de Archivos: buscador manual (con selector de proveedor) y
ficha del archivo seleccionado o del resultado que se está previsualizando.

Equivale a _build_detail_panel/_manual_search/_preview_result/
_assign_selected_result/_update_detail de la versión Tk; la lógica (búsqueda
por proveedor, construir MediaInfo, nombre nuevo, asignar a la selección...)
es la compartida de core/app_files_core.py, a través del host.
"""

from __future__ import annotations

import threading

import requests
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from core.status_colors import ERROR_COLOR, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui

PROVIDER_LABELS = {
    "tmdb": "TMDB", "openlibrary": "OpenLibrary", "google_books": "GoogleBooks",
    "comicvine": "ComicVine", "mangadex": "MangaDex", "anilist": "AniList", "kitsu": "Kitsu",
}
SEARCH_DEBOUNCE_MS = 500
SEARCH_MIN_CHARS = 2
SEARCH_RESULTS_CAP = 30
POSTER_W, POSTER_H = 180, 260
CAST_LIMIT = 6


def _is_openlibrary_work(info) -> bool:
    return info.media_type == "libro" and isinstance(info.tmdb_id, str) and info.tmdb_id.startswith("/works/")


class _ResultsList(QListWidget):
    """Lista de resultados; Enter = asignar."""
    activatedRow = Signal(int)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentRow() >= 0:
            self.activatedRow.emit(self.currentRow())
            return
        super().keyPressEvent(ev)


class FilesDetailPanel(QFrame):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.ctx = host.ctx
        self.setObjectName("card")
        self.setMinimumWidth(260)
        self._results: list = []
        self._previewed = -1
        self._token = None
        self._ftp_ruta = None
        self._ftp_tree_loaded_for = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(lambda: self.search(use_ai_fallback=False))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self.search_label = QLabel("Buscar en TMDB:")
        self.lang_hint = QLabel("🇬🇧 busca en inglés")
        self.lang_hint.setToolTip("Este proveedor no tiene títulos en español fiables: busca por el título original/inglés.")
        self.lang_hint.setStyleSheet(f"color: {theme.WARNING_COLOR}; font-size: 8pt;")
        self.lang_hint.hide()
        top.addWidget(self.search_label)
        top.addWidget(self.lang_hint)
        top.addStretch(1)
        lay.addLayout(top)

        self.provider = QComboBox()
        for key, label in PROVIDER_LABELS.items():
            self.provider.addItem(label, key)
        self.provider.currentIndexChanged.connect(self._on_provider_changed)
        lay.addWidget(self.provider)

        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Título a buscar…")
        self.query.setClearButtonEnabled(True)
        self.query.textEdited.connect(self._on_query_edited)
        self.query.returnPressed.connect(lambda: self.search(use_ai_fallback=True))
        self.query.installEventFilter(self)
        search_btn = QPushButton("Buscar")
        search_btn.clicked.connect(lambda: self.search(use_ai_fallback=True))
        row.addWidget(self.query, 1)
        row.addWidget(search_btn)
        lay.addLayout(row)

        self.results = _ResultsList()
        self.results.setMaximumHeight(150)
        self.results.currentRowChanged.connect(self.preview)
        self.results.activatedRow.connect(lambda _r: self.assign())
        self.results.itemDoubleClicked.connect(lambda _i: self.assign())
        lay.addWidget(self.results)

        btns = QHBoxLayout()
        self.assign_btn = QPushButton("Asignar")
        self.assign_btn.setProperty("accent", True)
        self.assign_btn.setToolTip("Aplicar el resultado elegido al archivo (o a toda la selección)")
        self.assign_btn.clicked.connect(lambda: self.assign())
        self.assign_up_btn = QPushButton("Asignar y subir")
        self.assign_up_btn.clicked.connect(lambda: self.assign(also_upload=True))
        btns.addWidget(self.assign_btn)
        btns.addWidget(self.assign_up_btn)
        lay.addLayout(btns)
        self.ai_comic_btn = QPushButton("🪄 IA: título original")
        self.ai_comic_btn.setToolTip("Traducir con IA el título del cómic a su título original y buscarlo en ComicVine")
        self.ai_comic_btn.clicked.connect(self.ai_translate_comic)
        self.ai_comic_btn.hide()
        lay.addWidget(self.ai_comic_btn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        b = QVBoxLayout(body)
        b.setSpacing(5)

        self.poster = QLabel("—")
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(POSTER_W, POSTER_H)
        self.poster.setStyleSheet(f"color: {theme.PENDING_COLOR}; background: {theme.BG_ALT}; border-radius: 6px;")
        b.addWidget(self.poster, 0, Qt.AlignHCenter)

        def lbl(size=9, bold=False, color=None):
            w = QLabel()
            w.setWordWrap(True)
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            css = [f"font-size: {size}pt;"]
            if bold:
                css.append("font-weight: bold;")
            if color:
                css.append(f"color: {color};")
            w.setStyleSheet(" ".join(css))
            b.addWidget(w)
            return w

        self.title = lbl(12, bold=True)
        self.episode = lbl(10)
        self.year = lbl(9, color=theme.PENDING_COLOR)
        self.confidence = lbl(9)
        self.meta = lbl(9)
        self.cert = lbl(9, color=theme.PENDING_COLOR)
        self.cast = lbl(9, color=theme.PENDING_COLOR)
        self.overview = lbl(9)
        self.error = lbl(9, color=theme.ERROR_COLOR)
        self.path_lbl = QLabel()
        self.path_lbl.setWordWrap(True)
        self.path_lbl.setCursor(Qt.PointingHandCursor)
        self.path_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
        self.path_lbl.mousePressEvent = lambda ev: self._toggle_ftp_tree()
        b.addWidget(self.path_lbl)
        self.ftp_tree = QPlainTextEdit()
        self.ftp_tree.setReadOnly(True)
        self.ftp_tree.setFixedHeight(170)
        self.ftp_tree.setStyleSheet("font-size: 8pt;")
        self.ftp_tree.hide()
        b.addWidget(self.ftp_tree)
        self.links_box = QVBoxLayout()
        b.addLayout(self.links_box)
        b.addStretch(1)

    # ── Teclado: flechas en el buscador recorren los resultados ──

    def eventFilter(self, obj, ev):
        if obj is self.query and ev.type() == ev.Type.KeyPress and ev.key() in (Qt.Key_Up, Qt.Key_Down):
            n = self.results.count()
            if n:
                cur = max(0, self.results.currentRow())
                cur = max(0, min(n - 1, cur + (1 if ev.key() == Qt.Key_Down else -1)))
                self.results.setCurrentRow(cur)
                self.ctx.set_status(f"{cur + 1} de {n} -- flechas para más, \"Asignar\" para elegir este")
            return True
        return super().eventFilter(obj, ev)

    # ── Proveedor ──

    def _on_provider_changed(self, _i):
        self.host._search_provider_override = self.provider.currentData()
        self._update_provider_hint()
        if self.host._selected_entry is not None and self.query.text().strip():
            self.search()

    def _update_provider_hint(self):
        provider = self.host._effective_search_provider(self.host._selected_entry)
        self.search_label.setText(f"Buscar en {PROVIDER_LABELS.get(provider, 'TMDB')}:")
        self.lang_hint.setVisible(provider in ("comicvine", "anilist", "kitsu"))
        self.ai_comic_btn.setVisible(provider == "comicvine")

    def reset_search(self, entry=None):
        """Limpia el buscador al cambiar de archivo (ver _reset_search_panel)."""
        self._debounce.stop()
        self.query.clear()
        self.results.clear()
        self._results = []
        self._previewed = -1
        self.host._results_kind = "tmdb"
        if self.host._search_provider_override == "auto":
            self.host._search_provider_override = "tmdb"
        idx = self.provider.findData(self.host._search_provider_override)
        if idx >= 0 and idx != self.provider.currentIndex():
            self.provider.blockSignals(True)
            self.provider.setCurrentIndex(idx)
            self.provider.blockSignals(False)
        self._update_provider_hint()

    # ── Búsqueda ──

    def _on_query_edited(self, _text):
        self._debounce.start()

    def search(self, use_ai_fallback: bool = False):
        self._debounce.stop()
        entry = self.host._selected_entry
        query = self.query.text().strip()
        if not query and entry is not None:
            query = entry.detected.get("title", "")
            if query:
                self.query.setText(query)
        if len(query) < SEARCH_MIN_CHARS or entry is None:
            return
        self.ctx.set_status("Buscando...", WARNING_COLOR)
        host = self.host
        override = host._search_provider_override

        def worker():
            shown = query
            try:
                kind, results = host._provider_search(query, entry, override)
                if not results and use_ai_fallback and kind == "tmdb":
                    fallback = host._try_ai_fallback(entry, host.tmdb)
                    if fallback:
                        results, shown, _det = fallback
                        ui(lambda q=shown: self.query.setText(q))
            except Exception as e:
                msg = str(e)
                self.ctx.set_status(f"Error: {msg}", ERROR_COLOR)
                return
            ui(lambda: self._apply_results(shown, kind, results))
        run_in_thread(worker)

    def _apply_results(self, query: str, kind: str, results: list):
        if self.query.text().strip() != query:
            return   # el usuario ya escribió otra cosa mientras tanto: respuesta obsoleta
        self.host._results_kind = kind
        self._results = (results or [])[:SEARCH_RESULTS_CAP]
        self.results.blockSignals(True)
        self.results.clear()
        for r in self._results:
            self.results.addItem(self.host._label_for_result(r))
        self.results.blockSignals(False)
        if self._results:
            self.results.setCurrentRow(0)
            self.ctx.set_status(f"{len(self._results)} resultado(s) -- flechas arriba/abajo para elegir uno",
                                SUCCESS_COLOR)
        else:
            self.ctx.set_status("Sin resultados", WARNING_COLOR)

    def ai_translate_comic(self):
        entry = self.host._selected_entry
        cfg = self.host.config_data
        if not entry or not entry.is_comic:
            self.ctx.set_status("Selecciona un cómic para traducir su título", WARNING_COLOR)
            return
        if not cfg.get("ai_fallback_enabled"):
            self.ctx.set_status('Activa "Usar IA como último recurso" en Ajustes', WARNING_COLOR)
            return
        api_key = cfg.get("ai_api_key", "")
        if not api_key:
            self.ctx.set_status("Configura tu API Key de Groq en Ajustes", WARNING_COLOR)
            return
        local_title = self.query.text().strip() or entry.detected.get("title", "")
        if not local_title:
            self.ctx.set_status("No hay título que traducir", WARNING_COLOR)
            return
        self.ctx.set_status("Traduciendo título con IA...", WARNING_COLOR)
        host = self.host

        def worker():
            from core.ai_title_fallback import guess_original_comic_title_via_ai
            translated = guess_original_comic_title_via_ai(local_title, api_key)
            if not translated:
                self.ctx.set_status("La IA no pudo determinar el título original", ERROR_COLOR)
                return
            try:
                results = host.comicvine.search_volumes(translated)
            except Exception as e:
                self.ctx.set_status(f"Error de ComicVine: {e}", ERROR_COLOR)
                return
            ui(lambda t=translated: self.query.setText(t))
            if not results:
                self.ctx.set_status(f'IA tradujo a "{translated}" pero ComicVine sigue sin resultados',
                                    WARNING_COLOR)
                return
            from core.learned_comic_titles import add_comic_title_translation
            add_comic_title_translation(local_title, translated)
            ui(lambda: self._apply_results(translated, "comic", results))
            self.ctx.set_status(f'IA tradujo a "{translated}"', SUCCESS_COLOR)
        run_in_thread(worker)

    # ── Previsualizar y asignar ──

    def preview(self, idx: int):
        if idx < 0 or idx >= len(self._results):
            return
        self._previewed = idx
        result = self._results[idx]
        entry = self.host._selected_entry
        det = entry.detected if entry else {}
        info = self.host._build_info_from_result(result, det)
        self._show_info(info, confidence=0, vote=float(result.get("vote_average") or 0))
        self.error.setText("")

    def assign(self, also_upload: bool = False):
        host = self.host
        entry = host._selected_entry
        if not entry:
            return
        idx = self.results.currentRow() if self.results.currentRow() >= 0 else self._previewed
        if idx < 0 or idx >= len(self._results):
            self.ctx.set_status("Busca y elige un resultado primero", WARNING_COLOR)
            return
        result = self._results[idx]
        targets = host._current_selection()
        if len(targets) > 1:
            self.ctx.set_status(f"Asignando a {len(targets)} archivo(s)...", WARNING_COLOR)
            threading.Thread(target=host._assign_to_selection_worker,
                             args=(result, targets, also_upload), daemon=True).start()
            return
        info = host._build_info_from_result(result, entry.detected)
        entry.media_info = info
        entry.new_name = host._build_name(info, entry.ext)
        entry.status = "listo"
        entry.error_msg = ""
        entry._stale_checked = False
        host._mark_auto_processed(entry.path, "identificado_manual", entry.new_name)
        host._update_row(entry)
        self.show_entry(entry)
        self.ctx.set_status(f"Asignado: {info.title}", SUCCESS_COLOR)
        if also_upload:
            host._upload_one(entry)

    def update_assign_labels(self, n: int):
        self.assign_btn.setText(f"Asignar a la selección ({n})" if n > 1 else "Asignar")
        self.assign_up_btn.setText(f"Asignar y subir ({n})" if n > 1 else "Asignar y subir")

    # ── Ficha ──

    def clear(self):
        self._token = None
        for w in (self.title, self.episode, self.year, self.confidence, self.meta, self.cert,
                  self.cast, self.overview, self.error, self.path_lbl):
            w.setText("")
        self.poster.setPixmap(QPixmap())
        self.poster.setText("—")
        self.ftp_tree.hide()
        self._ftp_ruta = None
        self._clear_links()

    def show_entry(self, entry):
        """Ficha de un archivo (ver App._update_detail)."""
        info = entry.media_info
        reason = entry.error_msg if entry.status in ("error", "omitido") and entry.error_msg else ""
        prefix = "⚠ Error: " if entry.status == "error" else "⏭ Omitido: "
        if not info:
            self.clear()
            self.title.setText(entry.detected.get("title", entry.name))
            self.episode.setText("Sin informacion de TMDB")
            self.poster.setText("Sin carátula")
            self.error.setText((prefix + reason) if reason else "")
            return
        self._show_info(info, confidence=getattr(entry, "confidence", 0), vote=0)
        self.error.setText((prefix + reason) if reason else "")

    def _show_info(self, info, confidence: int, vote: float):
        token = object()
        self._token = token
        self.title.setText(info.title or "")
        if info.season and info.episode:
            ep_text = f"S{info.season:02d}E{info.episode:02d} - {info.episode_title}"
        elif info.media_type == "libro" and info.episode:
            ep_text = f"#{info.episode:02d}"
        else:
            ep_text = ""
        self.episode.setText(ep_text)
        self.year.setText(info.year or "")
        if confidence > 0:
            color = SUCCESS_COLOR if confidence >= 85 else WARNING_COLOR if confidence >= 65 else ERROR_COLOR
            self.confidence.setText(f"Confianza: {confidence}%")
            self.confidence.setStyleSheet(f"font-size: 9pt; color: {color};")
        else:
            self.confidence.setText("")
        self.meta.setText(self.host._detail_meta_for(info, vote))
        if info.media_type == "tv":
            self.cert.setText("Serie")
        elif info.media_type == "movie":
            self.cert.setText("Clasificación: …")
        else:
            self.cert.setText("")
        self.cast.setText("" if info.media_type == "libro" or not info.tmdb_id else "Reparto: …")
        is_ol = _is_openlibrary_work(info)
        self.overview.setText(info.overview or ("Cargando sinopsis…" if is_ol else ""))
        self.poster.setPixmap(QPixmap())
        self.poster.setText("Cargando carátula…" if info.poster_url else "Sin carátula")
        self._render_links(info)
        self._ftp_ruta = None
        self._ftp_tree_loaded_for = None
        self.ftp_tree.hide()
        self.path_lbl.setText("")
        self._load_extras(info, token, is_ol)

    def _load_extras(self, info, token, is_ol: bool):
        host = self.host

        def worker():
            out = {}
            if info.poster_url:
                try:
                    resp = requests.get(info.poster_url, timeout=8)
                    out["poster"] = resp.content if resp.ok else None
                except Exception:
                    out["poster"] = None
            if info.media_type == "movie" and info.tmdb_id:
                try:
                    out["cert"] = host.tmdb.get_movie_certification(info.tmdb_id) or ""
                except Exception:
                    out["cert"] = ""
            if info.media_type in ("tv", "movie") and info.tmdb_id:
                try:
                    out["cast"] = host.tmdb.get_top_cast(info.media_type, info.tmdb_id, CAST_LIMIT)
                except Exception:
                    out["cast"] = []
            if is_ol:
                try:
                    out["ol_desc"] = host.openlibrary_client.get_work_description(info.tmdb_id) or ""
                except Exception:
                    out["ol_desc"] = ""
            if not (info.overview or "").strip() and not is_ol:
                try:
                    from core.ai_synopsis import ai_key_if_enabled, es_overview
                    ai_key = ai_key_if_enabled(host.config_data)
                    tid = int(info.tmdb_id) if str(getattr(info, "tmdb_id", "")).isdigit() else 0
                    if ai_key and tid and info.media_type in ("tv", "movie"):
                        out["ai_es"] = es_overview(info.media_type, tid,
                                                   host.config_data.get("tmdb_api_key", ""), ai_key)
                except Exception:
                    pass
            ui(lambda: self._apply_extras(token, info, out))
        run_in_thread(worker)

        # Ruta en el FTP: caché primero, si no conexión propia.
        def path_worker():
            ruta = ""
            try:
                ruta = host._existing_ftp_path_for_info(host.ftp, info, use_cache_only=True,
                                                         known_year=info.year or None)
            except Exception:
                ruta = ""
            if not ruta and host.config_data.get("ftp_host", ""):
                ftp = host.ctx.connect_ftp()
                if ftp is not None:
                    try:
                        ruta = host._existing_ftp_path_for_info(ftp, info, use_cache_only=False,
                                                                 known_year=info.year or None)
                    except Exception:
                        ruta = ""
                    finally:
                        ftp.disconnect()
            ui(lambda: self._apply_path(token, ruta))
        if info.media_type in ("tv", "movie", "libro"):
            self.path_lbl.setText("📁 Buscando la carpeta en el FTP...")
            run_in_thread(path_worker)

    def _apply_extras(self, token, info, out: dict):
        if token is not self._token:
            return
        raw = out.get("poster")
        if raw:
            pm = QPixmap()
            if pm.loadFromData(raw):
                self.poster.setPixmap(pm.scaled(POSTER_W, POSTER_H, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.poster.setText("")
            else:
                self.poster.setText("Sin carátula")
        elif info.poster_url:
            self.poster.setText("Sin carátula")
        if "cert" in out:
            self.cert.setText(f"Clasificación: {out['cert']}" if out["cert"] else "Clasificación: sin dato")
        if "cast" in out:
            names = out["cast"] or []
            self.cast.setText(("Reparto: " + ", ".join(names)) if names else "Reparto: sin datos")
        if "ol_desc" in out:
            p, d = info.overview or "", out["ol_desc"]
            if d:
                self.overview.setText(f"{p}\n\n{d}".strip() if p else d)
            elif not p:
                self.overview.setText("Sin sinopsis disponible")
        elif "ai_es" in out and out["ai_es"] and not (info.overview or "").strip():
            self.overview.setText(out["ai_es"])

    def _apply_path(self, token, ruta: str):
        if token is not self._token:
            return
        self._ftp_ruta = ruta or None
        if ruta:
            self.path_lbl.setText(f"> 📁 {ruta}")
            self.path_lbl.setToolTip("Pulsa para ver el árbol de archivos de la carpeta")
        else:
            self.path_lbl.setText("📁 Todavía no hay carpeta en el FTP")

    def _toggle_ftp_tree(self):
        ruta = self._ftp_ruta
        if not ruta:
            return
        if self.ftp_tree.isVisible():
            self.ftp_tree.hide()
            self.path_lbl.setText(f"> 📁 {ruta}")
            return
        self.ftp_tree.show()
        self.path_lbl.setText(f"v 📁 {ruta}")
        if self._ftp_tree_loaded_for == ruta:
            return
        self.ftp_tree.setPlainText("Listando el árbol de archivos...")
        token = self._token

        def worker():
            ftp = self.ctx.connect_ftp()
            tree = None
            if ftp is not None:
                try:
                    tree = ftp.get_folder_tree(ruta)
                except Exception:
                    tree = None
                finally:
                    ftp.disconnect()

            def apply():
                if token is not self._token or ruta != self._ftp_ruta:
                    return
                if tree is None:
                    self.ftp_tree.setPlainText("No se pudo listar la carpeta en el FTP.")
                    return
                from core.ftp_client import format_ftp_tree
                self.ftp_tree.setPlainText(format_ftp_tree(ruta, tree) or "Carpeta vacía.")
                self._ftp_tree_loaded_for = ruta
            ui(apply)
        run_in_thread(worker)

    # ── Enlaces personalizables ──

    def _clear_links(self):
        while self.links_box.count():
            w = self.links_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _render_links(self, info):
        self._clear_links()
        key = "custom_links_movie" if info.media_type == "movie" else "custom_links_episode"
        entry = self.host._selected_entry
        variables = {"serie": info.title, "tmdb_id": info.tmdb_id, "temporada": info.season,
                     "episodio": info.episode, "titulo": info.episode_title,
                     "nombre_archivo": entry.name if entry else ""}
        for link in self.host.config_data.get(key, []) or []:
            template = link.get("url_template", "")
            if not template:
                continue
            bg = link.get("background", False)
            btn = QPushButton(link.get("name", "Enlace"))
            btn.setToolTip("Abre: " + template + (" (en segundo plano)" if bg else ""))
            btn.clicked.connect(lambda _=False, t=template, v=variables, b=bg: self._open_link(t, v, info, b))
            self.links_box.addWidget(btn)

    def _open_link(self, template, variables, info, background):
        if "{ruta}" not in template:
            self.ctx.open_custom_link(template, variables, background)
            return
        if self._ftp_ruta:
            self.ctx.open_custom_link(template, dict(variables, ruta=self._ftp_ruta), background)
            return
        self.ctx.set_status("Buscando la carpeta en el FTP...", PENDING_COLOR)
        host = self.host

        def worker():
            ruta = ""
            ftp = host.ctx.connect_ftp()
            if ftp is not None:
                try:
                    ruta = host._existing_ftp_path_for_info(ftp, info, use_cache_only=False,
                                                             known_year=info.year or None)
                except Exception:
                    ruta = ""
                finally:
                    ftp.disconnect()
            ui(lambda: self.ctx.open_custom_link(
                template, dict(variables, ruta=ruta) if ruta else variables, background))
        run_in_thread(worker)
