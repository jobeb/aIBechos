"""
Utilidades de plataforma y rutas de datos de la app, compartidas por
config.py, gui/app.py y core/auto_watcher.py — antes cada uno tenía su
propia copia de esta lógica, y solo una distinguía macOS de Linux
correctamente (las otras dos mandaban los datos de macOS a "~/.config" en
vez de "~/Library/Application Support", repartiendo los ficheros de la app
entre dos carpetas distintas).
"""

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "aIBechos"

#: Cómo se llamaba la aplicación antes. Se conserva porque de este nombre
#: cuelga TODO lo que un usuario ya tiene guardado: la carpeta de datos, el
#: servicio del llavero donde vive la contraseña del servidor (ver config.py)
#: y la entrada de arranque automático (ver gui/app.py). Sin migrarlo, cambiar
#: el nombre dejaría la aplicación como recién instalada.
LEGACY_APP_NAME = "aRenombrar"

#: Se escribe en la carpeta nueva CUANDO la copia ha terminado entera, para no
#: repetirla en cada arranque. Si la migración se corta a medias, el marcador
#: no llega a escribirse y se reintenta en el siguiente lanzamiento.
_MIGRATION_MARKER = f".migrado_desde_{LEGACY_APP_NAME}"

#: app_data_dir() se llama decenas de veces por arranque; esto evita mirar el
#: marcador en disco todas ellas.
_migration_checked = False


def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Explícito en vez de inferir Linux por descarte (`else`/`not
    (is_windows() or is_macos())`, como hacía antes cada sitio que
    necesitaba distinguirlo) -- más claro de leer en gui/app.py donde ya
    hay tres ramas por SO (autoarranque, notificaciones...)."""
    return sys.platform.startswith("linux")


def _base_data_dir() -> Path:
    """La carpeta del sistema donde cada aplicación guarda lo suyo."""
    if is_windows():
        return Path(os.environ.get("APPDATA", Path.home()))
    if is_macos():
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".config"


def app_data_dir() -> Path:
    """Carpeta base de datos de la app, creada si no existe:
      Windows → %APPDATA%\\aIBechos
      macOS   → ~/Library/Application Support/aIBechos
      Linux   → ~/.config/aIBechos

    La primera vez que se llama tras el cambio de nombre se trae lo que hubiera
    en la carpeta de la aplicación antigua (ver _migrate_from_legacy)."""
    base = _base_data_dir()
    p = base / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    _migrate_from_legacy(base, p)
    return p


def _migrate_from_legacy(base: Path, new_dir: Path):
    """Trae los datos de cuando la aplicación se llamaba de otra forma.

    Se COPIA, nunca se mueve ni se borra: la carpeta antigua queda intacta, así
    que volver a instalar la versión anterior sigue funcionando y nada se
    pierde si esto sale mal.

    Se copia solo lo que falte en el destino, así que la función es reanudable
    (si se cortó a medias, la siguiente vez termina lo que quedaba) y no molesta
    aunque dos procesos entren a la vez -- cosa que puede pasar, porque main.py
    abre el log, y con él esta carpeta, ANTES de comprobar que no haya otra
    instancia abierta. Cada archivo se copia a un temporal y se pone en su sitio
    de un solo golpe (os.replace), para que nadie llegue a ver un archivo a
    medio escribir.

    Que esto falle (permisos, disco lleno) no puede impedir que la aplicación
    arranque: se traga el error y se reintenta en el siguiente lanzamiento."""
    global _migration_checked
    if _migration_checked:
        return
    _migration_checked = True
    try:
        marcador = new_dir / _MIGRATION_MARKER
        if marcador.exists():
            return
        old_dir = base / LEGACY_APP_NAME
        if old_dir.is_dir() and old_dir.resolve() != new_dir.resolve():
            for origen in old_dir.iterdir():
                if not origen.is_file():
                    continue
                destino = new_dir / origen.name
                if destino.exists():
                    continue        # ya migrado, o escrito de nuevo: no se pisa
                temporal = new_dir / (origen.name + ".migrando")
                shutil.copy2(origen, temporal)
                os.replace(temporal, destino)
        # Solo al final: una copia incompleta nunca debe contar como hecha.
        marcador.write_text(
            f"Datos traídos de {LEGACY_APP_NAME}. Borrar este archivo obliga a "
            f"comprobarlo otra vez.\n", encoding="utf-8")
    except Exception:
        pass
