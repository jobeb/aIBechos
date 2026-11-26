"""
aIBechos — punto de entrada.
Ejecutar: python main.py            (--minimized: arrancar en la bandeja)
"""

import sys
import os

# Asegurar que el directorio del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.single_instance import acquire


def main():
    if "--smoke-test" in sys.argv:
        # CI de la release: ¿abre el ejecutable compilado? (core/smoke_test.py)
        from core.smoke_test import run as smoke
        sys.exit(smoke())
    if not acquire():
        # Ya hay una abierta: que muestre su ventana. El arranque automático
        # (--minimized, al iniciar sesión) no la saca de la bandeja. Solo si
        # la otra no contesta (versión anterior) se avisa como antes.
        if "--minimized" in sys.argv:
            return
        try:
            from gui_qt.activation import request_show
            if request_show():
                return
        except Exception:
            pass
        from gui_qt.app import warn_already_running
        warn_already_running()
        return
    from gui_qt.app import run
    sys.exit(run())


if __name__ == "__main__":
    main()
