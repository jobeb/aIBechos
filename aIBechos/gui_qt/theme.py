"""
Tema oscuro de la interfaz Qt: estilo Fusion + paleta oscura + una hoja QSS
corta, con los colores de acento/estado de la app (ACCENT, ERROR_COLOR,
ICON_* ...). El acento es el amarillo de la web de solicitudes (#f2c230).
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#f2c230"
ACCENT_HOVER = "#ffd659"
ACCENT_INK = "#1d1600"
ERROR_COLOR = "#e74c3c"
WARNING_COLOR = "#f39c12"
SUCCESS_COLOR = "#2ecc71"
PENDING_COLOR = "#95a5a6"
QUEUED_COLOR = "#3498db"

# Botones de icono con color de fondo (solo el tono oscuro de cada par Tk).
ICON_COPY = "#2B4A8A"
ICON_AMULE = "#6e3aa1"
ICON_IGNORE = "#8E2A1F"
ICON_RESTORE = "#1d7a45"
ICON_LINKS = "#5f6b6d"
ICON_DL_IDLE = "#155a8a"
ICON_DL_BUSY = "#b85c12"
ICON_DL_OK = "#147a3d"
ICON_DL_ALREADY = "#9c6e0e"
ICON_DL_FAIL = "#8a1f16"
ICON_NEUTRAL = "#3a3f44"

SELECTED_ROW = "#4a3d20"

BG = "#1e1f22"
BG_ALT = "#26272b"
PANEL = "#2b2d31"
BORDER = "#3a3c42"
TEXT = "#e6e6e6"

TONE_COLORS = {"pending": PENDING_COLOR, "error": ERROR_COLOR,
               "warning": WARNING_COLOR, "success": SUCCESS_COLOR}

_QSS = f"""
QWidget {{ font-size: 10pt; }}
QToolTip {{ background: #2b2b2b; color: #f0f0f0; border: 1px solid {BORDER}; padding: 5px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: {PANEL}; color: {TEXT}; padding: 7px 16px; border: 1px solid {BORDER};
               border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: {ACCENT_INK}; font-weight: bold; }}
QTabBar::tab:hover:!selected {{ background: #35373c; }}
QPushButton {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 12px; color: {TEXT}; }}
QPushButton:hover {{ background: #35373c; }}
QPushButton:disabled {{ color: #6b6f75; }}
QPushButton[accent="true"] {{ background: {ACCENT}; color: {ACCENT_INK}; border: none; font-weight: bold; }}
QPushButton[accent="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[danger="true"] {{ background: #c0392b; border: none; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {BG_ALT}; border: 1px solid {BORDER};
               border-radius: 6px; padding: 4px 6px; color: {TEXT}; }}
QTreeView, QTableView {{ background: {BG}; alternate-background-color: {BG_ALT}; border: 1px solid {BORDER};
               border-radius: 6px; selection-background-color: {SELECTED_ROW}; selection-color: {TEXT}; }}
QTreeView::item {{ padding: 2px 0; }}
QHeaderView::section {{ background: {PANEL}; color: {TEXT}; border: none; border-right: 1px solid {BORDER};
               padding: 5px 6px; font-weight: bold; }}
QProgressBar {{ background: {BG_ALT}; border: 1px solid {BORDER}; border-radius: 5px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #6b6f75; border-radius: 3px; background: {BG_ALT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QStatusBar {{ background: {PANEL}; }}
QFrame#card {{ background: {PANEL}; border-radius: 8px; }}
QScrollArea {{ border: none; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(BG))
    pal.setColor(QPalette.AlternateBase, QColor(BG_ALT))
    pal.setColor(QPalette.ToolTipBase, QColor("#2b2b2b"))
    pal.setColor(QPalette.ToolTipText, QColor("#f0f0f0"))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(PANEL))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.BrightText, QColor(ERROR_COLOR))
    pal.setColor(QPalette.Link, QColor(ACCENT))
    pal.setColor(QPalette.Highlight, QColor(SELECTED_ROW))
    pal.setColor(QPalette.HighlightedText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor("#7d8187"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#6b6f75"))
    app.setPalette(pal)
    app.setStyleSheet(_QSS)
