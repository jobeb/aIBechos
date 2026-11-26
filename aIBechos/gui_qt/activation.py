"""Abrir la app cuando ya está abierta: restaurar la ventana de la que corre.

La instancia en marcha escucha en un QLocalServer (named pipe en Windows,
socket de dominio en macOS/Linux); la segunda, en vez de avisar con un
diálogo de "ya hay una instancia abierta", le pide que muestre su ventana y
termina. Si no hay nadie escuchando (la que corre es de una versión anterior
a esto, o el servidor no pudo arrancar), main.py cae al aviso de siempre.
"""

from __future__ import annotations

import getpass
import re

from core.applog import get_logger

_log = get_logger("aIBechos.qt", "app.log")

_MESSAGE = b"show\n"


def server_name() -> str:
    """Nombre del canal, por usuario: dos sesiones de Windows en el mismo
    equipo no deben restaurarse la ventana la una a la otra."""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return "aIBechos-activar-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "x")


def start_server(on_show, name: str | None = None):
    """Empieza a escuchar; *on_show* se llama en el hilo de la GUI cada vez
    que otra instancia pide mostrarse. Devuelve el QLocalServer (hay que
    mantener la referencia) o None si no se pudo."""
    from PySide6.QtNetwork import QLocalServer
    name = name or server_name()
    server = QLocalServer()
    if not server.listen(name):
        # Resto de un proceso que murió sin cerrarlo (en macOS/Linux queda
        # el fichero del socket): somos la única instancia (tenemos el
        # bloqueo), así que se puede quitar y reintentar.
        QLocalServer.removeServer(name)
        if not server.listen(name):
            _log.warning("Instancia única: no se pudo escuchar en %s (%s)", name, server.errorString())
            return None

    def _on_connection():
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            if sock is None:
                break

            def _read(s=sock):
                if bytes(s.readAll()).strip().startswith(b"show"):
                    _log.info("Instancia única: otra instancia pide mostrar la ventana")
                    try:
                        on_show()
                    except Exception:
                        _log.exception("Instancia única: no se pudo mostrar la ventana")
                s.disconnectFromServer()

            sock.readyRead.connect(_read)
            sock.disconnected.connect(sock.deleteLater)

    server.newConnection.connect(_on_connection)
    return server


def request_show(name: str | None = None, timeout_ms: int = 2000) -> bool:
    """Desde la segunda instancia: pide a la que corre que muestre su
    ventana. True si alguien recibió el mensaje."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    _app = QCoreApplication.instance() or QCoreApplication([])
    _allow_foreground()
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(_MESSAGE)
    ok = sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return bool(ok)


def _allow_foreground() -> None:
    """Windows solo deja pasar al frente a un proceso si lo autoriza el que
    está delante -- que somos nosotros, recién lanzados por el usuario. Sin
    esto la ventana restaurada se quedaba parpadeando en la barra de tareas."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    except Exception:
        pass
