"""Lectura/escritura de session.json (pestaña Archivos).

Caso real (Victor, 2026-09-11): al arrancar, Archivos apareció vacía y solo
volvió tras reiniciar, sin ningún rastro en app.log. Causas posibles (todas
transitorias o de datos): lectura fallida, JSON corrupto, o UNA entrada
inválida que tumbaba la carga entera -- porque _load_session tragaba
cualquier excepción en silencio y dejaba self.files en [].

Por eso esta lectura NUNCA lanza y distingue los casos:
  * fichero inexistente → ([], None): arranque limpio, nada que cargar.
  * JSON válido pero no-lista, o lectura/parse fallidos → ([], "motivo"):
    quien llama lo registra en el log y marca la carga como fallida para
    NO persistir después esa lista vacía encima de datos buenos (ver
    App._save_session), que convertiría un fallo transitorio en pérdida
    permanente.

La escritura es atómica (ver core/atomic_write.py): Path.write_text trunca
primero y escribe después, y morir en medio deja session.json a medias --
la siguiente lectura lo vería corrupto/vacío.
"""

import json
from pathlib import Path

from core.atomic_write import write_json_atomic


def load_session_dicts(path) -> tuple:
    """(lista_de_dicts, error|None) desde session.json. Nunca lanza."""
    try:
        p = Path(path)
        if not p.exists():
            return [], None
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return [], f"no se pudo leer/parsear: {e}"
    if not isinstance(raw, list):
        return [], f"contenido no es lista (es {type(raw).__name__})"
    return raw, None


def save_session_dicts(path, dicts) -> None:
    """Guarda la lista de dicts de forma atómica. LANZA si falla (quien
    llama decide si tragarla); ante cualquier error el fichero original
    sigue intacto, nunca a medias."""
    write_json_atomic(path, list(dicts))
