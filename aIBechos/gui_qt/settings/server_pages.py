"""Sub-pestañas de Configuración → 🌐 Servidor (iguales para todo el que use
este FTP, ver core/server_config.py): TMDB / IA, Plantillas, Categorías,
Servidores de medios, Reservas y Preferencias descargas. Mismos campos y
límites que App._collect_settings en Tk."""

from __future__ import annotations

import math

from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from core.ftp_categories import new_category_id
from gui_qt import theme
from gui_qt.bridge import run_in_thread, ui
from gui_qt.settings.widgets import Page, desc_label, hbox, list_box, set_status, status_label


def _validate_async(lbl, fn):
    """Pone "Validando..." y ejecuta fn() -> bool en un hilo."""
    set_status(lbl, "Validando...", theme.WARNING_COLOR)

    def worker():
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        ui(lambda: set_status(lbl, "API Key válida" if ok else "API Key inválida",
                              theme.SUCCESS_COLOR if ok else theme.ERROR_COLOR))
    run_in_thread(worker)


class TmdbPage(Page):
    def build(self):
        c = self.card("TMDB API")
        key = c.text("API Key:", "tmdb_api_key", secret=True, placeholder="Gratis en themoviedb.org/settings/api")
        c.combo("Idioma:", "language", ["es-ES", "en-US", "pt-BR", "fr-FR", "de-DE", "ja-JP"], "es-ES",
                editable=True)
        st = status_label()
        b = QPushButton("Validar API Key")
        b.setToolTip("Comprobar contra TMDB que la clave es válida, sin gastar una búsqueda de verdad.")
        b.clicked.connect(lambda: self._validate_tmdb(key.text().strip(), st))
        c.row(None, hbox(b, "themoviedb.org → Configuración → API"))
        c.row(None, st)

        c.subtitle("IA como último recurso")
        c.switch("Usar IA como último recurso (TMDB / ComicVine)", "ai_fallback_enabled", False)
        ai = c.text("API Key (Groq):", "ai_api_key", secret=True, placeholder="Gratis en console.groq.com/keys")
        st_ai = status_label()
        b = QPushButton("Validar API Key (Groq)")
        b.setToolTip("Comprobar que la clave de Groq es válida. Solo pide el listado de modelos: no consume "
                     "cuota de consultas.")
        b.clicked.connect(lambda: self._validate_groq(ai.text().strip(), st_ai))
        learned = QPushButton("Términos aprendidos")
        learned.setToolTip("Ver y borrar la basura que la IA ha aprendido a quitar de los nombres de archivo "
                           "(grupos de subida, etiquetas de calidad...).")
        learned.clicked.connect(self._learned_terms)
        c.row(None, hbox(b, learned))
        c.row(None, st_ai)
        c.note("Solo se consulta cuando TMDB/ComicVine falla — cada consulta queda registrada en ai_fallback.log")

        c.subtitle("ComicVine (cómics/manga)")
        cv = c.text("API Key:", "comicvine_api_key", secret=True, placeholder="Gratis en comicvine.gamespot.com/api")
        st_cv = status_label()
        b = QPushButton("Validar API Key")
        b.setToolTip("Comprobar contra ComicVine que la clave es válida.")
        b.clicked.connect(lambda: self._validate_cv(cv.text().strip(), st_cv))
        c.row(None, hbox(b, "comicvine.gamespot.com/api → solicitar key"))
        c.row(None, st_cv)

        c.subtitle("Google Books (ebooks, apoyo de OpenLibrary) -- opcional")
        gb = c.text("API Key:", "google_books_api_key", secret=True,
                    placeholder="Opcional -- gratis en console.cloud.google.com")
        st_gb = status_label()
        b = QPushButton("Validar API Key")
        b.setToolTip("Comprobar que la clave de Google Books es válida. Solo se usa cuando OpenLibrary falla "
                     "o no encuentra el libro.")
        b.clicked.connect(lambda: self._validate_gb(gb.text().strip(), st_gb))
        c.row(None, hbox(b))
        c.row(None, st_gb)
        c.note("console.cloud.google.com → crear proyecto → activar \"Books API\" → credenciales. OpenLibrary "
               "(sin key, sin ajustes) se prueba primero -- esta key solo se usa de apoyo, si OpenLibrary no "
               "encuentra el libro o falla.")

        c.subtitle("Streaming Availability (doblaje ES) -- opcional")
        sa = c.text("API Key:", "streaming_availability_key", secret=True,
                    placeholder="Opcional -- gratis en developers.movieofthenight.com")
        st_sa = status_label()
        b = QPushButton("Validar API Key")
        b.setToolTip("Comprobar que la clave de Streaming Availability es válida. Sin ella, el doblaje se "
                     "decide con eldoblaje/wiki/servidor.")
        b.clicked.connect(lambda: self._validate_sa(sa.text().strip(), st_sa))
        c.row(None, hbox(b))
        c.row(None, st_sa)
        c.note("developers.movieofthenight.com → plan gratis (1000 req/mes, sin tarjeta). 1 llamada = 1 serie "
               "completa (audios por episodio en España).")

    # Las validaciones usan un cliente de usar y tirar: probar una clave sin
    # guardarla no debe cambiar la que usa la app.
    def _validate_tmdb(self, key, lbl):
        if not key:
            return set_status(lbl, "Ingresa una API Key", theme.ERROR_COLOR)
        from core.api_client import TMDBClient
        _validate_async(lbl, lambda: TMDBClient(key).validate_key())

    def _validate_groq(self, key, lbl):
        if not key:
            return set_status(lbl, "Ingresa una API Key", theme.ERROR_COLOR)
        from core.ai_title_fallback import validate_api_key
        _validate_async(lbl, lambda: validate_api_key(key))

    def _validate_cv(self, key, lbl):
        if not key:
            return set_status(lbl, "Ingresa una API Key", theme.ERROR_COLOR)
        from core.comicvine_client import ComicVineClient
        _validate_async(lbl, lambda: ComicVineClient(key).validate_key())

    def _validate_gb(self, key, lbl):
        # Opcional: vacía vuelve a la cuota anónima, también se puede validar.
        from core.book_client import GoogleBooksClient
        _validate_async(lbl, lambda: GoogleBooksClient(key).validate_key())

    def _validate_sa(self, key, lbl):
        if not key:
            return set_status(lbl, "Vacía: fuente desactivada (vale eldoblaje/wiki/servidor)")

        def check():
            from core.streaming_availability import get_show
            return get_show(key, 456, granularity="show") is not None
        _validate_async(lbl, check)

    def _learned_terms(self):
        from gui_qt.settings.dialogs import LearnedTermsDialog
        LearnedTermsDialog(self).exec()


