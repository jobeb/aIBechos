"""
Descompresión de archivos comprimidos (.zip/.7z/.rar/.tar y variantes
comprimidas de tar) encontrados en la carpeta vigilada -- muchos
libros/cómics se descargan empaquetados así en vez de sueltos (ver
core/auto_watcher.py, activado con "auto_extract_archives" en Ajustes).

.zip, .tar (y sus variantes .tar.gz/.tgz, .tar.bz2/.tbz2, .tar.xz/.txz)
funcionan siempre -- zipfile/tarfile son de la librería estándar, sin
depender de gzip/bz2/lzma como binarios externos (también de serie en
CPython). .7z funciona siempre igual (py7zr es una dependencia Python
pura, sin binario externo). .rar necesita un programa externo
(unrar/unar/bsdtar) que esta app NO instala ni empaqueta -- si no está
disponible en el sistema, la extracción falla de forma controlada (ver
_extract_rar) y el archivo se deja intacto para que el usuario lo
resuelva a mano.

En Windows, rarfile solo busca "unrar"/"unar"/"7z"/"7zz"/"bsdtar" como
comando SUELTO en el PATH -- WinRAR instala UnRAR.exe (misma sintaxis de
línea de comandos que el unrar independiente) pero nunca lo añade al PATH,
así que un usuario con WinRAR instalado de verdad seguía viendo "unrar no
está instalado" (bug real, confirmado con un archivo .rar real: UnRAR.exe
SÍ estaba en su carpeta de instalación de WinRAR, rarfile simplemente no
lo encontraba). Ver _configure_windows_rar_fallback_tool, que busca en las
rutas de instalación típicas antes de rendirse.
"""

import os
from pathlib import Path

from core.appdirs import is_windows

_TAR_COMPOUND_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz")
_TAR_SINGLE_SUFFIXES = {".tar", ".tgz", ".tbz2", ".txz"}

# (plantilla de ruta con variable de entorno, atributo de rarfile a fijar) --
# UnRAR.exe/Rar.exe de WinRAR entienden la misma sintaxis que el unrar de
# línea de comandos independiente (UNRAR_CONFIG), así que apuntar
# rarfile.UNRAR_TOOL a su ruta completa basta, sin necesidad de una
# configuración de herramienta distinta. 7-Zip (sevenzip) sabe LEER (no
# crear) archivos .rar -- válido como último recurso si no hay WinRAR.
_WINDOWS_RAR_TOOL_CANDIDATES = [
    (r"%ProgramFiles%\WinRAR\UnRAR.exe", "UNRAR_TOOL"),
    (r"%ProgramFiles(x86)%\WinRAR\UnRAR.exe", "UNRAR_TOOL"),
    (r"%ProgramFiles%\WinRAR\Rar.exe", "UNRAR_TOOL"),
    (r"%ProgramFiles(x86)%\WinRAR\Rar.exe", "UNRAR_TOOL"),
    (r"%ProgramFiles%\7-Zip\7z.exe", "SEVENZIP_TOOL"),
    (r"%ProgramFiles(x86)%\7-Zip\7z.exe", "SEVENZIP_TOOL"),
]

_rar_fallback_probed = False


def _configure_windows_rar_fallback_tool(rarfile_module) -> bool:
    """Se llama solo cuando rarfile ya falló al buscar un binario por PATH
    (RarCannotExec) -- prueba las rutas de instalación típicas de Windows
    para WinRAR/7-Zip y, si encuentra algo, deja rarfile_module configurado
    para el resto de la sesión (no hace falta repetir la búsqueda en cada
    archivo .rar). Devuelve True si encontró y configuró algo utilizable."""
    global _rar_fallback_probed
    if _rar_fallback_probed:
        return rarfile_module.UNRAR_TOOL != "unrar" or rarfile_module.SEVENZIP_TOOL != "7z"
    _rar_fallback_probed = True
    if not is_windows():
        return False
    for template, attr in _WINDOWS_RAR_TOOL_CANDIDATES:
        candidate = os.path.expandvars(template)
        if os.path.isfile(candidate):
            setattr(rarfile_module, attr, candidate)
            try:
                rarfile_module.tool_setup(force=True)
                return True
            except rarfile_module.RarCannotExec:
                continue
    return False


def _is_safe_member(dest: Path, member_name: str) -> bool:
    """Rechaza miembros que intentarían escribir fuera de *dest* (rutas con
    "..", absolutas, o symlinks que apunten fuera) -- "zip slip". El archivo
    viene de una fuente no confiable (una descarga), así que esto no es
    opcional."""
    try:
        dest_resolved = dest.resolve()
        member_resolved = (dest / member_name).resolve()
    except (OSError, ValueError):
        return False
    return member_resolved == dest_resolved or dest_resolved in member_resolved.parents


