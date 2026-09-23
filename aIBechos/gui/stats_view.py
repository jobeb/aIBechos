"""
Pestaña "📊 Estadísticas" -- construida aparte de gui/app.py, mismo motivo
que gui/table_view.py: sacar una pieza de UI sustancial del archivo
principal en vez de seguir engordándolo.

Todos los paneles leen mirrors ya sincronizados en memoria por App -- esta
vista nunca abre su propia conexión FTP:
- El ranking de subidas (core/upload_stats.py) y el historial de actividad
  compartido se sincronizan solos al entrar en la pestaña (ver
  App._sync_upload_stats_from_ftp/_sync_activity_history_from_ftp).
- El recuento/tamaño por categoría (core/category_stats.py) se mantiene al
  día de forma incremental -- cada subida/borrado OK suma o resta su
  carpeta (ver App._push_category_stat_addition_to_ftp/
  _push_category_stat_removal_to_ftp) en vez de recorrer el árbol del FTP
  en cada visita; el árbol completo solo se recorre una vez, la primera
  vez que se usa esta función en el servidor (ver
  App._sync_category_stats_from_ftp/_bootstrap_category_stats).
- El espacio libre lo mantiene al día App._refresh_ftp_space() (arranque,
  cada 5 min, tras subir/borrar) -- esta vista solo lee
  app._shared_free_space_by_disk.
"""

import datetime
import time as _time

import customtkinter as ctk

from core.category_stats import bytes_by_disk
from core.deletion_stats import top_deleters
from core.milestones import milestone_for, next_milestone
from core.streaks import compute_streaks, top_streaks
from core.upload_stats import top_uploaders

# Colores fijos por categoría en la barra de tamaño por disco -- se
# asignan por orden alfabético del nombre en cada repintado (ver
# _refresh_size_by_disk), así que una categoría siempre sale con el mismo
# color mientras no cambie el conjunto de categorías con datos. El espacio
# libre usa PENDING_COLOR (gris neutro, ver gui/app.py), no esta paleta.
_CATEGORY_PALETTE = ("#3B82F6", "#F59E0B", "#8B5CF6", "#14B8A6", "#EC4899", "#84CC16", "#F97316", "#06B6D4")


def _fmt_ago(ts: float) -> str:
    seconds = max(0, _time.time() - ts)
    if seconds < 60:
        return "justo ahora"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    return f"hace {int(seconds // 86400)} d"


