"""
aIBechos — punto de entrada.
Ejecutar: python main.py
"""

import sys
import os
import time as _time

# Momento más temprano posible al que se puede poner un reloj desde
# Python -- todo lo que pasó antes (arranque del intérprete, y si es el
# .exe compilado, el propio extractor de PyInstaller) queda fuera de
# esta medición, pero todo lo de DESPUÉS sí queda cubierto. Instrumentación
# temporal para localizar dónde se va el tiempo de arranque -- ver
# también las líneas "Arranque:" dentro de gui/app.py::App.__init__.
_t_main_start = _time.perf_counter()

# Asegurar que el directorio del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.single_instance import acquire
from core.applog import get_logger

_log = get_logger("aIBechos.main", "app.log")
_log.info("Arranque: import core.single_instance/applog       %6.0f ms",
          (_time.perf_counter() - _t_main_start) * 1000)


def _warn_already_running():
    # En arranque automático (--minimized, vía inicio de sesión) no tiene
    # sentido molestar con un diálogo si por lo que sea ya hay una instancia
    # corriendo -- simplemente no se abre una segunda.
    if "--minimized" in sys.argv:
        return
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showwarning(
        "aIBechos ya está en ejecución",
        "Ya hay una instancia de aIBechos abierta.\n\n"
        "Búscala en la bandeja del sistema (junto al reloj) si no ves la ventana.")
    root.destroy()


def main():
    t_acquire0 = _time.perf_counter()
    if not acquire():
        _warn_already_running()
        return
    _log.info("Arranque: acquire()                                %6.0f ms",
              (_time.perf_counter() - t_acquire0) * 1000)

    t_import0 = _time.perf_counter()
    from gui.app import App
    _log.info("Arranque: from gui.app import App (customtkinter,  "
              "PIL, requests, tkinterdnd2...) %6.0f ms",
              (_time.perf_counter() - t_import0) * 1000)

    t_construct0 = _time.perf_counter()
    app = App()
    _log.info("Arranque: App() TOTAL (ver detalle en las líneas   "
              "anteriores)                    %6.0f ms",
              (_time.perf_counter() - t_construct0) * 1000)
    _log.info("Arranque: TOTAL desde el inicio de main.py hasta   "
              "App() lista para mainloop()    %6.0f ms",
              (_time.perf_counter() - _t_main_start) * 1000)
    app.mainloop()


if __name__ == "__main__":
    main()
