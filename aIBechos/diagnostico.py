"""
Script de diagnóstico - escribe resultados a diagnostico.log
"""
import sys
import traceback
from pathlib import Path

log = Path(__file__).parent / "diagnostico.log"

def write(msg):
    with open(log, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)

log.write_text("", encoding="utf-8")  # limpiar
try:
    from core.version import __version__
    write(f"aIBechos: v{__version__}")
except Exception:
    pass
write(f"Python: {sys.version}")
write(f"Ejecutable: {sys.executable}")
write(f"Ruta del script: {__file__}")
write("")

# Test imports
for mod in ["PySide6", "PySide6.QtWidgets", "requests", "keyring", "paramiko"]:
    try:
        __import__(mod)
        write(f"OK: {mod}")
    except Exception as e:
        write(f"ERROR {mod}: {e}")

write("")
write("Intentando lanzar la app...")

try:
    sys.path.insert(0, str(Path(__file__).parent))
    from gui_qt.app import run
    write("Interfaz importada OK — arrancando")
    run()   # no vuelve: al cerrar termina el proceso (ver gui_qt/app.py)
except Exception as e:
    write(f"ERROR lanzando app:\n{traceback.format_exc()}")
