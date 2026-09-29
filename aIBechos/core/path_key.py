"""Clave canónica de identidad de un archivo local.

La tabla de Archivos y el modo automático identificaban cada archivo por
el texto exacto de su ruta, y en Windows `C:\\Users\\...` y
`c:\\users\\...` son el MISMO archivo con distinto texto: la misma ruta
llegaba por dos caminos (arrastrar, diálogo de archivos, `os.walk` del
watcher, session.json de otro arranque) y la app creía que eran dos
archivos distintos -- filas duplicadas y los dos sistemas de subida
compitiendo por el mismo fichero. TODA comparación de identidad debe
usar canon_path(), nunca el string tal cual.

En Windows se ignora caja y se unifican separadores; en POSIX la caja
sí distingue archivos y se conserva (os.path.normcase solo toca la
caja en Windows). No toca el disco (sin resolve(): los enlaces no
importan para identidad y resolve() falla con rutas inexistentes).
Pura, testeable.
"""

import os


def canon_path(p) -> str:
    """Forma canónica de *p* para comparar identidad. "" si no hay ruta."""
    try:
        s = os.fspath(p) if not isinstance(p, str) else p
    except Exception:
        return ""
    if not s:
        return ""
    try:
        if s.startswith("\\\\?\\"):
            s = s[4:]
        # normpath: separadores + "."/".." léxicos. normcase: minúsculas
        # solo en Windows (en POSIX distingue de verdad).
        return os.path.normcase(os.path.normpath(s))
    except Exception:
        return s
