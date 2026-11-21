"""Prueba de arranque del ejecutable ya compilado (`aIBechos --smoke-test`).

La corre el CI de la release sobre el .exe/.app/binario de PyInstaller,
antes de publicarlo: importa todo lo que la app necesita al abrirse y lo
que se importa tarde (dentro de funciones, invisible para PyInstaller),
crea la QApplication con el plugin de plataforma de verdad y sale. Si
falta algo (keyring, paramiko, un plugin Qt...), el código de salida es 1
y la release no se publica: antes eso solo se veía cuando un usuario
abría la app y no pasaba nada.

No toca datos del usuario: ni configuración, ni sesión, ni AutoWatcher.
El resultado va también a un archivo si se da AIBECHOS_SMOKE_OUT (el .exe
de Windows no tiene consola donde escribir)."""

import importlib
import os
import sys
import traceback

#: Módulos que se importan dentro de funciones (no al arrancar) y que,
#: si PyInstaller no los empaqueta, solo fallan al usarlos.
LATE_IMPORTS = ("keyring", "paramiko", "py7zr", "rarfile", "requests", "PIL",
                "core.self_update", "core.update_check")

#: La interfaz entera: cada pestaña importa sus módulos de core/.
UI_MODULES = ("gui_qt.app", "gui_qt.main_window", "gui_qt.core_host", "gui_qt.context",
              "gui_qt.files.tab", "gui_qt.downloads.tab", "gui_qt.movies.tab",
              "gui_qt.history.tab", "gui_qt.cleanup.tab", "gui_qt.protected.tab",
              "gui_qt.stats.tab", "gui_qt.watch_sync.tab", "gui_qt.settings.tab",
              "gui_qt.missing_episodes.tab")

#: Opcionales: si no están, la app funciona sin esa parte.
OPTIONAL = ("PIL",)


def run() -> int:
    lines, failed = [], False
    for name in LATE_IMPORTS + UI_MODULES:
        try:
            importlib.import_module(name)
            lines.append(f"ok   {name}")
        except Exception as e:
            if name in OPTIONAL:
                lines.append(f"opt  {name}: {e}")
                continue
            failed = True
            lines.append(f"FAIL {name}: {e!r}")
            lines.append(traceback.format_exc())
    try:
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([sys.argv[0], "--smoke-test"])
        w = QWidget()
        w.show()
        app.processEvents()
        w.close()
        lines.append(f"ok   QApplication ({app.platformName()})")
    except Exception as e:
        failed = True
        lines.append(f"FAIL QApplication: {e!r}")
    try:
        import paramiko
        paramiko.SSHClient()   # arrastra cryptography: falla si no se empaquetó
        lines.append("ok   paramiko.SSHClient")
    except Exception as e:
        failed = True
        lines.append(f"FAIL paramiko.SSHClient: {e!r}")
    lines.append("RESULT " + ("FAIL" if failed else "OK"))
    report = "\n".join(lines)
    try:
        print(report)
    except Exception:
        pass   # .exe sin consola
    out = os.environ.get("AIBECHOS_SMOKE_OUT")
    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(report + "\n")
        except OSError:
            pass
    return 1 if failed else 0
