"""
Panel lateral de "Episodios que faltan": ficha de TMDB de la serie pulsada
(póster, datos, reparto, sinopsis), ruta de la serie en el FTP (pulsable para
ver su árbol de archivos), veredicto de la IA y enlaces personalizables a
nivel serie. Solo lectura -- equivale a _build_missing_ep_side_panel/
_show_missing_ep_poster de la versión Tk.
"""

from __future__ import annotations

import requests
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from core import missing_ep_rows as mer
from core.api_client import TMDB_IMAGE
from gui_qt import theme
from gui_qt.bridge import ui, run_in_thread

POSTER_W, POSTER_H = 180, 270
CAST_LIMIT = 6


class _PosterCache:
    """LRU mínima de pósters ya descargados (bytes), para no repetir la
    descarga al volver a pulsar la misma serie."""

    def __init__(self, size=60):
        self.size = size
        self._data = {}

    def get(self, url):
        v = self._data.pop(url, None)
        if v is not None:
            self._data[url] = v
        return v

    def put(self, url, raw):
        self._data[url] = raw
        while len(self._data) > self.size:
            self._data.pop(next(iter(self._data)))


class MissingEpSidePanel(QFrame):
    ai_requested = Signal()   # "🤖 Preguntar a la IA" (lo atiende la pestaña)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("card")
        self.setMinimumWidth(220)
        self._row = None
        self._token = None
        self._ftp_ruta = None
        self._ftp_tree_loaded_for = None
        self._posters = _PosterCache()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 6, 4, 6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        self.poster = QLabel("Pulsa una serie\npara ver su ficha")
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(POSTER_W, POSTER_H)
        self.poster.setStyleSheet(f"color: {theme.PENDING_COLOR}; background: {theme.BG_ALT}; border-radius: 6px;")
        lay.addWidget(self.poster, 0, Qt.AlignHCenter)

        def _label(size=None, bold=False, color=None):
            lbl = QLabel()
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            css = []
            if size:
                css.append(f"font-size: {size}pt;")
            if bold:
                css.append("font-weight: bold;")
            if color:
                css.append(f"color: {color};")
            lbl.setStyleSheet(" ".join(css))
            lay.addWidget(lbl)
            return lbl

        self.title = _label(12, bold=True)
        self.meta = _label(9)
        self.cast = _label(9, color=theme.PENDING_COLOR)
        self.overview = _label(9)

        self.path_lbl = QLabel()
        self.path_lbl.setWordWrap(True)
        self.path_lbl.setCursor(Qt.PointingHandCursor)
        self.path_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
        self.path_lbl.mousePressEvent = lambda ev: self._toggle_ftp_tree()
        lay.addWidget(self.path_lbl)

        self.ftp_tree = QPlainTextEdit()
        self.ftp_tree.setReadOnly(True)
        self.ftp_tree.setFixedHeight(180)
        self.ftp_tree.setStyleSheet("font-size: 8pt;")
        self.ftp_tree.hide()
        lay.addWidget(self.ftp_tree)

        # Botón por serie: pregunta a la IA solo por la serie que se ve.
        self.ai_btn = QPushButton("🤖 Preguntar a la IA")
        self.ai_btn.setEnabled(False)
        self.ai_btn.setToolTip("Preguntar a la IA por ESTA serie: sirve para distinguir un hueco de "
                               "verdad de una numeración distinta (temporadas partidas, numeración "
                               "absoluta...). Con «Ocultar sin doblaje ES» activo pregunta también por "
                               "el doblaje castellano. Consume tu cuota de Groq.")
        self.ai_btn.clicked.connect(self.ai_requested.emit)
        lay.addWidget(self.ai_btn)
        self.ai_lbl = _label(9, color=theme.WARNING_COLOR)

        self.links_box = QVBoxLayout()
        self.links_box.setSpacing(2)
        lay.addLayout(self.links_box)
        lay.addStretch(1)

    # ── Mostrar una serie ──

    def show_row(self, r: dict) -> None:
        self._row = r
        token = object()
        self._token = token
        self.title.setText(r["name"])
        self.meta.setText("")
        self.cast.setText("")
        self.overview.setText("Cargando...")
        self.poster.setPixmap(QPixmap())
        self.poster.setText("…")
        self.ai_lbl.setText(mer.ai_verdict_text(r))
        self.ai_btn.setEnabled(bool(self.ctx.config.get("ai_api_key", "")))
        self._render_links(r)
        self._ftp_ruta = None
        self._ftp_tree_loaded_for = None
        self.ftp_tree.hide()
        self._update_path_label(r, token)

        tmdb = self.ctx.tmdb

        def worker():
            try:
                details = tmdb.get_tv_details(r["tmdb_id"]) or {}
            except Exception:
                details = {}
            try:
                cast = tmdb.get_top_cast("tv", r["tmdb_id"], CAST_LIMIT)
            except Exception:
                cast = []
            poster_path = details.get("poster_path")
            poster_url = f"{TMDB_IMAGE}{poster_path}" if poster_path else None
            raw = None
            if poster_url:
                raw = self._posters.get(poster_url)
                if raw is None:
                    try:
                        resp = requests.get(poster_url, timeout=10)
                        if resp.ok:
                            raw = resp.content
                            self._posters.put(poster_url, raw)
                    except Exception:
                        raw = None
            ui(lambda: self._apply_detail(token, details, cast, raw))
        run_in_thread(worker)

    def _apply_detail(self, token, details: dict, cast: list, raw) -> None:
        if token is not self._token:
            return   # ya se pulsó otra serie
        year = (details.get("first_air_date") or "")[:4]
        vote = details.get("vote_average") or 0
        genres = [g.get("name") for g in (details.get("genres") or []) if g.get("name")]
        meta = " · ".join(x for x in ["📺 Serie", year, f"⭐ {vote:.1f}"] if x)
        if genres:
            meta += " · " + ", ".join(genres)
        self.meta.setText(meta)
        self.cast.setText(("Reparto: " + ", ".join(cast)) if cast else "Reparto: sin datos")
        self.overview.setText(details.get("overview") or "Sin sinopsis disponible")
        if raw:
            pm = QPixmap()
            if pm.loadFromData(raw):
                self.poster.setPixmap(pm.scaled(POSTER_W, POSTER_H, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))
                self.poster.setText("")
                return
        self.poster.setText("Sin póster")

    def update_ai_verdict(self, r: dict) -> None:
        if self._row is not None and r.get("tmdb_id") == self._row.get("tmdb_id"):
            self.ai_lbl.setText(mer.ai_verdict_text(r))

    def set_ai_text(self, text: str) -> None:
        self.ai_lbl.setText(text)

    def current_row(self):
        return self._row

    # ── Enlaces a nivel serie ──

    def _render_links(self, r: dict) -> None:
        while self.links_box.count():
            w = self.links_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        variables = {"serie": r["name"], "tmdb_id": r["tmdb_id"]}
        for link in self.ctx.config.get("custom_links_show", []) or []:
            template = link.get("url_template", "")
            if not template:
                continue
            bg = link.get("background", False)
            btn = QPushButton(link.get("name", "Enlace"))
            btn.setToolTip("Abre: " + template + (" (en segundo plano)" if bg else ""))
            btn.clicked.connect(lambda _=False, t=template, v=variables, b=bg:
                                self.open_link_with_ruta(t, v, r, b))
            self.links_box.addWidget(btn)

    def open_link_with_ruta(self, template: str, variables: dict, r: dict, background: bool) -> None:
        """Abre un enlace personalizable; si usa {ruta}, primero busca la
        carpeta de la serie en el FTP (con conexión propia, en un hilo)."""
        if "{ruta}" not in template:
            self.ctx.open_custom_link(template, variables, background)
            return
        self.ctx.set_status("Buscando la carpeta de la serie en el FTP...")

        def worker():
            ruta = self._find_series_path(r)
            if not ruta:
                self.ctx.set_status("No se encontró la carpeta de la serie en el FTP", theme.WARNING_COLOR)
            else:
                self.ctx.set_status("Carpeta encontrada", theme.SUCCESS_COLOR)
            ui(lambda: self.ctx.open_custom_link(template, dict(variables, ruta=ruta), background))
        run_in_thread(worker)

    # ── Ruta y árbol en el FTP ──

    def _find_series_path(self, r: dict) -> str:
        """Ruta de la carpeta de la serie en el FTP ("" si no se encuentra)
        -- caché primero; si no, conexión propia (llamar desde un hilo)."""
        ruta = self.ctx.existing_series_path(r, use_cache_only=True)
        if ruta:
            return ruta
        ftp = self.ctx.connect_ftp()
        if ftp is None:
            return ""
        try:
            return self.ctx.existing_series_path(r, ftp=ftp)
        finally:
            ftp.disconnect()

    def _update_path_label(self, r: dict, token) -> None:
        self.path_lbl.setText("📁 Buscando la carpeta en el FTP...")
        self.path_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")

        def worker():
            ruta = self._find_series_path(r)
            ui(lambda: self._apply_path(token, ruta))
        run_in_thread(worker)

    def _apply_path(self, token, ruta: str) -> None:
        if token is not self._token:
            return
        self._ftp_ruta = ruta or None
        if ruta:
            self.path_lbl.setText(f"> 📁 {ruta}")
            self.path_lbl.setToolTip("Pulsa para ver el árbol de archivos de la carpeta")
        else:
            self.path_lbl.setText("📁 No se encontró la carpeta en el FTP")
            self.path_lbl.setStyleSheet(f"color: {theme.WARNING_COLOR}; font-size: 9pt;")

    def _toggle_ftp_tree(self) -> None:
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
            ui(lambda: self._apply_tree(token, ruta, tree))
        run_in_thread(worker)

    def _apply_tree(self, token, ruta, tree) -> None:
        if token is not self._token or ruta != self._ftp_ruta:
            return
        if tree is None:
            self.ftp_tree.setPlainText("No se pudo listar la carpeta en el FTP.")
            return
        from core.ftp_client import format_ftp_tree
        self.ftp_tree.setPlainText(format_ftp_tree(ruta, tree) or "Carpeta vacía.")
        self._ftp_tree_loaded_for = ruta
