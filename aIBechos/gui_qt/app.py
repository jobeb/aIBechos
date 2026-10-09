"""Arranque de la interfaz (PySide6), llamado desde main.py."""

import sys

from PySide6.QtWidgets import QApplication

from core.applog import get_logger

_log = get_logger("aIBechos.qt", "app.log")


def run(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("aIBechos")
    app.setQuitOnLastWindowClosed(False)   # la bandeja mantiene viva la app

    from gui_qt import bridge, theme
    bridge.install()
    theme.apply_theme(app)

    from gui_qt.context import AppContext
    from gui_qt.main_window import MainWindow
    ctx = AppContext()
    win = MainWindow(ctx, start_minimized="--minimized" in argv)
    app._main_window = win   # mantener la referencia viva
    rc = app.exec()
    # Salida garantizada, igual que App._force_quit en Tk: la sesión y la
    # configuración ya están guardadas (MainWindow.quit_app); si un hilo de
    # subida se quedara colgado en un read de socket, el intérprete no
    # terminaría y retendría el bloqueo de instancia única.
    import logging
    import os
    logging.shutdown()
    os._exit(rc)


def warn_already_running() -> None:
    # En arranque automático (--minimized, al iniciar sesión) no se molesta con
    # un diálogo: simplemente no se abre una segunda instancia.
    if "--minimized" in sys.argv:
        return
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.warning(None, "aIBechos ya está en ejecución",
                        "Ya hay una instancia de aIBechos abierta.\n\n"
                        "Búscala en la bandeja del sistema (junto al reloj) si no ves la ventana.")
    del app