class StatsView:
    def __init__(self, app, parent):
        self.app = app
        self._build(parent)

    # ------------------------------------------------------------- UI --

    def _build(self, parent):
        from gui.app import CONTAINER_GAP

        parent.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(parent, fg_color=("gray90", "gray20"), corner_radius=8)
        header.grid(row=0, column=0, sticky="ew", pady=(0, CONTAINER_GAP))
        ctk.CTkLabel(header, text="Estadísticas",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=12, pady=8)

        body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")

        self._build_size_panel(body)
        self._build_leaderboard_panel(body)
        self._build_category_leaderboards_panel(body)
        self._build_streaks_panel(body)
        self._build_deleters_panel(body)
        self._build_contribution_panel(body)
        self._build_timeline_panel(body)
        self._build_recent_panel(body)
        self._build_recent_deletions_panel(body)

    def _panel_frame(self, parent, title):
        frame = ctk.CTkFrame(parent, fg_color=("gray95", "gray17"), corner_radius=8)
        frame.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        return frame

    def _build_size_panel(self, parent):
        """Una sola barra apilada por disco -- un segmento de color por
        categoría más un segmento gris para el espacio libre, con una
        leyenda compartida encima (ver _refresh_size_by_disk)."""
        self._size_frame = self._panel_frame(parent, "💾 Tamaño del servidor")
        # height=1: CTkFrame por defecto pide 200px de alto (ver
        # ctk_frame.py) -- con pack_propagate normal eso no se nota
        # porque el contenido real de sobra lo estira, PERO estos dos
        # frames pueden quedarse sin ningún hijo (todavía sin datos, ver
        # _refresh_size_by_disk) y entonces no hay nada que "encoja" el
        # tamaño por defecto, dejando un hueco enorme y vacío. Con
        # height=1 de partida, se quedan pegados al contenido real que
        # tengan en cada momento, sea mucho o ninguno.
        self._size_legend_fr = ctk.CTkFrame(self._size_frame, fg_color="transparent", height=1)
        self._size_legend_fr.pack(fill="x", padx=14, pady=(0, 8))
        self._size_disks_fr = ctk.CTkFrame(self._size_frame, fg_color="transparent", height=1)
        self._size_disks_fr.pack(fill="x", padx=14, pady=(0, 12))

    def _build_leaderboard_panel(self, parent):
        self._leaderboard_frame = self._panel_frame(parent, "🏆 Top 10 subidores")
        # height=1: ver el comentario en _build_size_panel sobre por qué
        # (CTkFrame pide 200px por defecto si se queda sin hijos).
        self._leaderboard_rows_fr = ctk.CTkFrame(self._leaderboard_frame, fg_color="transparent", height=1)
        self._leaderboard_rows_fr.pack(fill="x", padx=14, pady=(0, 12))

    def _build_category_leaderboards_panel(self, parent):
        """Contenedor para un panel "Top subidores" POR CADA categoría
        configurada (Series, Películas, SeriesPeques...) -- el número de
        paneles es dinámico (depende de cuántas categorías tengan
        subidas), así que aquí solo se reserva el hueco; los paneles de
        verdad se construyen y destruyen enteros en cada refresco (ver
        _refresh_category_leaderboards), no solo sus filas."""
        self._category_leaderboards_fr = ctk.CTkFrame(parent, fg_color="transparent", height=1)
        self._category_leaderboards_fr.pack(fill="x")

    def _build_streaks_panel(self, parent):
        self._streaks_frame = self._panel_frame(parent, "🔥 Racha de subidas")
        self._streaks_rows_fr = ctk.CTkFrame(self._streaks_frame, fg_color="transparent", height=1)
        self._streaks_rows_fr.pack(fill="x", padx=14, pady=(0, 12))

    def _build_deleters_panel(self, parent):
        self._deleters_frame = self._panel_frame(parent, "🧹 Top borradores")
        # height=1: ver el comentario en _build_size_panel sobre por qué
        # (CTkFrame pide 200px por defecto si se queda sin hijos).
        self._deleters_rows_fr = ctk.CTkFrame(self._deleters_frame, fg_color="transparent", height=1)
        self._deleters_rows_fr.pack(fill="x", padx=14, pady=(0, 12))

    def _build_contribution_panel(self, parent):
        self._contribution_frame = self._panel_frame(parent, "⭐ Tu contribución")
        self._contribution_lbl = ctk.CTkLabel(self._contribution_frame, text="", anchor="w", justify="left")
        self._contribution_lbl.pack(fill="x", padx=14, pady=(0, 12))
        # CTkLabel.configure(text_color=None) no tiene el mismo trato
        # especial que el constructor (ver ctk_label.py) -- lanza
        # ValueError. Guardamos el color por defecto aquí para poder
        # "resetear" el color explícitamente más tarde.
        self._default_label_text_color = self._contribution_lbl.cget("text_color")

    _TIMELINE_DAYS = 30
    _TIMELINE_CHART_HEIGHT = 140

    def _build_timeline_panel(self, parent):
        self._timeline_frame = self._panel_frame(parent, "📈 Subidas en los últimos 30 días")
        self._timeline_body_fr = ctk.CTkFrame(self._timeline_frame, fg_color="transparent", height=1)
        self._timeline_body_fr.pack(fill="x", padx=14, pady=(0, 12))

    def _build_recent_panel(self, parent):
        self._recent_frame = self._panel_frame(parent, "🕓 Últimas subidas")
        self._recent_rows_fr = ctk.CTkFrame(self._recent_frame, fg_color="transparent", height=1)
        self._recent_rows_fr.pack(fill="x", padx=14, pady=(0, 12))

    def _build_recent_deletions_panel(self, parent):
        self._recent_deletions_frame = self._panel_frame(parent, "🗑 Últimos borrados")
        self._recent_deletions_rows_fr = ctk.CTkFrame(self._recent_deletions_frame, fg_color="transparent",
                                                        height=1)
        self._recent_deletions_rows_fr.pack(fill="x", padx=14, pady=(0, 12))

    # --------------------------------------------------- datos en vivo --

    def refresh_from_cache(self):
        """Redibuja todos los paneles a partir de los mirrors ya
        sincronizados en memoria por App -- nunca toca la red por su
        cuenta (ver el docstring del módulo para quién mantiene cada
        mirror al día)."""
        self._refresh_size_by_disk()
        self._refresh_leaderboard()
        self._refresh_category_leaderboards()
        self._refresh_streaks()
        self._refresh_deleters()
        self._refresh_contribution()
        self._refresh_timeline()
        self._refresh_recent()
        self._refresh_recent_deletions()

    def _refresh_size_by_disk(self):
        from gui.app import PENDING_COLOR, _fmt_size

        for w in self._size_legend_fr.winfo_children():
            w.destroy()
        for w in self._size_disks_fr.winfo_children():
            w.destroy()

        by_disk = bytes_by_disk(self.app._shared_category_stats,
                                 self.app.config_data.get("ftp_categories", {"tv": [], "movie": [], "libro": []}))
        free_by_disk = self.app._shared_free_space_by_disk

        disks = sorted(set(by_disk) | set(free_by_disk))
        if not disks:
            ctk.CTkLabel(self._size_disks_fr, text="Sin datos todavía.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return

        # Un color estable por nombre de categoría: mismo orden alfabético
        # en cada repintado, para que una categoría no cambie de color de
        # una visita a otra mientras el conjunto de categorías no cambie.
        totals_by_name = {}
        for cats in by_disk.values():
            for c in cats:
                if c["bytes"] <= 0:
                    continue
                t = totals_by_name.setdefault(c["name"], {"count": 0, "bytes": 0})
                t["count"] += c["count"]
                t["bytes"] += c["bytes"]
        names = sorted(totals_by_name)
        color_by_name = {name: _CATEGORY_PALETTE[i % len(_CATEGORY_PALETTE)] for i, name in enumerate(names)}

        legend_row = ctk.CTkFrame(self._size_legend_fr, fg_color="transparent")
        legend_row.pack(fill="x")
        for name in names:
            t = totals_by_name[name]
            ctk.CTkFrame(legend_row, width=12, height=12, fg_color=color_by_name[name],
                         corner_radius=3).pack(side="left", padx=(0, 4), pady=2)
            ctk.CTkLabel(legend_row, text=f"{name} ({t['count']}, {_fmt_size(t['bytes'])})").pack(
                side="left", padx=(0, 14))
        ctk.CTkFrame(legend_row, width=12, height=12, fg_color=PENDING_COLOR,
                     corner_radius=3).pack(side="left", padx=(0, 4), pady=2)
        ctk.CTkLabel(legend_row, text="Libre").pack(side="left")

        for disk in disks:
            cats = sorted((c for c in by_disk.get(disk, []) if c["bytes"] > 0),
                          key=lambda c: c["bytes"], reverse=True)
            free = free_by_disk.get(disk)
            used = sum(c["bytes"] for c in cats)
            total = used + (free or 0)

            block = ctk.CTkFrame(self._size_disks_fr, fg_color="transparent")
            block.pack(fill="x", pady=(0, 10))
            summary = f"Usado: {_fmt_size(used)} · Libre: " + (_fmt_size(free) if free is not None else "--")
            ctk.CTkLabel(block, text=f"{disk.capitalize()}  --  {summary}", anchor="w").pack(fill="x")

            bar = ctk.CTkFrame(block, height=26, corner_radius=4, fg_color=("gray85", "gray25"))
            bar.pack(fill="x", pady=(4, 0))
            if total <= 0:
                continue
            cum = 0.0
            for c in cats:
                frac = c["bytes"] / total
                ctk.CTkFrame(bar, fg_color=color_by_name.get(c["name"], PENDING_COLOR),
                             corner_radius=0).place(relx=cum, rely=0, relwidth=frac, relheight=1)
                cum += frac
            if free:
                frac = max(0.0, min(free / total, 1.0 - cum))
                ctk.CTkFrame(bar, fg_color=PENDING_COLOR,
                             corner_radius=0).place(relx=cum, rely=0, relwidth=frac, relheight=1)

    def _render_leaderboard_rows(self, rows_fr, top, accent_color, value_key="total_bytes",
                                  format_value=None, name_suffix=None):
        """Filas puesto+nombre+barra+valor, compartidas por "Top 10
        subidores", "Top borradores", "Racha de subidas" y los paneles de
        "Top subidores" por categoría -- lo que cambia entre ellos es
        dónde se pintan, de qué color sale el primer puesto, qué campo del
        entry se usa como valor (bytes subidos, días de racha...) y cómo
        se formatea. `top` ya viene ordenado/limitado (ver
        core.upload_stats.top_uploaders / core.deletion_stats.top_deleters
        / core.streaks.top_streaks). `name_suffix(entry)`, si se da,
        añade texto extra tras el nombre (usado para la insignia de hito,
        ver core/milestones.py)."""
        from gui.app import PENDING_COLOR, _fmt_size
        if format_value is None:
            format_value = _fmt_size

        max_value = max(e.get(value_key, 0) for e in top) or 1
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        for i, entry in enumerate(top):
            row = ctk.CTkFrame(rows_fr, fg_color="transparent")
            row.pack(fill="x", pady=3)
            rank_text = medals.get(i, f"{i + 1}.")
            is_first = i == 0
            ctk.CTkLabel(row, text=rank_text, width=32,
                         font=ctk.CTkFont(size=13, weight="bold" if is_first else "normal")).pack(side="left")
            name_text = entry.get("display_name", "?")
            if name_suffix:
                extra = name_suffix(entry)
                if extra:
                    name_text = f"{name_text} {extra}"
            ctk.CTkLabel(row, text=name_text, anchor="w", width=160,
                         font=ctk.CTkFont(size=13, weight="bold" if is_first else "normal"),
                         text_color=accent_color if is_first else None).pack(side="left", padx=(4, 8))
            bar = ctk.CTkProgressBar(row, progress_color=accent_color if is_first else None)
            bar.set(entry.get(value_key, 0) / max_value)
            bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
            ctk.CTkLabel(row, text=format_value(entry.get(value_key, 0)), width=80,
                         text_color=accent_color if is_first else PENDING_COLOR).pack(side="left")

    @staticmethod
    def _milestone_name_suffix(entry):
        badge = milestone_for(entry.get("total_bytes", 0))
        return badge[0] if badge else ""

    def _refresh_leaderboard(self):
        from gui.app import ACCENT, PENDING_COLOR

        for w in self._leaderboard_rows_fr.winfo_children():
            w.destroy()
        top = top_uploaders(self.app._shared_upload_stats, limit=10)
        if not top:
            ctk.CTkLabel(self._leaderboard_rows_fr, text="Nadie ha subido nada todavía.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return
        self._render_leaderboard_rows(self._leaderboard_rows_fr, top, ACCENT,
                                       name_suffix=self._milestone_name_suffix)

    def _refresh_category_leaderboards(self):
        """Un panel "Top subidores" completo por cada categoría con
        subidas registradas (core/category_upload_stats.py) -- número de
        paneles dinámico, así que se reconstruyen enteros, no solo sus
        filas (a diferencia del resto de paneles, que tienen un hueco fijo
        creado una vez en _build)."""
        from gui.app import ACCENT

        for w in self._category_leaderboards_fr.winfo_children():
            w.destroy()
        data = self.app._shared_category_upload_stats
        for category_id, cat_entry in sorted(data.items(), key=lambda kv: kv[1].get("category_name", "")):
            top = top_uploaders(cat_entry.get("uploaders", {}), limit=10)
            if not top:
                continue
            frame = self._panel_frame(
                self._category_leaderboards_fr,
                f"🏆 Top subidores -- {cat_entry.get('category_name', '?')}")
            rows_fr = ctk.CTkFrame(frame, fg_color="transparent", height=1)
            rows_fr.pack(fill="x", padx=14, pady=(0, 12))
            self._render_leaderboard_rows(rows_fr, top, ACCENT, name_suffix=self._milestone_name_suffix)

    def _refresh_streaks(self):
        from gui.app import ACCENT, PENDING_COLOR

        for w in self._streaks_rows_fr.winfo_children():
            w.destroy()
        streaks = compute_streaks(self.app._shared_activity_history)
        top = top_streaks(streaks, limit=10)
        if not top:
            ctk.CTkLabel(self._streaks_rows_fr, text="Nadie tiene una racha activa ahora mismo.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return

        def _fmt_streak(days):
            plural = "s" if days != 1 else ""
            return f"🔥 {days} día{plural}"

        self._render_leaderboard_rows(self._streaks_rows_fr, top, ACCENT,
                                       value_key="streak_days", format_value=_fmt_streak)

    def _refresh_deleters(self):
        from gui.app import WARNING_COLOR, PENDING_COLOR

        for w in self._deleters_rows_fr.winfo_children():
            w.destroy()
        top = top_deleters(self.app._shared_deletion_stats, limit=10)
        if not top:
            ctk.CTkLabel(self._deleters_rows_fr, text="Nadie ha borrado nada todavía.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return
        self._render_leaderboard_rows(self._deleters_rows_fr, top, WARNING_COLOR)

    def _refresh_contribution(self):
        from gui.app import PENDING_COLOR, _fmt_size

        person = (self.app.config_data.get("app_user_name", "") or "").strip()
        if not person:
            self._contribution_lbl.configure(
                text="Configura \"Tu nombre\" en Ajustes → Conexión FTP para aparecer aquí.",
                text_color=PENDING_COLOR)
            return
        ranked = top_uploaders(self.app._shared_upload_stats, limit=len(self.app._shared_upload_stats) or 1)
        key = None
        from core.upload_stats import _normalize_key
        norm = _normalize_key(person)
        for i, entry in enumerate(ranked):
            if _normalize_key(entry.get("display_name", "")) == norm:
                key = i
                break
        if key is None:
            self._contribution_lbl.configure(
                text=f"{person} todavía no ha subido nada -- ¡sé el primero en aparecer en el ranking!",
                text_color=PENDING_COLOR)
            return
        my_entry = ranked[key]
        my_gb = _fmt_size(my_entry.get("total_bytes", 0))
        if key == 0:
            text = f"{person} va Nº1 con {my_gb} subidos -- ¡el que más aporta al servidor!"
        else:
            prev = ranked[key - 1]
            delta = prev.get("total_bytes", 0) - my_entry.get("total_bytes", 0)
            text = (f"{person} va Nº{key + 1} con {my_gb} subidos -- "
                    f"te faltan {_fmt_size(delta)} para adelantar a {prev.get('display_name', '?')}.")
        nxt = next_milestone(my_entry.get("total_bytes", 0))
        if nxt:
            gap_bytes, label = nxt
            text += f" Próximo hito: {_fmt_size(gap_bytes)} más para {label}."
        else:
            text += " ¡Ya tienes todas las insignias! 💎"
        self._contribution_lbl.configure(text=text, text_color=self._default_label_text_color)

    def _refresh_timeline(self):
        """Gráfico de barras estático (sin animación, geometría fija con
        .place() -- mismo recurso que ya usa la barra de tamaño por disco)
        -- un día por columna en el eje horizontal, GB subidos ese día en
        el vertical. Incluye TODOS los días del rango, no solo los que
        tuvieron subidas, para que el eje horizontal sea continuo de
        verdad."""
        from gui.app import ACCENT, PENDING_COLOR, _fmt_size

        for w in self._timeline_body_fr.winfo_children():
            w.destroy()

        entries = [e for e in self.app._shared_activity_history
                   if e.get("kind") == "subida" and e.get("status", "ok") == "ok"]
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=i) for i in range(self._TIMELINE_DAYS - 1, -1, -1)]
        bytes_by_day = dict.fromkeys(days, 0)
        for e in entries:
            day = datetime.datetime.fromtimestamp(e.get("ts", 0)).date()
            if day in bytes_by_day:
                bytes_by_day[day] += e.get("size", 0)

        if not any(bytes_by_day.values()):
            ctk.CTkLabel(self._timeline_body_fr, text="Sin subidas registradas en los últimos 30 días.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return

        max_bytes = max(bytes_by_day.values()) or 1
        n = len(days)

        chart = ctk.CTkFrame(self._timeline_body_fr, fg_color="transparent", height=self._TIMELINE_CHART_HEIGHT)
        chart.pack(fill="x")
        chart.pack_propagate(False)
        for i in range(n):
            chart.grid_columnconfigure(i, weight=1, uniform="day")
        chart.grid_rowconfigure(0, weight=1)

        for i, day in enumerate(days):
            day_bytes = bytes_by_day[day]
            cell = ctk.CTkFrame(chart, fg_color="transparent")
            cell.grid(row=0, column=i, sticky="nsew", padx=1)
            if day_bytes > 0:
                frac = max(day_bytes / max_bytes, 0.03)   # barra mínima visible, no un punto invisible
                bar = ctk.CTkFrame(cell, fg_color=ACCENT, corner_radius=0)
                bar.place(relx=0, rely=1.0, relwidth=1.0, relheight=frac, anchor="sw")

        # Eje vertical: solo el máximo y el cero como referencia -- sin
        # líneas de rejilla, para no complicar el dibujo. Se coloca DESPUÉS
        # de las barras para quedar siempre encima y legible.
        ctk.CTkLabel(chart, text=_fmt_size(max_bytes), font=ctk.CTkFont(size=9),
                     text_color=PENDING_COLOR, fg_color=("gray95", "gray17")
                     ).place(relx=0, rely=0, anchor="nw")
        ctk.CTkLabel(chart, text="0", font=ctk.CTkFont(size=9),
                     text_color=PENDING_COLOR, fg_color=("gray95", "gray17")
                     ).place(relx=0, rely=1.0, anchor="sw")

        # Eje horizontal: el día del mes cada pocas columnas -- ponerlo en
        # todas las columnas amontonaría los números, ilegible con 30 días.
        labels_row = ctk.CTkFrame(self._timeline_body_fr, fg_color="transparent")
        labels_row.pack(fill="x")
        for i in range(n):
            labels_row.grid_columnconfigure(i, weight=1, uniform="day")
        label_every = 5
        for i, day in enumerate(days):
            text = str(day.day) if i % label_every == 0 or i == n - 1 else ""
            ctk.CTkLabel(labels_row, text=text, font=ctk.CTkFont(size=9),
                         text_color=PENDING_COLOR).grid(row=0, column=i)

    def _refresh_recent(self):
        from gui.app import PENDING_COLOR, _fmt_size

        for w in self._recent_rows_fr.winfo_children():
            w.destroy()
        entries = [e for e in self.app._shared_activity_history
                   if e.get("kind") == "subida" and e.get("status", "ok") == "ok"]
        entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
        recent = entries[:8]
        if not recent:
            ctk.CTkLabel(self._recent_rows_fr, text="Sin actividad reciente.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return
        for e in recent:
            person = e.get("person", "?")
            name = e.get("filename", "?")
            text = f"{person} subió {name} ({_fmt_size(e.get('size', 0))}) {_fmt_ago(e.get('ts', 0))}"
            ctk.CTkLabel(self._recent_rows_fr, text=text, anchor="w").pack(fill="x", pady=1)

    def _refresh_recent_deletions(self):
        """Mismo patrón que _refresh_recent, pero para borrados -- los
        registros de borrado usan "name" (no "filename", ver
        App._save_deletion_history_entry) para el nombre del elemento."""
        from gui.app import PENDING_COLOR, _fmt_size

        for w in self._recent_deletions_rows_fr.winfo_children():
            w.destroy()
        entries = [e for e in self.app._shared_activity_history
                   if e.get("kind") == "borrado" and e.get("status", "ok") == "ok"]
        entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
        recent = entries[:8]
        if not recent:
            ctk.CTkLabel(self._recent_deletions_rows_fr, text="Sin borrados recientes.",
                         text_color=PENDING_COLOR).pack(pady=8)
            return
        for e in recent:
            person = e.get("person", "?")
            name = e.get("name", "?")
            text = f"{person} borró {name} ({_fmt_size(e.get('size', 0))}) {_fmt_ago(e.get('ts', 0))}"
            ctk.CTkLabel(self._recent_deletions_rows_fr, text=text, anchor="w").pack(fill="x", pady=1)