_PRESETS = {
    "tv_template": ["{serie} {temporada}x{episodio:02d} {titulo}{ext}",
                    "{serie} S{temporada:02d}E{episodio:02d} {titulo}{ext}",
                    "{serie} - S{temporada:02d}E{episodio:02d} - {titulo}{ext}",
                    "{serie} {temporada}x{episodio:02d}{ext}"],
    "movie_template": ["{serie} ({año}){ext}", "{serie} [{año}]{ext}", "{serie}.{año}{ext}", "{serie}{ext}"],
    "anime_template": ["{serie} {temporada}x{episodio:03d} {titulo}{ext}", "{serie} - {episodio:04d}{ext}",
                       "[{serie}] {episodio:04d} {titulo}{ext}", "{serie} EP{episodio:04d}{ext}"],
    "libro_template": ["{serie}{ext}", "{serie} ({año}){ext}"],
    "comic_template": ["{serie} ({año}) #{episodio:02d}{ext}", "{serie} #{episodio:02d}{ext}"],
}
_LINK_LEVELS = (("show", "custom_links_show", "Nivel serie"),
                ("season", "custom_links_season", "Nivel temporada"),
                ("episode", "custom_links_episode", "Nivel episodio"),
                ("movie", "custom_links_movie", "Nivel película"))