def _archive_kind_and_dest(path: Path):
    """Devuelve (kind, dest) -- kind es "zip"/"7z"/"rar"/"tar", o (None, None)
    si la extensión no se reconoce. dest es la carpeta hermana destino
    (mismo nombre sin extensión) -- para .tar.gz/.tar.bz2/.tar.xz hay que
    quitar el sufijo COMPUESTO entero (dos puntos), no solo el último
    (path.stem de "x.tar.gz" daría "x.tar", no "x")."""
    name_lower = path.name.lower()
    for compound in _TAR_COMPOUND_SUFFIXES:
        if name_lower.endswith(compound):
            return "tar", path.parent / path.name[:-len(compound)]
    ext = path.suffix.lower()
    if ext in _TAR_SINGLE_SUFFIXES:
        return "tar", path.parent / path.stem
    if ext in (".zip", ".7z", ".rar"):
        return ext[1:], path.parent / path.stem
    return None, None


def extract_archive(path) -> tuple[bool, str]:
    """Extrae *path* a una subcarpeta hermana con su mismo nombre (sin
    extensión). Devuelve (True, ruta_destino) o (False, motivo) -- nunca
    lanza, ni con un archivo corrupto, ni con .rar sin unrar instalado."""
    path = Path(path)
    kind, dest = _archive_kind_and_dest(path)
    try:
        if kind == "zip":
            return _extract_zip(path, dest)
        if kind == "7z":
            return _extract_7z(path, dest)
        if kind == "rar":
            return _extract_rar(path, dest)
        if kind == "tar":
            return _extract_tar(path, dest)
        return False, f"Formato de archivo comprimido no soportado: {path.suffix.lower()}"
    except Exception as e:
        return False, f"No se pudo descomprimir: {e}"


def _extract_zip(path: Path, dest: Path) -> tuple[bool, str]:
    import zipfile
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if not all(_is_safe_member(dest, n) for n in names):
            return False, "Archivo con rutas inseguras (posible zip-slip) -- no se descomprime"
        dest.mkdir(parents=True, exist_ok=True)
        zf.extractall(dest)
    return True, str(dest)


def _extract_tar(path: Path, dest: Path) -> tuple[bool, str]:
    import tarfile
    with tarfile.open(path, mode="r:*") as tf:   # "r:*" autodetecta gz/bz2/xz o sin comprimir
        names = tf.getnames()
        if not all(_is_safe_member(dest, n) for n in names):
            return False, "Archivo con rutas inseguras (posible zip-slip) -- no se descomprime"
        dest.mkdir(parents=True, exist_ok=True)
        # filter="data" (Python 3.12+, PEP 706): capa extra de seguridad del
        # propio tarfile (bloquea rutas absolutas, symlinks que escapen,
        # archivos de dispositivo...) -- además de _is_safe_member, que ya
        # cubre lo mismo para todos los formatos de este módulo.
        tf.extractall(dest, filter="data")
    return True, str(dest)


def _extract_7z(path: Path, dest: Path) -> tuple[bool, str]:
    import py7zr
    with py7zr.SevenZipFile(path, mode="r") as zf:
        names = zf.getnames()
        if not all(_is_safe_member(dest, n) for n in names):
            return False, "Archivo con rutas inseguras (posible zip-slip) -- no se descomprime"
        dest.mkdir(parents=True, exist_ok=True)
        zf.extractall(path=dest)
    return True, str(dest)


def _extract_rar(path: Path, dest: Path) -> tuple[bool, str]:
    import rarfile

    def _do_extract() -> tuple[bool, str]:
        with rarfile.RarFile(path) as rf:
            # namelist() NO es puro Python pese a lo que se pensaba antes
            # (bug real: con un .rar de verdad sin unrar todavía
            # configurado, esta misma llamada ya lanza RarCannotExec --
            # algunas cabeceras SÍ necesitan invocar la herramienta
            # externa para leerse, contra lo que decía este comentario) --
            # por eso toda la función vive dentro del mismo try/except de
            # más abajo, no solo extractall().
            names = rf.namelist()
            if not all(_is_safe_member(dest, n) for n in names):
                return False, "Archivo con rutas inseguras (posible zip-slip) -- no se descomprime"
            dest.mkdir(parents=True, exist_ok=True)
            rf.extractall(dest)
        return True, str(dest)

    try:
        return _do_extract()
    except rarfile.RarCannotExec:
        # Antes de rendirse: en Windows, WinRAR/7-Zip pueden estar
        # instalados de verdad pero no en el PATH (ver
        # _configure_windows_rar_fallback_tool) -- reintentar la operación
        # ENTERA (no solo extractall(), ver más arriba) una vez con lo que
        # se encuentre ahí antes de reportar "no instalado".
        if not _configure_windows_rar_fallback_tool(rarfile):
            return False, ("unrar no está instalado en este sistema -- instálalo "
                            "(o unar/bsdtar) para poder descomprimir archivos .rar")
        return _do_extract()
