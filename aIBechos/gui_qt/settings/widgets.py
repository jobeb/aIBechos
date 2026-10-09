"""
Piezas de formulario de Configuración (Qt). Cada página registra sus campos
como {clave de config: función que lee el valor del widget}; collect() da el
mismo dict que App._collect_settings en Tk para esa sub-pestaña, y la página
lo compara con config para saber si hay cambios sin guardar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

from gui_qt import theme


def status_label() -> QLabel:
    lbl = QLabel("")
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setWordWrap(True)
    return lbl


def set_status(lbl: QLabel, text: str, color: str = theme.PENDING_COLOR):
    lbl.setText(text)
    lbl.setStyleSheet(f"color: {color};")


def desc_label(text: str, color: str = theme.PENDING_COLOR) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {color}; font-size: 9pt;")
    return lbl


def hbox(*widgets, stretch=True) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if isinstance(x, str):
            x = desc_label(x)
        lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


class Card(QFrame):
    """Apartado con título, descripción y un formulario etiqueta/campo."""

    def __init__(self, page: "Page", title: str, desc: str | None = None):
        super().__init__()
        self.page = page
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 12)
        t = QLabel(title)
        t.setStyleSheet("font-size: 12pt; font-weight: bold;")
        self.lay.addWidget(t)
        if desc:
            self.lay.addWidget(desc_label(desc))
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.lay.addLayout(self.form)

    def subtitle(self, text: str, desc: str | None = None):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 8px;")
        self.form.addRow(lbl)
        if desc:
            self.form.addRow(desc_label(desc))

    def row(self, label: str | None, widget: QWidget, tip: str | None = None):
        if tip:
            widget.setToolTip(tip)
        if label is None:
            self.form.addRow(widget)
        else:
            self.form.addRow(label, widget)
        return widget

    def note(self, text: str, color: str = theme.PENDING_COLOR):
        self.form.addRow(desc_label(text, color))

    # ── Campos ligados a una clave de config ──

    def text(self, label, key, default="", secret=False, placeholder="", strip=True, tip=None):
        e = QLineEdit(str(self.page.cfg.get(key, default) or ""))
        if secret:
            e.setEchoMode(QLineEdit.Password)
        e.setPlaceholderText(placeholder)
        self.page.bind(key, (lambda: e.text().strip()) if strip else e.text)
        self.row(label, e, tip)
        return e

    def spin(self, label, key, lo, hi, default, tip=None, width=90):
        sb = QSpinBox()
        sb.setRange(lo, hi)
        try:
            sb.setValue(int(self.page.cfg.get(key, default)))
        except (TypeError, ValueError):
            sb.setValue(default)
        sb.setFixedWidth(width)
        self.page.bind(key, sb.value)
        self.row(label, sb, tip)
        return sb

    def combo(self, label, key, values, default, editable=False, tip=None):
        cb = QComboBox()
        cb.addItems([str(v) for v in values])
        cb.setEditable(editable)
        cur = str(self.page.cfg.get(key, default))
        if cb.findText(cur) < 0:
            cb.addItem(cur)
        cb.setCurrentText(cur)
        self.page.bind(key, (lambda: cb.currentText().strip()) if editable else cb.currentText)
        self.row(label, cb, tip)
        return cb

    def switch(self, text, key, default=False, tip=None):
        ch = QCheckBox(text)
        ch.setChecked(bool(self.page.cfg.get(key, default)))
        self.page.bind(key, ch.isChecked)
        self.row(None, ch, tip)
        return ch


class Page(QScrollArea):
    """Sub-pestaña de Configuración: columna con scroll de Cards."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cfg = host.config_data
        self._fields: dict = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.col = QVBoxLayout(inner)
        self.col.setContentsMargins(4, 4, 10, 4)
        self.col.setSpacing(10)
        self.setWidget(inner)
        self.build()
        self.col.addStretch(1)

    def build(self):
        raise NotImplementedError

    def card(self, title: str, desc: str | None = None) -> Card:
        c = Card(self, title, desc)
        self.col.addWidget(c)
        return c

    def bind(self, key: str, getter):
        self._fields[key] = getter

    def collect(self) -> dict:
        return {k: g() for k, g in self._fields.items()}


def int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_spin(lo, hi, value, decimals=1, width=100) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(decimals)
    try:
        sb.setValue(float(value))
    except (TypeError, ValueError):
        sb.setValue(lo)
    sb.setFixedWidth(width)
    return sb


def list_box(lines, height=110, tip=None) -> QPlainTextEdit:
    box = QPlainTextEdit("\n".join(str(x) for x in (lines or [])))
    box.setFixedHeight(height)
    if tip:
        box.setToolTip(tip)
    return box
