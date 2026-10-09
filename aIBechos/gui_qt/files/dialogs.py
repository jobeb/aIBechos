"""
Diálogos de la pestaña Archivos en Qt -- mismos textos, botones y valores de
resultado que los CTkToplevel de gui/app.py, porque la lógica compartida
(core/app_files_core.py) decide según ese .result:

  OverwriteDialog       "overwrite" | "skip" | "all" | "skip_all"
  StaleUploadDialog     "delete_upload" | "no_delete" | "delete_upload_all" | "no_delete_all" | None
  SeriesMatchDialog     "yes" | "no" | None
  RemoveEntryDialog     una opción de core.remote_presence | None
  ConfirmRemovalDialog  True | False
  ClearDialog           "solo_subidos" | "todo" | None

Los .ask(...) se llaman en el hilo de la interfaz y devuelven el diálogo ya
cerrado (con .result), igual que construir el CTkToplevel en Tk.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from core import remote_presence as rp
from gui_qt import theme


class _ChoiceDialog(QDialog):
    """Diálogo modal de texto + fila(s) de botones; cada botón cierra con su
    resultado. Cerrar con la X devuelve *close_result*."""

    def __init__(self, parent, title: str, close_result=None, width: int = 440):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.result = close_result
        self._close_result = close_result
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(22, 18, 22, 18)
        self._lay.setSpacing(8)

    def label(self, text: str, bold=False, size=None, color=None, selectable=False):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        css = []
        if bold:
            css.append("font-weight: bold;")
        if size:
            css.append(f"font-size: {size}pt;")
        if color:
            css.append(f"color: {color};")
        lbl.setStyleSheet(" ".join(css))
        if selectable:
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lay.addWidget(lbl)
        return lbl

    def buttons(self, specs: list):
        """specs: [(texto, resultado, estilo)] -- estilo: "accent"/"danger"/None."""
        row = QHBoxLayout()
        row.addStretch(1)
        for text, result, style in specs:
            b = QPushButton(text)
            if style == "accent":
                b.setProperty("accent", True)
            elif style == "danger":
                b.setProperty("danger", True)
            b.clicked.connect(lambda _=False, r=result: self._close(r))
            row.addWidget(b)
        row.addStretch(1)
        self._lay.addLayout(row)

    def _close(self, result):
        self.result = result
        self.accept()

    def reject(self):
        self.result = self._close_result
        super().reject()

    @classmethod
    def ask(cls, parent, *args, **kwargs):
        dlg = cls(parent, *args, **kwargs)
        dlg.exec()
        return dlg


class OverwriteDialog(_ChoiceDialog):
    def __init__(self, parent, filename: str, title="Archivo ya existe",
                 message="El archivo ya existe en el servidor:", overwrite_label="Sobrescribir",
                 all_label="Sobrescribir todos", skip_all_label="Omitir todos", close_result="skip",
                 show_all_button=True, show_skip_all_button=False):
        super().__init__(parent, title, close_result=close_result, width=420)
        self.label(message, size=11)
        self.label(filename, color=theme.PENDING_COLOR, selectable=True)
        specs = [(overwrite_label, "overwrite", "accent"), ("Omitir", "skip", None)]
        if show_all_button:
            specs.append((all_label, "all", "danger"))
        if show_skip_all_button:
            specs.append((skip_all_label, "skip_all", None))
        self.buttons(specs)


class StaleUploadDialog(_ChoiceDialog):
    def __init__(self, parent, desired: str, stale: str, show_all_buttons: bool = True):
        super().__init__(parent, "Resto de una subida anterior", close_result=None, width=500)
        self.label("Este archivo ya se subió con otro nombre (identificación anterior).", bold=True)
        self.label(f"Resto en el servidor:\n{stale}\n\nSe va a subir ahora como:\n{desired}",
                   color=theme.PENDING_COLOR, selectable=True)
        self.label("El viejo puede haber quedado a medio subir. ¿Borrarlo del servidor?",
                   color=theme.PENDING_COLOR)
        self.buttons([("Borrar resto y subir", "delete_upload", "danger"),
                      ("No borrar, subir", "no_delete", "accent")])
        if show_all_buttons:
            self.buttons([("Borrar resto y subir (todos)", "delete_upload_all", "danger"),
                          ("No borrar, subir (todos)", "no_delete_all", "accent")])
        self.buttons([("Cancelar la subida", None, None)])


class SeriesMatchDialog(_ChoiceDialog):
    def __init__(self, parent, desired: str, existing: str):
        super().__init__(parent, "¿Es la misma serie?", close_result=None)
        self.label("Ya existe una carpeta con un nombre parecido en el FTP.", bold=True)
        self.label(f"Serie a subir:\n{desired}\n\nCarpeta existente:\n{existing}",
                   color=theme.PENDING_COLOR, selectable=True)
        self.label("¿Es la misma serie? Si respondes que sí, se subirá dentro de esa carpeta.",
                   color=theme.PENDING_COLOR)
        self.buttons([("Sí, usar esa carpeta", "yes", "accent"),
                      ("No, crear carpeta nueva", "no", None)])


class RemoveEntryDialog(_ChoiceDialog):
    _TEXTOS = {
        rp.QUITAR_LISTA: ("Quitar solo de la lista",
                          "El archivo se queda donde está. Solo desaparece de aquí.", None),
        rp.QUITAR_Y_LOCAL: ("Quitar y borrar en local",
                            "Además se borra el archivo de tu disco.", "danger"),
        rp.QUITAR_LOCAL_Y_REMOTO: ("Quitar y borrar en local y en el servidor",
                                   "Además se borra de tu disco Y del servidor.", "danger"),
    }

    def __init__(self, parent, name: str, opciones: list, local_path: str, remote_path: str):
        super().__init__(parent, "Quitar archivo", close_result=None, width=480)
        self.label("¿Qué quieres hacer con este archivo?", bold=True, size=11)
        self.label(name, bold=True, selectable=True)
        for opt in opciones:
            etiqueta, detalle, style = self._TEXTOS[opt]
            b = QPushButton(etiqueta)
            if style:
                b.setProperty(style, True)
            b.clicked.connect(lambda _=False, o=opt: self._close(o))
            self._lay.addWidget(b)
            self.label(detalle, color=theme.PENDING_COLOR, size=9)
        faltan = []
        if rp.QUITAR_Y_LOCAL not in opciones:
            faltan.append("El archivo ya no está en tu disco.")
        elif rp.QUITAR_LOCAL_Y_REMOTO not in opciones:
            faltan.append("No consta subido al servidor (o no se sabe dónde quedó).")
        if faltan:
            self.label(" ".join(faltan), color="#7f8c8d", size=9)
        self.buttons([("Cancelar", None, None)])


class ConfirmRemovalDialog(_ChoiceDialog):
    def __init__(self, parent, name: str, local_path: str, remote_path: str):
        super().__init__(parent, "Confirmar borrado", close_result=False, width=480)
        self.label("Se va a borrar de verdad", bold=True, size=11)
        self.label(name, bold=True, selectable=True)
        if local_path:
            self.label("En tu disco:", color=theme.PENDING_COLOR, size=9)
            self.label(local_path, selectable=True)
        if remote_path:
            self.label("En el servidor:", color=theme.PENDING_COLOR, size=9)
            self.label(remote_path, selectable=True)
        self.label("Esta acción NO se puede deshacer.", bold=True, color=theme.ERROR_COLOR)
        self.buttons([("Cancelar", False, None), ("Sí, borrar", True, "danger")])


class ClearDialog(_ChoiceDialog):
    def __init__(self, parent, pendientes: int):
        super().__init__(parent, "Limpiar lista", close_result=None, width=420)
        plural = "s" if pendientes != 1 else ""
        self.label("Hay una subida FTP en progreso.", bold=True)
        self.label(f"Aún queda{'n' if pendientes != 1 else ''} {pendientes} archivo{plural} por subir. "
                   "¿Qué quieres hacer?", color=theme.PENDING_COLOR)
        self.buttons([("Limpiar solo subidos", "solo_subidos", None),
                      ("Limpiar todo y cancelar", "todo", "danger")])


class SameSeriesDialog(_ChoiceDialog):
    """+ Carpeta con 2+ libros/cómics: ¿misma serie? (cerrar = uno a uno)."""

    def __init__(self, parent, n: int):
        super().__init__(parent, "¿Misma serie o colección?", close_result=False)
        self.label(f"Se han detectado {n} archivos de libro/cómic en esta carpeta.\n"
                   "¿Pertenecen todos a la misma serie o colección?", bold=True)
        self.buttons([("Sí, son la misma serie", True, "accent"),
                      ("No, identificar cada uno", False, None)])


class EditDetectedDialog(QDialog):
    """Corregir a mano título/temporada/episodio detectados (escribe en
    entry.detected, igual que App._edit_detected)."""

    def __init__(self, parent, entry):
        super().__init__(parent)
        self.setWindowTitle("Editar título/episodio")
        self.entry = entry
        det = entry.detected
        lay = QVBoxLayout(self)
        t = QLabel(f"Detectado a mano para:\n{entry.name}")
        t.setStyleSheet("font-weight: bold;")
        t.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(QLabel("Título"))
        self.title = QLineEdit(det.get("title", "") or "")
        self.title.setMinimumWidth(380)
        lay.addWidget(self.title)
        self.season = None
        if not entry.is_book:
            lay.addWidget(QLabel("Temporada"))
            self.season = QLineEdit("" if det.get("season") is None else str(det["season"]))
            lay.addWidget(self.season)
        lay.addWidget(QLabel("Capítulo" if entry.is_comic else "Episodio"))
        self.episode = QLineEdit("" if det.get("episode") is None else str(det["episode"]))
        lay.addWidget(self.episode)
        row = QHBoxLayout()
        save = QPushButton("Guardar")
        save.setProperty("accent", True)
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(save)
        row.addWidget(cancel)
        lay.addLayout(row)

    @staticmethod
    def _int_or_none(text: str):
        text = text.strip()
        return int(text) if text.isdigit() else None

    def _save(self):
        det = self.entry.detected
        det["title"] = self.title.text().strip()
        det["episode"] = self._int_or_none(self.episode.text())
        if self.season is not None:
            det["season"] = self._int_or_none(self.season.text())
        self.accept()


class EditRemoteDirDialog(QDialog):
    """Fijar a mano la carpeta remota de destino (entry.remote_dir_override)."""

    def __init__(self, parent, entry, auto_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Carpeta de destino")
        self.entry = entry
        lay = QVBoxLayout(self)
        t = QLabel(f"Carpeta remota para:\n{entry.name}")
        t.setStyleSheet("font-weight: bold;")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.path = QLineEdit(entry.remote_dir_override or auto_dir)
        self.path.setMinimumWidth(380)
        lay.addWidget(self.path)
        if entry.remote_dir_override:
            hint = QLabel(f"Automático (por categoría): {auto_dir or '—'}")
            hint.setStyleSheet(f"color: {theme.PENDING_COLOR};")
            hint.setWordWrap(True)
            lay.addWidget(hint)
        row = QHBoxLayout()
        save = QPushButton("Guardar")
        save.setProperty("accent", True)
        save.clicked.connect(self._save)
        auto = QPushButton("Usar automático")
        auto.clicked.connect(self._use_auto)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        for b in (save, auto, cancel):
            row.addWidget(b)
        lay.addLayout(row)

    def _save(self):
        self.entry.remote_dir_override = self.path.text().strip() or None
        self.accept()

    def _use_auto(self):
        self.entry.remote_dir_override = None
        self.accept()


class ConfirmDeleteDialog(_ChoiceDialog):
    """Antes de borrar algo DEL SERVIDOR: una vez por elemento, con ruta
    exacta, tamaño y motivo (ver _ConfirmDeleteDialog de la versión Tk)."""

    def __init__(self, parent, name: str, ftp_path: str, size_bytes: int, reason: str):
        super().__init__(parent, "Confirmar borrado", close_result=False, width=470)
        from core.fmt import fmt_size
        self.label("¿Eliminar esto del servidor?", bold=True, size=11)
        self.label(name, bold=True, selectable=True)
        self.label(ftp_path, color=theme.PENDING_COLOR, selectable=True)
        self.label(f"{fmt_size(size_bytes)} -- {reason}")
        self.label("Esta acción NO se puede deshacer.", bold=True, color=theme.ERROR_COLOR)
        self.buttons([("Cancelar", False, None), ("Sí, eliminar", True, "danger")])
