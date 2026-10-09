"""
Columna de botones de acción dibujados, no widgets (compartida por todas las
tablas Qt): cada fila declara sus
acciones en ActionsRole (lista de Action) y este delegado las pinta como
"pastillas" de color con su icono, detecta el clic sobre cada una y muestra
su tooltip. Son píxeles, no ventanas nativas: da igual que haya 10 o 10.000
filas, no se crea ningún objeto del sistema por botón (el origen del cuelgue
de la versión Tk).
"""

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QToolTip

#: Rol con la lista de acciones (list[Action]) de una celda.
ActionsRole = Qt.UserRole + 2


@dataclass
class Action:
    id: str
    glyph: str
    color: str
    tooltip: str
    enabled: bool = True


CHIP_W = 28
CHIP_H = 22
CHIP_GAP = 4
LEFT_PAD = 4


class ActionsDelegate(QStyledItemDelegate):
    actionTriggered = Signal(object, str)   # (QModelIndex, id de la acción)

    def _chip_rects(self, option_rect: QRect, n: int) -> list:
        y = option_rect.top() + max(0, (option_rect.height() - CHIP_H) // 2)
        x = option_rect.left() + LEFT_PAD
        return [QRect(x + i * (CHIP_W + CHIP_GAP), y, CHIP_W, CHIP_H) for i in range(n)]

    def paint(self, painter: QPainter, option, index):
        actions = index.data(ActionsRole)
        if not actions:
            super().paint(painter, option, index)
            return
        # Fondo de selección/alternado como el resto de la fila.
        style = option.widget.style() if option.widget else None
        if style is not None:
            opt = type(option)(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, painter, option.widget)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        hover_pos = None
        if option.state & QStyle.State_MouseOver and option.widget is not None:
            vp = option.widget.viewport() if hasattr(option.widget, "viewport") else option.widget
            hover_pos = vp.mapFromGlobal(QCursor.pos())
        for rect, act in zip(self._chip_rects(option.rect, len(actions)), actions):
            color = QColor(act.color)
            if not act.enabled:
                color.setAlpha(70)
            elif hover_pos is not None and rect.contains(hover_pos):
                color = color.lighter(135)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QColor("#f2f2f2") if act.enabled else QColor("#8a8a8a"))
            painter.drawText(rect, Qt.AlignCenter, act.glyph)
        painter.restore()

    def sizeHint(self, option, index):
        actions = index.data(ActionsRole) or []
        base = super().sizeHint(option, index)
        width = LEFT_PAD + len(actions) * (CHIP_W + CHIP_GAP)
        return QSize(max(base.width(), width), max(base.height(), CHIP_H + 4))

    def _hit(self, option, index, pos):
        actions = index.data(ActionsRole) or []
        for rect, act in zip(self._chip_rects(option.rect, len(actions)), actions):
            if rect.contains(pos):
                return act
        return None

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            act = self._hit(option, index, event.position().toPoint())
            if act is not None:
                if act.enabled:
                    self.actionTriggered.emit(index, act.id)
                return True
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            # Que pulsar una pastilla no despliegue/seleccione la fila.
            if self._hit(option, index, event.position().toPoint()) is not None:
                return True
        return super().editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.ToolTip:
            act = self._hit(option, index, event.pos())
            if act is not None:
                QToolTip.showText(event.globalPos(), act.tooltip, view)
                return True
            QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)
