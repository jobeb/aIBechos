"""
¿Dónde está un archivo: solo en local, o también en el servidor?

Lo consume el diálogo de la ✕ en la lista de Archivos, que solo debe ofrecer
"borrar también del servidor" cuando allí hay algo que borrar de verdad.

El dato sale de upload_history.json, pero ese historial está a medias: hasta
la corrección del emisor, las subidas del modo automático guardaban la ruta
LOCAL en el campo "remote" (241 de 500 registros en una instalación real).
De ahí que esto sea híbrido -- el historial cuando el dato es aprovechable,
y una comprobación FTP de verdad cuando no lo es.
"""

from pathlib import PurePosixPath


def looks_like_remote_path(value: str) -> bool:
    """True si *value* parece una ruta del servidor y no una ruta local de
    Windows/macOS guardada por error.

    Las rutas del servidor son POSIX absolutas ("/datos2/series/..."); las
    locales traen letra de unidad ("C:\\...") o separadores invertidos.
    """
    if not value or not isinstance(value, str):
        return False
    if "\\" in value:
        return False
    if len(value) >= 2 and value[1] == ":":
        return False
    return value.startswith("/")


def remote_path_from_history(history: list, local_path: str, filename: str = "") -> str:
    """Ruta en el servidor según el historial, o "" si el historial no lo
    sabe (nunca se subió, o lo registró con el campo estropeado).

    Se recorre de la entrada más reciente a la más antigua: si un archivo se
    subió varias veces, manda la última.
    """
    if not history:
        return ""

    for e in reversed(history):
        if not isinstance(e, dict) or e.get("status") != "ok":
            continue
        if local_path and e.get("local_path") == local_path:
            remote = e.get("remote", "")
            return remote if looks_like_remote_path(remote) else ""
        if filename and e.get("filename") == filename:
            remote = e.get("remote", "")
            return remote if looks_like_remote_path(remote) else ""
    return ""


def was_uploaded_according_to_history(history: list, local_path: str, filename: str = "") -> bool:
    """True si el historial dice que este archivo llegó a subirse, aunque no
    sepa a qué ruta -- distingue "nunca se subió" de "se subió pero el
    registro está estropeado", que necesitan tratamientos distintos: el
    primero no ofrece borrado remoto, el segundo lo busca por FTP.
    """
    if not history:
        return False
    for e in reversed(history):
        if not isinstance(e, dict) or e.get("status") != "ok":
            continue
        if local_path and e.get("local_path") == local_path:
            return True
        if filename and e.get("filename") == filename:
            return True
    return False


def stale_remote_from_history(history: list, local_path: str, filename: str,
                              new_remote: str) -> str:
    """Ruta remota bajo la que este MISMO archivo se subió ANTES, si esa
    ruta difiere de la que se va a usar ahora.

    El caso de uso: un archivo se identificó mal ("Papillon (2017)" como
    "Papillon (1973)") y se empezó a subir con ese nombre viejo; al
    reasignarlo bien y volver a subir, el servidor puede haber quedado con
    un resto (parcial o la versión completa) bajo el nombre viejo. Esto
    detecta ese resto comparando el historial (local_path → ruta remota)
    con la ruta nueva. Devuelve "" si no hay ningún resto distinto.

    Solo se miran los registros "ok"/"error" (el "error" a medias también
    deja un parcial) con el campo "remote" aprovechable (rutas POSIX del
    servidor, no las locales guardadas por error -- ver
    looks_like_remote_path). Si la ruta vieja coincide con la nueva (se
    está resubiendo el mismo archivo al mismo sitio), no hay resto que
    borrar y se devuelve "".
    """
    if not new_remote or not looks_like_remote_path(new_remote):
        return ""
    for e in reversed(history or []):
        if not isinstance(e, dict) or e.get("status") not in ("ok", "error"):
            continue
        if local_path and e.get("local_path") == local_path:
            old = e.get("remote", "")
            if looks_like_remote_path(old) and old != new_remote:
                return old
        elif not local_path and filename and e.get("filename") == filename:
            old = e.get("remote", "")
            if looks_like_remote_path(old) and old != new_remote:
                return old
    return ""


def resolve_remote_path(history: list, local_path: str, filename: str,
                        ftp_lookup=None) -> str:
    """Ruta en el servidor, tirando de FTP solo cuando hace falta.

    ftp_lookup(filename) -> ruta completa o "" -- lo aporta quien llama
    (la GUI sabe cómo listar sin bloquear la interfaz). Si no se pasa, esta
    función se queda con lo que diga el historial.

    Orden deliberado: primero el historial, que es instantáneo; la consulta
    FTP solo para los registros viejos con el campo estropeado, que se irán
    extinguiendo según se resuban archivos con el emisor ya corregido.
    """
    remote = remote_path_from_history(history, local_path, filename)
    if remote:
        return remote

    if ftp_lookup and was_uploaded_according_to_history(history, local_path, filename):
        try:
            return ftp_lookup(filename) or ""
        except Exception:
            return ""
    return ""


# ── Qué ofrece el diálogo de la ✕ ───────────────────────────────────────

# Identificadores de las acciones. El diálogo los devuelve tal cual y quien
# lo llama decide qué hacer; así la regla de qué se ofrece se puede probar
# sin levantar tkinter.
QUITAR_LISTA          = "lista"
QUITAR_Y_LOCAL        = "lista_local"
QUITAR_LOCAL_Y_REMOTO = "lista_local_servidor"

DESTRUCTIVAS = (QUITAR_Y_LOCAL, QUITAR_LOCAL_Y_REMOTO)


def removal_options(local_exists: bool, remote_path: str) -> list:
    """Acciones aplicables al quitar una fila, en orden de menos a más
    destructiva.

    Quitar de la lista se ofrece siempre: no destruye nada y es lo único
    que tiene sentido cuando el archivo ya no está en ninguna parte.

    Borrar en local solo si el archivo sigue en disco. Borrar también en
    el servidor solo si se sabe dónde está allí -- ofrecer un borrado
    remoto sin ruta fiable acabaría mandando basura al FTP (ver
    looks_like_remote_path: casi la mitad del historial guardaba la ruta
    local en el campo del servidor). El borrado remoto se ofrece incluso
    si el archivo ya no está en local (subido y luego borrado/movido),
    para que "ya subido" no se quede huérfano en el servidor.
    """
    opciones = [QUITAR_LISTA]
    if local_exists:
        opciones.append(QUITAR_Y_LOCAL)
    if looks_like_remote_path(remote_path):
        opciones.append(QUITAR_LOCAL_Y_REMOTO)
    return opciones


def is_destructive(option: str) -> bool:
    """Si la acción borra archivos hace falta una segunda confirmación con
    la ruta exacta -- quitar de la lista, no."""
    return option in DESTRUCTIVAS


def join_remote(directory: str, filename: str) -> str:
    """Une directorio y nombre con separadores POSIX, que es lo que entiende
    el servidor -- os.path.join usaría "\\" en Windows."""
    if not directory:
        return filename or ""
    return str(PurePosixPath(directory) / (filename or ""))