class TemplatesPage(Page):
    def build(self):
        c = self.card("Plantillas de nombre")
        guide = QPushButton("? Guía")
        guide.setProperty("accent", True)
        guide.setToolTip("Ver qué variables admiten las plantillas de nombre ({serie}, {temporada}, "
                         "{episodio}, {año}, {ext}...) y ejemplos de cada una.")
        guide.clicked.connect(self._guide)
        c.row(None, hbox(guide))
        for label, key in (("TV / Series:", "tv_template"), ("Películas:", "movie_template"),
                           ("Anime:", "anime_template"), ("Libros:", "libro_template"),
                           ("Cómics:", "comic_template")):
            c.combo(label, key, _PRESETS[key], _PRESETS[key][0], editable=True)
        c.switch("Renombrar archivos en destino (FTP)", "rename_remote", True)

        c = self.card("Enlaces personalizables (episodios que faltan)",
                      "Botones para abrir una URL con variables sustituidas -- solo eso, nunca se conectan a "
                      "nada por su cuenta. Variables disponibles: {serie}, {tmdb_id}, {temporada}, {episodio}, "
                      "{titulo}, {nombre_archivo}, {ruta} (carpeta de la serie en el FTP, si ya existe) -- las "
                      "que no apliquen al nivel del botón -- p.ej. {episodio} a nivel serie -- quedan vacías. "
                      "Marca \"Segundo plano\" para que, en vez de abrir una pestaña del navegador, se haga una "
                      "petición silenciosa a la URL (útil para webhooks o disparar una búsqueda en otra "
                      "herramienta).")
        self._links: dict = {}
        self._link_boxes: dict = {}
        for level, key, title in _LINK_LEVELS:
            lbl = QLabel(title)
            lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
            c.row(None, lbl)
            box = QWidget()
            QVBoxLayout(box).setContentsMargins(0, 0, 0, 0)
            c.row(None, box)
            self._link_boxes[level] = box
            self._links[level] = []
            for link in self.cfg.get(key, []) or []:
                self._add_link(level, link.get("name", ""), link.get("url_template", ""),
                               link.get("background", False))
            add = QPushButton("+ Añadir enlace")
            add.setToolTip("Añadir un enlace propio que aparecerá en la ficha de cada título, para abrirlo "
                           "donde tú quieras.")
            add.clicked.connect(lambda _=False, lv=level: self._add_link(lv, "", ""))
            c.row(None, hbox(add))
            self.bind(key, lambda lv=level: self._collect_links(lv))

    def _add_link(self, level, name, url, background=False):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        n = QLineEdit(name)
        n.setPlaceholderText("Nombre")
        n.setFixedWidth(150)
        u = QLineEdit(url)
        u.setPlaceholderText("https://.../{serie}/{tmdb_id}")
        bg = QCheckBox("Segundo plano")
        bg.setChecked(bool(background))
        rm = QPushButton("✕")
        rm.setStyleSheet(f"color: {theme.ERROR_COLOR};")
        rm.setToolTip("Quitar este enlace personalizado de la lista.")
        lay.addWidget(n)
        lay.addWidget(u, 1)
        lay.addWidget(bg)
        lay.addWidget(rm)
        entry = {"row": row, "name": n, "url": u, "background": bg}
        self._links[level].append(entry)
        rm.clicked.connect(lambda: self._remove_link(level, entry))
        self._link_boxes[level].layout().addWidget(row)

    def _remove_link(self, level, entry):
        self._links[level] = [e for e in self._links[level] if e is not entry]
        entry["row"].deleteLater()

    def _collect_links(self, level) -> list:
        return [{"name": e["name"].text().strip(), "url_template": e["url"].text().strip(),
                 "background": e["background"].isChecked()}
                for e in self._links[level] if e["name"].text().strip() or e["url"].text().strip()]

    def _guide(self):
        from gui_qt.settings.dialogs import TemplateGuideDialog
        TemplateGuideDialog(self).exec()


_CATEGORY_TYPES = ("tv", "movie", "libro")
_CATEGORY_TYPE_LABELS = {"tv": "Series", "movie": "Películas", "libro": "Libros/Cómics"}
_LIBRO_GENRE_OPTIONS = [("ebook", "Ebook"), ("comic", "Cómic")]


