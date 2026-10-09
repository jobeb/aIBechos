"""
Diálogos de la interfaz Qt -- equivalen a los CTkToplevel de gui/app.py
(_ConfirmDialog, la plantilla aMule por serie...).
"""

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout)

from gui_qt import theme


def confirm(parent, title: str, heading: str, body: str = "",
            confirm_text: str = "Aceptar", cancel_text: str = "Cancelar", danger: bool = False) -> bool:
    """Confirmación modal (equivalente a _ConfirmDialog). True si se acepta."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setText(heading)
    if body:
        box.setInformativeText(body)
    ok = box.addButton(confirm_text, QMessageBox.AcceptRole)
    box.addButton(cancel_text, QMessageBox.RejectRole)
    if danger:
        ok.setProperty("danger", True)
    box.exec()
    return box.clickedButton() is ok


class AmuleTemplateDialog(QDialog):
    """Plantilla de búsqueda en aMule para UNA serie (config
    series_search_patterns[serie]) -- mismo comportamiento que
    App._configure_amule_template: Guardar / Borrar / Cancelar, con vista
    previa en vivo y claves sin distinguir mayúsculas."""

    def __init__(self, parent, config, series_name: str):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Template aMule por serie")
        templates = dict(config.get("series_search_patterns", {}) or {})
        current = templates.get(series_name)
        if current is None:
            low = series_name.strip().lower()
            for k, v in templates.items():
                if k.strip().lower() == low:
                    current, series_name = v, k   # conservar la clave original
                    break
        self.series_name = series_name

        lay = QVBoxLayout(self)
        title = QLabel(f"Serie: {series_name}")
        title.setStyleSheet("font-weight: bold;")
        title.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(QLabel("Template de búsqueda en aMule:"))
        self.entry = QLineEdit(current or "")
        self.entry.setMinimumWidth(420)
        lay.addWidget(self.entry)
        help_lbl = QLabel("Vars: {serie} {temporada} {temporada:02d} {episodio} {episodio:02d} {año}\n"
                          "Si no usas {temporada}/{episodio}, se añade \" {temporada}x{episodio:02d}\" solo.\n"
                          "Ej: \"Slime {temporada}x{episodio:02d}\" → \"Slime 2x01\"")
        help_lbl.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
        lay.addWidget(help_lbl)
        self.preview = QLabel()
        self.preview.setStyleSheet(f"color: {theme.ACCENT}; font-size: 9pt;")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        self.entry.textChanged.connect(self._update_preview)
        self._update_preview()

        row = QHBoxLayout()
        save = QPushButton("Guardar")
        save.setProperty("accent", True)
        save.clicked.connect(self._save)
        clear = QPushButton("Borrar")
        clear.setProperty("danger", True)
        clear.clicked.connect(self._clear)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        for b in (save, clear, cancel):
            row.addWidget(b)
        lay.addLayout(row)

    def _update_preview(self):
        tmpl = self.entry.text().strip() or f"{self.series_name} {{temporada}}x{{episodio:02d}}"
        try:
            from core.amule_search import build_amule_query
            q = build_amule_query(self.series_name, 2, 1, templates={self.series_name: tmpl})
            self.preview.setText(f"Vista previa (2x01): {q}")
        except Exception:
            self.preview.setText("")

    def _patterns_without_variants(self) -> dict:
        pats = dict(self.config.get("series_search_patterns", {}) or {})
        low = self.series_name.strip().lower()
        for k in list(pats):
            if k.strip().lower() == low and k != self.series_name:
                del pats[k]
        return pats

    def _save(self):
        new_tmpl = self.entry.text().strip()
        pats = self._patterns_without_variants()
        if new_tmpl:
            pats[self.series_name] = new_tmpl
        else:
            pats.pop(self.series_name, None)
        self.config.set("series_search_patterns", pats)
        self.config.save()
        self.accept()

    def _clear(self):
        pats = self._patterns_without_variants()
        pats.pop(self.series_name, None)
        self.config.set("series_search_patterns", pats)
        self.config.save()
        self.accept()


class DubHiddenDialog(QDialog):
    """Series que "Ocultar sin doblaje ES" deja sin nada visible -- para
    auditar un veredicto de doblaje equivocado que las esconde sin rastro
    (caso real: Kung Fu Panda). 🔄 vuelve a comprobar el doblaje de esa serie
    (solo el lado de las fuentes; un veredicto de la IA se corrige
    volviendo a preguntarle a mano)."""

    def __init__(self, parent, hidden: list, on_recheck):
        super().__init__(parent)
        from PySide6.QtWidgets import QFrame, QScrollArea, QWidget
        self.setWindowTitle("Series ocultas por doblaje")
        self.resize(500, 440)
        lay = QVBoxLayout(self)
        plural = "s" if len(hidden) != 1 else ""
        title = QLabel(f"{len(hidden)} serie{plural} con hueco real, ocultada{plural} por "
                       f"\"Ocultar sin doblaje ES\"")
        title.setStyleSheet("font-weight: bold; font-size: 11pt;")
        title.setWordWrap(True)
        lay.addWidget(title)
        hint = QLabel("Búscalas con el interruptor desactivado si crees que el doblaje indicado no es correcto.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.PENDING_COLOR};")
        lay.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        blay = QVBoxLayout(body)
        for r in hidden:
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            top = QHBoxLayout()
            name = QLabel(r["name"])
            name.setStyleSheet("font-weight: bold;")
            top.addWidget(name, 1)
            ai_verdict = r.get("ai_verdict") or {}
            if "doblaje_castellano" not in ai_verdict:
                btn = QPushButton("🔄")
                btn.setToolTip("Volver a comprobar el doblaje castellano de esta serie")
                btn.setFixedWidth(36)
                btn.clicked.connect(lambda _=False, tid=r["tmdb_id"]: (self.accept(), on_recheck(tid)))
                top.addWidget(btn)
            cl.addLayout(top)
            motivo = ai_verdict.get("motivo", "")
            fuente = "según la IA" if "doblaje_castellano" in ai_verdict else "según las fuentes de doblaje"
            detail = QLabel(fuente + (f": {motivo}" if motivo else ""))
            detail.setWordWrap(True)
            detail.setStyleSheet(f"color: {theme.PENDING_COLOR}; font-size: 9pt;")
            cl.addWidget(detail)
            blay.addWidget(card)
        blay.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        lay.addWidget(close)
