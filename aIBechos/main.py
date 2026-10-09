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
    if not acquire():
        from gui_qt.app import warn_already_running
        warn_already_running()
        return
    from gui_qt.app import run
    sys.exit(run())


if __name__ == "__main__":
    main()
