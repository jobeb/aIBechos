"""Escritura atómica de JSON en disco.

`Path.write_text` trunca primero y escribe después: si el proceso muere
entre medias (cierre brusco, apagón, matar la app), el fichero queda a
medias y la siguiente lectura lo ve corrupto/vacío -- que es exactamente
"se ha borrado el historial", y el siguiente guardado lo hace permanente
al partir de esa lectura vacía.

Escribiendo a un temporal del mismo directorio y moviéndolo con
`os.replace` (atómico en Windows y POSIX dentro del mismo filesystem),
el fichero visible o está entero el viejo o entero el nuevo, nunca a
medias. Se usa para upload_history.json y deletion_history.json, que son
fuente de verdad local (la pestaña Historial no se puede reconstruir
desde el FTP).
"""

import json
import os
from pathlib import Path


def write_json_atomic(path, data) -> None:
    """Guarda *data* como JSON en *path* de forma atómica. Lanza excepción
    si falla (el llamador decide si tragarla); no deja el destino a medias:
    ante cualquier error el fichero original sigue intacto."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # No dejar huérfanos .tmp-PID por el directorio en cada guardado
        # fallido (ver tests/test_session_store.py::test_save_never_leaves_half_file).
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
