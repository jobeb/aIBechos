"""
Fábrica compartida de loggers rotativos (2 MB × 2 archivos) hacia la
carpeta de datos de la app. Antes cada módulo (auto_watcher.py,
ai_title_fallback.py) repetía la misma configuración de RotatingFileHandler
-- esto la centraliza para no triplicarla al añadir el log del modo manual.

El handler se comparte por FICHERO, no por nombre de logger (ver _handlers).
Esto no es un detalle de eficiencia: en Windows, RotatingFileHandler rota con
os.rename, y renombrar un fichero que otro handler tiene abierto falla con
WinError 32. Como "app.log" lo usan tres loggers distintos (aIBechos.gui,
aIBechos.comicvine, aIBechos.duplicate_detect), con un handler cada uno la
rotación fallaba SIEMPRE en caliente: el error se tragaba en handleError() --
que en una app gráfica no lo ve nadie -- y a partir de los 2 MB la app dejaba
de registrar absolutamente todo hasta el siguiente reinicio, que era el único
momento en que un handler estaba solo con el fichero y podía rotar. Medido en
una reproducción: 2115 líneas perdidas y app.log clavado en 2 MB sin generar
app.log.1. Es lo que hizo que las subidas de un usuario "no aparecieran" en el
log y pareciera un fallo del registro de subidas.
"""

import logging
import threading
from logging.handlers import RotatingFileHandler

from core.appdirs import app_data_dir
from core.version import __version__

# {nombre de fichero: handler} -- un único handler abierto por fichero de log,
# compartido por todos los loggers que escriban en él.
_handlers: dict[str, RotatingFileHandler] = {}
_lock = threading.Lock()


def get_logger(name: str, filename: str, level: int = logging.INFO) -> logging.Logger:
    """Crea (o reutiliza si ya existe) un logger con handler rotativo hacia
    app_data_dir()/filename. Idempotente: si ya tiene handlers (segunda
    instancia, o se llama más de una vez), no los duplica."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    with _lock:
        fh = _handlers.get(filename)
        primer_logger_del_fichero = fh is None
        if fh is None:
            fh = RotatingFileHandler(app_data_dir() / filename,
                                     maxBytes=2 * 1024 * 1024, backupCount=2,
                                     encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"))
            _handlers[filename] = fh
        logger.addHandler(fh)
    # El banner va una vez por FICHERO, no una por logger: antes app.log
    # arrancaba con la misma línea de versión repetida tres veces.
    if primer_logger_del_fichero:
        logger.info("=== aIBechos v%s ===", __version__)
    return logger
