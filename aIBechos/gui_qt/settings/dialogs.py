"""Diálogos de Configuración (Qt): cambio de nombre con reservas, cambios sin
guardar, términos aprendidos por la IA y guía de plantillas."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QTextBrowser, QVBoxLayout)

from gui_qt import theme
from gui_qt.files.dialogs import _ChoiceDialog


class RenameReservationsDialog(_ChoiceDialog):
    """Al cambiar "Tu nombre" teniendo reservas con el anterior (ver
    _resolve_app_user_name_change). .result: "transfer" | "unprotect" | None."""

    def __init__(self, parent, old_name: str, new_name: str):
        super().__init__(parent, "Cambio de nombre", close_result=None, width=460)
        self.label(f"Tienes contenido reservado como \"{old_name}\".", bold=True, size=11)
        self.label(f"¿Qué quieres hacer con esas reservas al pasar a llamarte \"{new_name}\"?" if new_name else
                   "Vas a quitar tu nombre de Ajustes. ¿Qué quieres hacer con esas reservas?",
                   color=theme.PENDING_COLOR)
        specs = [("Traspasarlas al nombre nuevo", "transfer", "accent")] if new_name else []
        specs += [("Desproteger todas", "unprotect", "danger"), ("Cancelar", None, None)]
        self.buttons(specs)


class UnsavedSettingsDialog(_ChoiceDialog):
    """Al salir de Configuración con cambios. .result: "save" | "discard" | None."""

    def __init__(self, parent):
        super().__init__(parent, "Cambios sin guardar", close_result=None, width=400)
        self.label("Tienes cambios sin guardar en Ajustes.", bold=True, size=11)
        self.label("¿Qué quieres hacer?", color=theme.PENDING_COLOR)
        self.buttons([("Guardar y salir", "save", "accent"), ("Salir sin guardar", "discard", None),
                      ("Seguir aquí", None, None)])


class LearnedTermsDialog(QDialog):
    """Ver/añadir/quitar los términos que el fallback de IA ha aprendido a
    quitar de los nombres (core/learned_terms.py)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Términos aprendidos")
        self.setModal(True)
        self.resize(420, 420)
        lay = QVBoxLayout(self)
        d = QLabel("Términos que la IA identificó como ruido técnico y que\n"
                   "ya se reconocen solos, sin volver a consultarla.")
        d.setStyleSheet(f"color: {theme.PENDING_COLOR};")
        lay.addWidget(d)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Añadir término manualmente...")
        self.entry.returnPressed.connect(self._add)
        add = QPushButton("+ Añadir")
        add.clicked.connect(self._add)
        row.addWidget(self.entry, 1)
        row.addWidget(add)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        bf = QHBoxLayout()
        rm = QPushButton("✕ Olvidar seleccionado")
        rm.setToolTip("Olvidar este término: la app dejará de quitarlo automáticamente de los nombres de archivo.")
        rm.clicked.connect(self._remove)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        bf.addWidget(rm)
        bf.addStretch(1)
        bf.addWidget(close)
        lay.addLayout(bf)
        self._refresh()

    def _refresh(self):
        from core.learned_terms import load_learned_terms
        self.list.clear()
        terms = load_learned_terms()
        if not terms:
            it = QListWidgetItem("Ningún término aprendido todavía.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        for t in terms:
            self.list.addItem(t)

    def _add(self):
        term = self.entry.text().strip()
        if not term:
            return
        from core.learned_terms import add_learned_terms
        add_learned_terms([term])
        self.entry.clear()
        self._refresh()

    def _remove(self):
        it = self.list.currentItem()
        if it is None or not (it.flags() & Qt.ItemIsSelectable):
            return
        from core.learned_terms import remove_learned_term
        remove_learned_term(it.text())
        self._refresh()


_GUIDE_VARS = [("{serie}", "Nombre de la serie o película", "Breaking Bad"),
               ("{titulo}", "Título del episodio", "One Minute"),
               ("{temporada}", "Número de temporada", "3"),
               ("{episodio}", "Número de episodio", "7"),
               ("{año}", "Año de estreno", "2008"),
               ("{ext}", "Extensión (incluye el punto)", ".mkv")]
_GUIDE_FORMATS = [("{episodio:02d}", "Mínimo 2 dígitos", "07"),
                  ("{episodio:03d}", "Mínimo 3 dígitos", "007"),
                  ("{episodio:04d}", "Mínimo 4 dígitos", "0007"),
                  ("{temporada:02d}", "Temporada 2 díg.", "03")]
_GUIDE_EXAMPLES = [
    ("Ejemplos - TV", [("{serie} {temporada}x{episodio:02d} {titulo}{ext}", "Breaking Bad 3x07 One Minute.mkv"),
                       ("{serie} S{temporada:02d}E{episodio:02d} {titulo}{ext}",
                        "Breaking Bad S03E07 One Minute.mkv")]),
    ("Ejemplos - Películas", [("{serie} ({año}){ext}", "The Dark Knight (2008).mkv"),
                              ("{año} - {serie}{ext}", "2008 - The Dark Knight.mkv")]),
    ("Ejemplos - Anime", [("{serie} {temporada}x{episodio:03d} {titulo}{ext}", "One Piece 1x078 Nami.mkv"),
                          ("{serie} - {episodio:04d}{ext}", "One Piece - 1078.mkv")]),
]


def template_guide_html() -> str:
    a, p = theme.ACCENT, theme.PENDING_COLOR

    def section(title):
        return f"<h3 style='margin-top:14px; border-bottom:1px solid {a}'>{title}</h3>"

    def rows(items):
        out = "<table cellspacing='4'>"
        for var, desc, ex in items:
            out += (f"<tr><td width='170'><code style='color:{a}'>{var}</code></td>"
                    f"<td>{desc}<br><span style='color:{p}'>&nbsp;&nbsp;ej: {ex}</span></td></tr>")
        return out + "</table>"

    html = section("Variables disponibles") + rows(_GUIDE_VARS)
    html += section("Formato de números") + rows(_GUIDE_FORMATS)
    for title, examples in _GUIDE_EXAMPLES:
        html += section(title)
        for tpl, res in examples:
            html += (f"<p style='margin:4px 0'><code style='color:{a}'>{tpl}</code><br>"
                     f"<code style='color:{p}'>&nbsp;&nbsp;-&gt; {res}</code></p>")
    return html


class TemplateGuideDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Guía de plantillas")
        self.resize(700, 520)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setHtml(template_guide_html())
        lay.addWidget(tb)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)