class CategoriesPage(Page):
    def build(self):
        saved = self.cfg.get("ftp_categories", {"tv": [], "movie": [], "libro": []}) or {}
        self._cats = {mt: [dict(c) for c in saved.get(mt, [])] for mt in _CATEGORY_TYPES}
        self._widgets: dict = {}   # id(cat) -> widgets
        c = self.card("Categorías FTP",
                      "Cada categoría busca y sube contenido en su propia ruta del servidor. La app elige la "
                      "categoría sola según el género de TMDB (o, para Libros/Cómics, según sea ebook o "
                      "cómic) — el orden importa (la primera que coincida gana); una categoría sin géneros "
                      "marcados actúa como categoría por defecto para lo que no encaje en ninguna otra.")
        reload_btn = QPushButton("🔄 Recargar géneros")
        reload_btn.setToolTip("Volver a pedir a TMDB la lista de géneros con la que se marcan las categorías. "
                              "Solo hace falta si TMDB añade géneros nuevos o la lista salió vacía.")
        reload_btn.clicked.connect(self._load_genres)
        c.row(None, hbox(reload_btn))
        cols = QWidget()
        grid = QHBoxLayout(cols)
        grid.setContentsMargins(0, 0, 0, 0)
        self._containers = {}
        for mt in _CATEGORY_TYPES:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            grid.addWidget(box, 1)
            self._containers[mt] = box
        c.row(None, cols)
        for mt in _CATEGORY_TYPES:
            self._render(mt)
        self.bind("ftp_categories", self._collect_categories)
        if not any((getattr(self.host, "_genres_cache", None) or {}).values()):
            self._load_genres()

    def _genre_options(self, mt, cat):
        if mt == "libro":
            return _LIBRO_GENRE_OPTIONS
        loaded = (getattr(self.host, "_genres_cache", None) or {}).get(mt) or []
        if loaded:
            return [(g["id"], g["name"]) for g in loaded]
        return [(gid, f"ID {gid}") for gid in (cat.get("genre_ids") or [])]

    def _sync(self, mt):
        """Vuelca lo tecleado a los dicts antes de reordenar/repintar."""
        for cat in self._cats[mt]:
            w = self._widgets.get(id(cat))
            if w is None:
                continue
            cat["name"] = w["name"].text().strip() or cat.get("name", "")
            cat["root"] = w["root"].text().strip()
            cat["template"] = w["template"].text().strip()
            cat["genre_ids"] = sorted(gid for gid, ch in w["genres"].items() if ch.isChecked())

    def _render(self, mt):
        box = self._containers[mt]
        lay = box.layout()
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)   # fuera ya, no al siguiente ciclo
                w.deleteLater()
        t = QLabel(f"Categorías de {_CATEGORY_TYPE_LABELS[mt]}")
        t.setStyleSheet("font-weight: bold;")
        lay.addWidget(t)
        cats = self._cats[mt]
        for idx, cat in enumerate(cats):
            lay.addWidget(self._card(mt, cat, idx, len(cats)))
        add = QPushButton("+ Añadir categoría")
        add.setToolTip("Añadir una carpeta de destino nueva con sus propios géneros. Una categoría sin ningún "
                       "género marcado hace de destino por defecto para lo que no encaje en ninguna otra.")
        add.clicked.connect(lambda _=False, m=mt: self._add(m))
        lay.addWidget(add)
        lay.addStretch(1)

    def _card(self, mt, cat, idx, total):
        card = QWidget()
        card.setObjectName("catcard")
        card.setStyleSheet(f"QWidget#catcard {{ background: {theme.BG_ALT}; border-radius: 8px; }}")
        v = QVBoxLayout(card)
        top = QHBoxLayout()
        name = QLineEdit(cat.get("name", ""))
        name.setPlaceholderText("Nombre de la categoría")
        top.addWidget(name, 1)
        if idx > 0:
            up = QPushButton("▲")
            up.setToolTip("Subir esta categoría. El orden decide: se usa la primera categoría cuyos géneros "
                          "encajen.")
            up.clicked.connect(lambda: self._move(mt, cat, -1))
            top.addWidget(up)
        if idx < total - 1:
            down = QPushButton("▼")
            down.setToolTip("Bajar esta categoría. El orden decide: se usa la primera categoría cuyos géneros "
                            "encajen.")
            down.clicked.connect(lambda: self._move(mt, cat, 1))
            top.addWidget(down)
        rm = QPushButton("✕")
        rm.setStyleSheet(f"color: {theme.ERROR_COLOR};")
        rm.setToolTip("Eliminar esta categoría. No toca nada del servidor: solo deja de usarse para decidir a "
                      "qué carpeta va cada archivo.")
        rm.clicked.connect(lambda: self._remove(mt, cat))
        top.addWidget(rm)
        v.addLayout(top)
        v.addWidget(desc_label("Géneros (ninguno marcado = categoría por defecto):"))
        genres = {}
        options = self._genre_options(mt, cat)
        if not options:
            v.addWidget(desc_label("(géneros no cargados aún — pulsa 'Recargar géneros')"))
        else:
            g = QGridLayout()
            cur = set(cat.get("genre_ids") or [])
            for i, (gid, gname) in enumerate(options):
                ch = QCheckBox(gname)
                ch.setChecked(gid in cur)
                g.addWidget(ch, i // 2, i % 2)
                genres[gid] = ch
            v.addLayout(g)
        v.addWidget(desc_label("Ruta en el servidor:"))
        root = QLineEdit(cat.get("root", ""))
        root.setPlaceholderText("/datos2/series")
        v.addWidget(root)
        v.addWidget(desc_label("Plantilla (relativa a la ruta):"))
        tpl = QLineEdit(cat.get("template", "{serie}/"))
        v.addWidget(tpl)
        self._widgets[id(cat)] = {"name": name, "root": root, "template": tpl, "genres": genres}
        return card

    def _new(self) -> dict:
        return {"id": new_category_id(), "name": "Nueva categoría", "genre_ids": [], "root": "",
                "template": "{serie}/"}

    def _add(self, mt):
        self._sync(mt)
        self._cats[mt].append(self._new())
        self._render(mt)

    def _remove(self, mt, cat):
        self._sync(mt)
        self._cats[mt] = [c for c in self._cats[mt] if c is not cat]
        self._render(mt)

    def _move(self, mt, cat, delta):
        self._sync(mt)
        lst = self._cats[mt]
        i = lst.index(cat)
        j = max(0, min(len(lst) - 1, i + delta))
        if i != j:
            lst.insert(j, lst.pop(i))
            self._render(mt)

    def _load_genres(self):
        host = self.host

        def worker():
            try:
                host._genres_cache = {"tv": host.tmdb.get_genres("tv"), "movie": host.tmdb.get_genres("movie")}
            except Exception:
                return
            ui(self._genres_loaded)
        run_in_thread(worker)

    def _genres_loaded(self):
        for mt in ("tv", "movie"):
            self._sync(mt)
            self._render(mt)

    def _collect_categories(self) -> dict:
        out = {}
        for mt in _CATEGORY_TYPES:
            self._sync(mt)
            out[mt] = [{"id": c.get("id") or self._new()["id"], "name": c.get("name", ""),
                        "genre_ids": c.get("genre_ids", []), "root": c.get("root", ""),
                        "template": c.get("template", "{serie}/")} for c in self._cats[mt]]
        return out


class MediaServersPage(Page):
    def build(self):
        c = self.card("Servidores de medios", "Refresca la biblioteca justo tras subir, en vez de esperar al "
                                              "siguiente escaneo periódico. Cada uno es independiente.")
        c.subtitle("Plex")
        c.switch("Activar", "plex_enabled", False)
        ph = c.text("URL:", "plex_host", placeholder="http://192.168.1.10:32400")
        pt = c.text("Token:", "plex_token", secret=True)
        st_p = status_label()
        b = QPushButton("Validar")
        b.setToolTip("Conectar con Plex para comprobar que la dirección y el token funcionan.")
        b.clicked.connect(lambda: self._validate("plex", ph.text().strip(), pt.text().strip(), st_p))
        c.row(None, hbox(b, st_p))

        c.subtitle("Jellyfin")
        c.switch("Activar", "jellyfin_enabled", False)
        jh = c.text("URL:", "jellyfin_host", placeholder="http://192.168.1.10:8096")
        jk = c.text("API Key:", "jellyfin_api_key", secret=True)
        c.text("Usuario (opcional):", "jellyfin_username", placeholder="vacío = visionado de TODOS los usuarios")
        c.note("Vacío (recomendado): se combina el visionado de TODOS los usuarios del servidor -- algo se "
               "considera visto si CUALQUIERA lo ha visto. Rellena esto solo si quieres restringirlo a una "
               "sola persona en concreto.")
        st_j = status_label()
        b = QPushButton("Validar")
        b.clicked.connect(lambda: self._validate("jellyfin", jh.text().strip(), jk.text().strip(), st_j))
        c.row(None, hbox(b, st_j))
        c.note("El detector de episodios que faltan tiene su propia pantalla: pestaña \"🔍 Episodios\".")

        c = self.card("Web de solicitudes")
        c.text("Dirección:", "solicitudes_web_url", placeholder="https://aibechos.fordema.es/")
        c.note("Al completar una solicitud, este equipo avisa a la web para que mande la notificación al móvil "
               "de quien la pidió. Vacío = sin aviso inmediato (la web lo comprueba sola al usarse).")

    def _validate(self, which, host, secret, lbl):
        if not host or not secret:
            return set_status(lbl, "Rellena URL y token" if which == "plex" else "Rellena URL y API Key",
                              theme.ERROR_COLOR)
        set_status(lbl, "Validando...", theme.WARNING_COLOR)

        def worker():
            from core import media_server_refresh as msr
            ok = (msr.validate_plex if which == "plex" else msr.validate_jellyfin)(host, secret)
            ui(lambda: set_status(lbl, "Conexión válida" if ok else "No se pudo conectar",
                                  theme.SUCCESS_COLOR if ok else theme.ERROR_COLOR))
        run_in_thread(worker)


class ReservationsPage(Page):
    def build(self):
        c = self.card("Cuota de reservas", "Cuánto puede reservar cada persona en \"Protegidos\" para que su "
                                           "contenido nunca aparezca como candidato a borrar en Liberar espacio.")
        c.spin("GB por persona:", "reservation_quota_gb", 1, 1_000_000, 100)


# (kind, etiqueta, clave de config) del apartado Otros filtros.
_OTHER_BOX_DEFS = (("adult", "Adultos (excluye)", "p2p_blocked_adult"),
                   ("sample", "Muestras/trailers", "p2p_blocked_sample"),
                   ("scr", "Cine/screeners", "p2p_blocked_scr"),
                   ("exts", "Ext. no-vídeo", "p2p_blocked_exts"))


class DownloadPrefsPage(Page):
    def build(self):
        from core.download_quality import (LANG_CONFIG_KEYS, SCORE_GROUP_TIPS, SCORE_WEIGHT_DEFAULTS,
                                           SCORE_WEIGHT_LABELS, SCORE_WEIGHT_TIPS, format_lang_box,
                                           parse_lang_box, parse_provider_lines)
        self.col.addWidget(desc_label("Qué proveedores puntúan, qué marcas se excluyen, qué idiomas no se "
                                      "descargan y cuánto puntúa cada cosa. Vale para todos los clientes de "
                                      "este servidor."))
        c = self.card("Proveedores")
        trusted = list_box(self.cfg.get("p2p_trusted_groups", []),
                           tip="Proveedores de confianza (esta lista es la que vale, entera: quitar uno le quita "
                               "el bonus).\nSus releases ganan +25 al elegir qué descargar.\nUno por línea, sin "
                               "comas ni regex: se busca el texto tal cual.")
        blocked = list_box(self.cfg.get("p2p_blocked_groups", []),
                           tip="Proveedores/marcas que no se quieren.\nUn release que los contenga NO se descarga "
                               "nunca, aunque sea lo único\ndisponible y aunque traiga español (la marca dice "
                               "quién lo hizo).\nUno por línea.")
        c.row(None, self._two_cols(("De confianza (+25)", trusted), ("Bloqueados (no descargar)", blocked)))
        self.bind("p2p_trusted_groups", lambda: parse_provider_lines(trusted.toPlainText()))
        self.bind("p2p_blocked_groups", lambda: parse_provider_lines(blocked.toPlainText()))

        c = self.card("Idioma", "El motor siempre excluye estos idiomas sin pista en español (VOS, francés/"
                                "VOSTFR, italiano, alemán, portugués) y penaliza el catalán. Tus marcadores se "
                                "AÑADEN por secciones [vos] [fr] [it] [de] [pt] [ca], uno por línea. No toques "
                                "las líneas [..].")
        langs = {kind: (self.cfg.get(ckey, []) or []) for kind, ckey in LANG_CONFIG_KEYS.items()}
        lang_box = list_box([], height=240, tip="Todos los idiomas juntos, por secciones.\nCada marcador se suma "
                                                "al motor de su idioma.\nUno por línea, sin comas ni regex.")
        lang_box.setPlainText(format_lang_box(langs))
        c.row(None, lang_box)

        def lang_value(kind):
            try:
                return list((parse_lang_box(lang_box.toPlainText()) or {}).get(kind, []))
            except Exception:
                return []
        for kind, ckey in LANG_CONFIG_KEYS.items():
            self.bind(ckey, lambda k=kind: lang_value(k))

        c = self.card("Otros filtros", "Adultos excluye siempre; muestras y cine penalizan; extensiones no-vídeo "
                                       "penalizan (sin punto). Tus listas se suman al motor.")
        boxes = []
        for kind, label, ckey in _OTHER_BOX_DEFS:
            box = list_box(self.cfg.get(ckey, []), height=80,
                           tip=f"Marcadores extra de {label}.\nSe suman al motor.\nUno por línea, sin comas ni regex.")
            self.bind(ckey, lambda b=box: parse_provider_lines(b.toPlainText()))
            boxes.append((label, box))
        c.row(None, self._two_cols(*boxes[:2]))
        c.row(None, self._two_cols(*boxes[2:]))

        c = self.card("Puntuación", "Cuánto suma o resta cada cosa al elegir qué descargar. Vacío o texto no "
                                    "numérico = valor de fábrica.")
        reset = QPushButton("Restablecer valores")
        reset.setToolTip("Vuelve a los valores de fábrica en las casillas.\nSe aplica al guardar Ajustes.")
        c.row(None, hbox(reset))
        overrides = self.cfg.get("p2p_score_weights", {}) or {}
        self._weights = {}
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        r, col, last = 0, 0, None
        for key, factory in SCORE_WEIGHT_DEFAULTS.items():
            group, label = SCORE_WEIGHT_LABELS.get(key, ("Otros", key))
            if group != last:
                if col:
                    r, col = r + 1, 0
                gh = QLabel(group)
                gh.setStyleSheet("font-weight: bold; margin-top: 8px;")
                gh.setToolTip(SCORE_GROUP_TIPS.get(group, group))
                grid.addWidget(gh, r, 0, 1, 6)
                r, last = r + 1, group
            tip = SCORE_WEIGHT_TIPS.get(key, key)
            lbl = QLabel(label)
            lbl.setToolTip(tip)
            e = QLineEdit(str(overrides[key]) if isinstance(overrides, dict) and key in overrides else str(factory))
            e.setFixedWidth(70)
            e.setToolTip(f"{tip}\nValor de fábrica: {factory}")
            grid.addWidget(lbl, r, col * 2)
            grid.addWidget(e, r, col * 2 + 1)
            self._weights[key] = e
            col += 1
            if col >= 3:
                r, col = r + 1, 0
        c.row(None, grid_w)
        reset.clicked.connect(lambda: [e.setText(str(SCORE_WEIGHT_DEFAULTS.get(k, "")))
                                       for k, e in self._weights.items()])
        self.bind("p2p_score_weights", lambda: self._collect_weights(SCORE_WEIGHT_DEFAULTS))

    def _collect_weights(self, defaults) -> dict:
        """Solo los pesos cambiados respecto a fábrica (como en Tk)."""
        out = {}
        for key, factory in defaults.items():
            e = self._weights.get(key)
            if e is None:
                continue
            try:
                val = float(e.text().strip().replace(",", "."))
            except ValueError:
                continue
            if math.isfinite(val) and val != float(factory):
                out[key] = val
        return out

    @staticmethod
    def _two_cols(*pairs) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        for i, (label, widget) in enumerate(pairs):
            g.addWidget(QLabel(label), 0, i)
            g.addWidget(widget, 1, i)
        return w

