"""
Pestaña "📊 Estadísticas" en Qt (antes gui/stats_view.py): tamaño del
servidor por disco y categoría, rankings (subidas, por categoría, rachas,
borrados, adelgazamientos), tu contribución, subidas de los últimos 30 días y
actividad reciente.

Solo lee los mirrors que el host mantiene sincronizados (core/app_history_core,
app_shared_sync) -- nunca abre conexiones por su cuenta. Las barras y el
gráfico se dibujan con QPainter (un widget por panel, no uno por barra).
"""

from __future__ import annotations

import datetime
import time as _time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from core.category_stats import bytes_by_disk
from core.deletion_stats import top_deleters
from core.fmt import fmt_size
from core.milestones import milestone_for, next_milestone
from core.status_colors import ACCENT, PENDING_COLOR, SUCCESS_COLOR, WARNING_COLOR
from core.streaks import compute_streaks, top_streaks
from core.upload_stats import top_uploaders
from gui_qt import theme

PALETTE = ("#3B82F6", "#F59E0B", "#8B5CF6", "#14B8A6", "#EC4899", "#84CC16", "#F97316", "#06B6D4")
TIMELINE_DAYS = 30


def _ago(ts: float) -> str:
    s = max(0, _time.time() - ts)
    if s < 60:
        return "justo ahora"
    if s < 3600:
        return f"hace {int(s // 60)} min"
    if s < 86400:
        return f"hace {int(s // 3600)} h"
    return f"hace {int(s // 86400)} d"


class _Panel(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 12)
        t = QLabel(title)
        t.setStyleSheet("font-size: 11pt; font-weight: bold;")
        self.lay.addWidget(t)
        self.body = QVBoxLayout()
        self.lay.addLayout(self.body)

    def clear(self):
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def note(self, text, color=PENDING_COLOR):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {color};")
        self.body.addWidget(lbl)
        return lbl


class _Bars(QWidget):
    """Ranking: puesto · nombre · barra · valor, todo pintado."""

    ROW_H = 26

    def __init__(self, rows, accent):
        super().__init__()
        self.rows = rows          # [(puesto, nombre, fracción, texto_valor)]
        self.accent = accent
        self.setMinimumHeight(self.ROW_H * len(rows) + 4)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        name_w, val_w, rank_w = 200, 100, 34
        bar_x, bar_w = rank_w + name_w + 8, max(40, w - (rank_w + name_w + val_w + 24))
        bold = QFont(self.font())
        bold.setBold(True)
        for i, (rank, name, frac, val) in enumerate(self.rows):
            y = i * self.ROW_H
            first = i == 0
            p.setFont(bold if first else self.font())
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(0, y, rank_w, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft, rank)
            p.setPen(QColor(self.accent if first else theme.TEXT))
            p.drawText(QRectF(rank_w, y, name_w, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, name_w - 4))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BORDER))
            p.drawRoundedRect(QRectF(bar_x, y + 9, bar_w, 8), 4, 4)
            p.setBrush(QColor(self.accent if first else "#5b6b7f"))
            p.drawRoundedRect(QRectF(bar_x, y + 9, max(4.0, bar_w * frac), 8), 4, 4)
            p.setPen(QColor(self.accent if first else PENDING_COLOR))
            p.drawText(QRectF(w - val_w, y, val_w, self.ROW_H), Qt.AlignVCenter | Qt.AlignRight, val)
        p.end()


class _DiskBar(QWidget):
    """Barra apilada de un disco: un segmento por categoría + libre."""

    def __init__(self, segments, total):
        super().__init__()
        self.segments = segments   # [(color, bytes)]
        self.total = total
        self.setFixedHeight(26)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_ALT))
        p.drawRoundedRect(r, 4, 4)
        x = 0.0
        if self.total > 0:
            for color, b in self.segments:
                wseg = r.width() * b / self.total
                p.setBrush(QColor(color))
                p.drawRect(QRectF(x, 0, wseg, r.height()))
                x += wseg
        p.end()


class _Timeline(QWidget):
    def __init__(self, days, values):
        super().__init__()
        self.days, self.values = days, values
        self.setFixedHeight(170)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.days)
        h_chart = self.height() - 22
        mx = max(self.values) or 1
        col_w = self.width() / n
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        for i, v in enumerate(self.values):
            if v > 0:
                frac = max(v / mx, 0.03)
                bh = h_chart * frac
                p.drawRect(QRectF(i * col_w + 1, h_chart - bh, col_w - 2, bh))
        small = QFont(self.font())
        small.setPointSize(8)
        p.setFont(small)
        p.setPen(QColor(PENDING_COLOR))
        p.drawText(QRectF(2, 0, 120, 14), Qt.AlignLeft, fmt_size(mx))
        p.drawText(QRectF(2, h_chart - 14, 40, 14), Qt.AlignLeft, "0")
        for i, d in enumerate(self.days):
            if i % 5 == 0 or i == n - 1:
                p.drawText(QRectF(i * col_w, h_chart + 4, col_w * 2, 16), Qt.AlignLeft, str(d.day))
        p.end()


class StatsTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        t = QLabel("Estadísticas")
        t.setStyleSheet("font-size: 13pt; font-weight: bold;")
        root.addWidget(t)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.col = QVBoxLayout(body)
        self.col.setSpacing(12)
        self.p_size = _Panel("💾 Tamaño del servidor")
        self.p_top = _Panel("🏆 Top 10 subidores")
        self.cat_holder = QVBoxLayout()
        self.p_streaks = _Panel("🔥 Racha de subidas")
        self.p_deleters = _Panel("🧹 Top borradores")
        self.p_slimmers = _Panel("🪶 Top adelgazadores")
        self.p_contrib = _Panel("⭐ Tu contribución")
        self.p_timeline = _Panel("📈 Subidas en los últimos 30 días")
        self.p_recent = _Panel("🕓 Últimas subidas")
        self.p_recent_del = _Panel("🗑 Últimos borrados")
        self.col.addWidget(self.p_size)
        self.col.addWidget(self.p_top)
        self.col.addLayout(self.cat_holder)
        for p in (self.p_streaks, self.p_deleters, self.p_slimmers, self.p_contrib, self.p_timeline,
                  self.p_recent, self.p_recent_del):
            self.col.addWidget(p)
        self.col.addStretch(1)
        self._cat_panels = []
        host._stats_view = self

    # La lógica (app_shared_sync/app_history_core) llama a esto tras sincronizar
    def refresh_from_cache(self):
        self._size()
        self._leaderboards()
        self._contribution()
        self._timeline()
        self._recent()

    def on_shown(self):
        h = self.host
        h._stats_visible = True
        h._sync_activity_history_from_ftp()
        h._sync_upload_stats_from_ftp()
        h._sync_category_stats_from_ftp()
        h._sync_deletion_stats_from_ftp()
        h._sync_slim_stats_from_ftp()
        h._sync_category_upload_stats_from_ftp()
        self.refresh_from_cache()

    def on_hidden(self):
        self.host._stats_visible = False

    # ── Paneles ──

    def _size(self):
        p = self.p_size
        p.clear()
        h = self.host
        by_disk = bytes_by_disk(h._shared_category_stats,
                                h.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}))
        free_by_disk = getattr(h, "_shared_free_space_by_disk", {}) or {}
        disks = sorted(set(by_disk) | set(free_by_disk))
        if not disks:
            p.note("Sin datos todavía.")
            return
        totals = {}
        for cats in by_disk.values():
            for c in cats:
                if c["bytes"] > 0:
                    t = totals.setdefault(c["name"], {"count": 0, "bytes": 0})
                    t["count"] += c["count"]
                    t["bytes"] += c["bytes"]
        names = sorted(totals)
        color_of = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}
        legend = " &nbsp; ".join(f"<span style='color:{color_of[n]}'>■</span> {n} ({totals[n]['count']}, "
                                 f"{fmt_size(totals[n]['bytes'])})" for n in names)
        lg = QLabel(legend + f" &nbsp; <span style='color:{PENDING_COLOR}'>■</span> Libre")
        lg.setTextFormat(Qt.RichText)
        lg.setWordWrap(True)
        p.body.addWidget(lg)
        for disk in disks:
            cats = sorted((c for c in by_disk.get(disk, []) if c["bytes"] > 0), key=lambda c: c["bytes"], reverse=True)
            free = free_by_disk.get(disk)
            used = sum(c["bytes"] for c in cats)
            total = used + (free or 0)
            p.body.addWidget(QLabel(f"{disk.capitalize()}  --  Usado: {fmt_size(used)} · Libre: "
                                    + (fmt_size(free) if free is not None else "--")))
            segs = [(color_of.get(c["name"], PENDING_COLOR), c["bytes"]) for c in cats]
            if free:
                segs.append((PENDING_COLOR, free))
            p.body.addWidget(_DiskBar(segs, total))

    def _bars(self, panel, top, accent, value_key="total_bytes", fmt=fmt_size, suffix=None):
        mx = max(e.get(value_key, 0) for e in top) or 1
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        rows = []
        for i, e in enumerate(top):
            name = e.get("display_name", "?")
            if suffix:
                extra = suffix(e)
                if extra:
                    name = f"{name} {extra}"
            rows.append((medals.get(i, f"{i + 1}."), name, e.get(value_key, 0) / mx, fmt(e.get(value_key, 0))))
        panel.body.addWidget(_Bars(rows, accent))

    @staticmethod
    def _badge(e):
        b = milestone_for(e.get("total_bytes", 0))
        return b[0] if b else ""

    def _leaderboards(self):
        h = self.host
        p = self.p_top
        p.clear()
        top = top_uploaders(h._shared_upload_stats, limit=10)
        if top:
            self._bars(p, top, ACCENT, suffix=self._badge)
        else:
            p.note("Nadie ha subido nada todavía.")
        for old in self._cat_panels:
            old.deleteLater()
        self._cat_panels = []
        for _cid, cat in sorted((h._shared_category_upload_stats or {}).items(),
                                key=lambda kv: kv[1].get("category_name", "")):
            ctop = top_uploaders(cat.get("uploaders", {}), limit=10)
            if not ctop:
                continue
            panel = _Panel(f"🏆 Top subidores -- {cat.get('category_name', '?')}")
            self._bars(panel, ctop, ACCENT, suffix=self._badge)
            self.cat_holder.addWidget(panel)
            self._cat_panels.append(panel)
        p = self.p_streaks
        p.clear()
        st = top_streaks(compute_streaks(h._shared_activity_history), limit=10)
        if st:
            self._bars(p, st, ACCENT, value_key="streak_days",
                       fmt=lambda d: f"🔥 {d} día{'s' if d != 1 else ''}")
        else:
            p.note("Nadie tiene una racha activa ahora mismo.")
        p = self.p_deleters
        p.clear()
        dt = top_deleters(h._shared_deletion_stats, limit=10)
        if dt:
            self._bars(p, dt, WARNING_COLOR)
        else:
            p.note("Nadie ha borrado nada todavía.")
        p = self.p_slimmers
        p.clear()
        from core.slim_stats import top_slimmers
        sl = top_slimmers(h._shared_slim_stats, limit=10)
        if sl:
            self._bars(p, sl, SUCCESS_COLOR)
        else:
            p.note("Nadie ha adelgazado nada todavía.")

    def _contribution(self):
        h = self.host
        p = self.p_contrib
        p.clear()
        person = (h.config_data.get("app_user_name", "") or "").strip()
        if not person:
            p.note("Configura \"Tu nombre\" en Ajustes → Conexión FTP para aparecer aquí.")
            return
        ranked = top_uploaders(h._shared_upload_stats, limit=len(h._shared_upload_stats) or 1)
        from core.upload_stats import _normalize_key
        norm = _normalize_key(person)
        idx = next((i for i, e in enumerate(ranked) if _normalize_key(e.get("display_name", "")) == norm), None)
        if idx is None:
            p.note(f"{person} todavía no ha subido nada -- ¡sé el primero en aparecer en el ranking!")
            return
        me = ranked[idx]
        mine = fmt_size(me.get("total_bytes", 0))
        if idx == 0:
            text = f"{person} va Nº1 con {mine} subidos -- ¡el que más aporta al servidor!"
        else:
            prev = ranked[idx - 1]
            delta = prev.get("total_bytes", 0) - me.get("total_bytes", 0)
            text = (f"{person} va Nº{idx + 1} con {mine} subidos -- te faltan {fmt_size(delta)} para "
                    f"adelantar a {prev.get('display_name', '?')}.")
        nxt = next_milestone(me.get("total_bytes", 0))
        text += (f" Próximo hito: {fmt_size(nxt[0])} más para {nxt[1]}." if nxt
                 else " ¡Ya tienes todas las insignias! 💎")
        p.note(text, theme.TEXT)

    def _timeline(self):
        p = self.p_timeline
        p.clear()
        entries = [e for e in self.host._shared_activity_history
                   if e.get("kind") == "subida" and e.get("status", "ok") == "ok"]
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=i) for i in range(TIMELINE_DAYS - 1, -1, -1)]
        by_day = dict.fromkeys(days, 0)
        for e in entries:
            d = datetime.datetime.fromtimestamp(e.get("ts", 0)).date()
            if d in by_day:
                by_day[d] += e.get("size", 0)
        if not any(by_day.values()):
            p.note("Sin subidas registradas en los últimos 30 días.")
            return
        p.body.addWidget(_Timeline(days, [by_day[d] for d in days]))

    def _recent(self):
        for panel, kind, verb, key, empty in ((self.p_recent, "subida", "subió", "filename", "Sin actividad reciente."),
                                              (self.p_recent_del, "borrado", "borró", "name", "Sin borrados recientes.")):
            panel.clear()
            entries = sorted((e for e in self.host._shared_activity_history
                              if e.get("kind") == kind and e.get("status", "ok") == "ok"),
                             key=lambda e: e.get("ts", 0), reverse=True)[:8]
            if not entries:
                panel.note(empty)
                continue
            for e in entries:
                panel.note(f"{e.get('person', '?')} {verb} {e.get(key, '?')} ({fmt_size(e.get('size', 0))}) "
                           f"{_ago(e.get('ts', 0))}", theme.TEXT)
