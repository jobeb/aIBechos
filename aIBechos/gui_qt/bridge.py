"""
Puente hilo de trabajo -> hilo de la interfaz.

Qt, igual que tkinter, no permite tocar widgets desde otro hilo. En la versión
Tk eso se resolvía con `self.after(0, lambda: ...)`; aquí el equivalente es
`ui(lambda: ...)`, que se puede llamar desde cualquier hilo: emite una señal
cuyo receptor vive en el hilo principal, así que Qt encola la llamada y la
ejecuta allí (conexión en cola).

Los hilos de trabajo siguen siendo threading.Thread / ThreadPoolExecutor
normales -- no hace falta QThread para nada de esto.
"""

import threading

from PySide6.QtCore import QObject, Qt, Signal

from core.applog import get_logger

_log = get_logger("aIBechos.qt", "app.log")


class _Dispatcher(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except Exception:
            _log.exception("Error en una llamada diferida a la interfaz")


_dispatcher = None


def install():
    """Crear el despachador -- una vez, desde el hilo principal y con la
    QApplication ya construida (el QObject queda ligado a ese hilo)."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _Dispatcher()


def ui(fn):
    """Ejecuta *fn* en el hilo de la interfaz (encolado, nunca en línea --
    también si ya se está en ese hilo, igual que after(0, ...))."""
    _dispatcher.call.emit(fn)


def run_in_thread(target, *args, name: str = None):
    """Lanza *target* en un hilo daemon (atajo del patrón de toda la app)."""
    t = threading.Thread(target=target, args=args, daemon=True, name=name)
    t.start()
    return t
