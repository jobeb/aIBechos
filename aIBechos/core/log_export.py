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


#: Logs que escribían versiones antiguas y ya nada escribe (el sondeo de
#: descargas activas y la depuración del protocolo EC de aMule): se
#: quedaban para siempre en la carpeta de datos (3 MB cada uno) y se
#: colaban en el zip de "Descargar log completo".
OBSOLETE_LOGS = ("active_poll.log", "ec_debug.log")


def remove_obsolete_logs(data_dir) -> list:
    """Borra de *data_dir* los OBSOLETE_LOGS (y sus rotaciones) que haya;
    devuelve los nombres borrados. Nunca lanza."""
    removed = []
    data_dir = Path(data_dir)
    for name in OBSOLETE_LOGS:
        try:
            paths = [p for p in data_dir.glob(name + "*") if p.is_file()]
        except Exception:
            continue
        for p in paths:
            try:
                p.unlink()
                removed.append(p.name)
            except Exception:
                pass
    return sorted(removed)
