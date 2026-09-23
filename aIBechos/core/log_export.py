"""Qué ficheros entran en el "Descargar log completo" de Historial.

Se recogen por patrón (*.log*) y no por lista fija de nombres: la lista
fija se dejaba fuera los backups de la rotación (app.log.1, app.log.2 --
ver core/applog.py) y los logs añadidos después (update_check.log,
active_poll.log), y del zip enviado faltaba justo lo necesario para
diagnosticar. Este módulo existe para fijar ese criterio con tests.
"""

from pathlib import Path


def collect_log_files(data_dir) -> list:
    """Ficheros de log del raíz de *data_dir*, ordenados por nombre. Solo
    el raíz (los loggers de core/applog.py y active_poll.log escriben
    todos ahí, sin subcarpetas) e incluyendo rotaciones (*.log.1...)."""
    data_dir = Path(data_dir)
    try:
        return sorted((p for p in data_dir.glob("*.log*") if p.is_file()),
                      key=lambda p: p.name)
    except Exception:
        return []
