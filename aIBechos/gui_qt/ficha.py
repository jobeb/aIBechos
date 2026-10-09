"""
Ficha de TMDB para paneles laterales (Protegidos, Liberar espacio...):
póster, título, datos, clasificación, reparto y sinopsis de una obra, cargados
en un hilo con testigo (si se pide otra mientras carga, la anterior se
descarta). Las líneas propias de cada pestaña van en set_extra().
"""

from __future__ import annotations

import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from core.api_client import TMDB_IMAGE
from core.status_colors import PENDING_COLOR
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui


class TmdbFicha(QFrame):
    def __init__(self, tmdb, placeholder: str = "Pulsa una fila\npara ver su ficha", parent=None):
        super().__init__(parent)
        self.tmdb = tmdb
        self.setObjectName("card")
        self.setMinimumWidth(240)
        self._token = None
        lay = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        lay.addWidget(scroll)
        self.body = QVBoxLayout(body)
        self.poster = QLabel(placeholder)
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(170, 245)
        self.poster.setStyleSheet(f"color: {PENDING_COLOR}; background: {theme.BG_ALT}; border-radius: 6px;")
        self.body.addWidget(self.poster, 0, Qt.AlignHCenter)
        self.title = self._lbl(bold=True)
        self.meta = self._lbl()
        self.cert = self._lbl(color=PENDING_COLOR)
        self.cast = self._lbl(color=PENDING_COLOR)
        self.extra = self._lbl(color=PENDING_COLOR)
        self.overview = self._lbl()
        self.slot = QVBoxLayout()     # widgets propios de la pestaña (ruta FTP, enlaces...)
        self.body.addLayout(self.slot)
        self.body.addStretch(1)

    def _lbl(self, bold=False, color=None):
        w = QLabel()
        w.setWordWrap(True)
        w.setTextInteractionFlags(Qt.TextSelectableByMouse)
        w.setStyleSheet(("font-weight: bold; font-size: 11pt;" if bold else "font-size: 9pt;")
                        + (f" color: {color};" if color else ""))
        self.body.addWidget(w)
        return w

    def set_extra(self, text: str):
        self.extra.setText(text)

    def show(self, media_type: str, tmdb_id, title: str, overview: str = ""):
        """media_type "tv"/"movie"; sin tmdb_id solo muestra el título."""
        token = object()
        self._token = token
        self.title.setText(title or "")
        self.meta.setText("📺 Serie" if media_type == "tv" else "🎬 Película")
        self.cert.setText("")
        self.cast.setText("")
        self.overview.setText(overview or "Cargando…")
        self.poster.setPixmap(QPixmap())
        self.poster.setText("…")
        if not tmdb_id:
            self.overview.setText(overview or "")
            self.poster.setText("Sin póster")
            return
        tmdb = self.tmdb

        def worker():
            out = {}
            try:
                out["details"] = (tmdb.get_tv_details(tmdb_id) if media_type == "tv"
                                  else tmdb.get_movie_details(tmdb_id)) or {}
            except Exception:
                out["details"] = {}
            if media_type == "movie":
                try:
                    out["cert"] = tmdb.get_movie_certification(tmdb_id) or ""
                except Exception:
                    out["cert"] = ""
            try:
                out["cast"] = tmdb.get_top_cast(media_type, tmdb_id, 6)
            except Exception:
                out["cast"] = []
            pp = out["details"].get("poster_path")
            if pp:
                try:
                    resp = requests.get(f"{TMDB_IMAGE}{pp}", timeout=8)
                    out["poster"] = resp.content if resp.ok else None
                except Exception:
                    out["poster"] = None
            ui(lambda: self._apply(token, media_type, title, overview, out))
        run_in_thread(worker)

    def _apply(self, token, media_type, title, overview, out):
        if token is not self._token:
            return
        d = out.get("details") or {}
        year = (d.get("first_air_date") or d.get("release_date") or "")[:4]
        vote = d.get("vote_average") or 0
        genres = [g.get("name") for g in (d.get("genres") or []) if g.get("name")]
        meta = " · ".join(x for x in ["📺 Serie" if media_type == "tv" else "🎬 Película", year,
                                      f"⭐ {vote:.1f}" if vote else ""] if x)
        if genres:
            meta += " · " + ", ".join(genres)
        self.title.setText(d.get("name") or d.get("title") or title or "")
        self.meta.setText(meta)
        if media_type == "tv":
            self.cert.setText("Serie")
        else:
            self.cert.setText(f"Clasificación: {out.get('cert')}" if out.get("cert") else "Clasificación: sin dato")
        cast = out.get("cast") or []
        self.cast.setText(("Reparto: " + ", ".join(cast)) if cast else "")
        self.overview.setText(d.get("overview") or overview or "Sin sinopsis disponible")
        raw = out.get("poster")
        pm = QPixmap()
        if raw and pm.loadFromData(raw):
            self.poster.setPixmap(pm.scaled(170, 245, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.poster.setText("")
        else:
            self.poster.setText("Sin póster")
