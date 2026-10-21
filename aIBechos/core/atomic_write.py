"""Escritura atómica de JSON en disco.

`Path.write_text` trunca primero y escribe después: si el proceso muere
entre medias (cierre brusco, apagón, matar la app), el fichero queda a
medias y la siguiente lectura lo ve corrupto/vacío -- que es exactamente
"se ha borrado el historial", y el siguiente guardado lo hace permanente
al partir de esa lectura vacía.

Escribiendo a un temporal del mismo directorio y moviéndolo con
`os.replace` (atómico en Windows y POSIX dentro del mismo filesystem),
el fichero visible o está entero el viejo o entero el nuevo, nunca a
medias. Se usa para config.json, el historial, la sesión y el resto de
JSON de la carpeta de datos.
"""

import itertools
import json
import os
import threading
import time
from pathlib import Path

_seq = itertools.count()

#: En Windows os.replace falla (PermissionError) si otro proceso tiene el
#: destino abierto en ese instante (antivirus, indexador, otra lectura).
_REPLACE_TRIES = 5
_REPLACE_WAIT_S = 0.05


def write_json_atomic(path, data, indent=2) -> None:
    """Guarda *data* como JSON en *path* de forma atómica. Lanza excepción
    si falla (el llamador decide si tragarla); no deja el destino a medias:
    ante cualquier error el fichero original sigue intacto."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Un temporal por llamada: dos hilos guardando el mismo archivo a la vez
    # no deben escribir en el mismo temporal.
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{next(_seq)}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(_REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == _REPLACE_TRIES - 1:
                    raise
                time.sleep(_REPLACE_WAIT_S)
    except Exception:
        # No dejar huérfanos .tmp-PID por el directorio en cada guardado
        # fallido (ver tests/test_session_store.py::test_save_never_leaves_half_file).
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
