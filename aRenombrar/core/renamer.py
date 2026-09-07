"""
Lógica de renombrado de archivos.
Variables: {serie}, {titulo}, {temporada}, {episodio}, {año}, {ext}
"""

import re
from pathlib import Path
from core.api_client import MediaInfo


DEFAULT_TV_TEMPLATE = "{serie} {temporada}x{episodio:02d} {titulo}{ext}"
DEFAULT_MOVIE_TEMPLATE = "{serie} ({año}){ext}"
DEFAULT_ANIME_TEMPLATE = "{serie} {temporada}x{episodio:03d} {titulo}{ext}"
DEFAULT_LIBRO_TEMPLATE = "{serie}{ext}"


def build_new_name(info: MediaInfo, template: str, ext: str) -> str:
    """
    Construye el nuevo nombre de archivo.
      TV:     "Breaking Bad 3x07 One Minute.mkv"
      Movie:  "The Dark Knight (2008).mkv"
      Anime:  "One Piece 1x1078 The New Era.mkv"
    """
    ext = ext if ext.startswith(".") else f".{ext}"

    replacements = {
        "serie": _sanitize(info.title),
        "titulo": _sanitize(info.episode_title or ""),
        "temporada": info.season or 1,
        "episodio": info.episode or 1,
        "año": info.year or "",
        "ext": ext,
    }

    try:
        name = template.format(**replacements)
    except (KeyError, ValueError) as e:
        raise ValueError(f"Plantilla inválida: {e}")

    # Limpiar espacios dobles cuando {titulo} está vacío
    name = re.sub(r"\s{2,}", " ", name).strip()
    # Quitar espacio sobrante antes de la extensión: "Serie 3x07 .mkv" -> "Serie 3x07.mkv"
    name = re.sub(r"\s+(\.[a-zA-Z0-9]+)$", r"\1", name)

    return name


def _sanitize(text: str) -> str:
    """Elimina caracteres no válidos en nombres de archivo."""
    invalid = r'[<>:"/\\|?*\x00-\x1f]'
    text = re.sub(invalid, "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = text.rstrip(".")
    return text


def rename_file(src_path: str, new_name: str, dry_run: bool = False, force_overwrite: bool = False):
    """Renombra src_path con new_name. Devuelve (éxito, mensaje).
    force_overwrite: si ya existe un archivo distinto con new_name, lo
    reemplaza en vez de fallar con "Ya existe" (decisión explícita del
    usuario tras un diálogo de confirmación, nunca por defecto)."""
    src = Path(src_path)
    if not src.exists():
        return False, f"Archivo no encontrado: {src_path}"

    dst = src.parent / new_name

    if dst.exists() and not dst.samefile(src) and not force_overwrite:
        return False, f"Ya existe: {new_name}"

    if dry_run:
        return True, f"[Simulación] {src.name} → {new_name}"

    try:
        if force_overwrite:
            src.replace(dst)   # atómico: sobrescribe el destino si ya existe
        else:
            src.rename(dst)
        return True, str(dst)
    except OSError as e:
        return False, f"Error al renombrar: {e}"


def get_extension(filepath: str) -> str:
    return Path(filepath).suffix.lower()


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v",
    ".wmv", ".flv", ".ts", ".m2ts", ".webm",
    ".mpg", ".mpeg", ".mpe", ".m2v", ".mts", ".m2ts",
    ".3gp", ".3g2", ".f4v", ".rm", ".rmvb", ".vob", ".ogv",
}

# Ebooks de texto -- identificados vía Google Books (core/book_client.py).
EBOOK_EXTENSIONS = {".pdf", ".epub", ".mobi", ".azw3"}
# Cómics/manga escaneados -- identificados vía ComicVine
# (core/comicvine_client.py), no Google Books: son imágenes empaquetadas,
# no texto, y ComicVine tiene el catálogo real para esto.
COMIC_EXTENSIONS = {".cbz", ".cbr"}
BOOK_EXTENSIONS = EBOOK_EXTENSIONS | COMIC_EXTENSIONS


def is_video_file(filepath: str) -> bool:
    return get_extension(filepath) in VIDEO_EXTENSIONS


def is_book_file(filepath: str) -> bool:
    return get_extension(filepath) in BOOK_EXTENSIONS


def is_comic_file(filepath: str) -> bool:
    return get_extension(filepath) in COMIC_EXTENSIONS


# Archivos comprimidos -- muchos libros/cómics se descargan empaquetados así
# en vez de sueltos (ver core/archive_extract.py, usado por AutoWatcher
# cuando "auto_extract_archives" está activado en Ajustes). ".tgz"/".tbz2"/
# ".txz" son alias de un solo sufijo (get_extension ya los reconoce tal
# cual); ".tar.gz"/".tar.bz2"/".tar.xz" son sufijo COMPUESTO (dos puntos) --
# Path.suffix (get_extension) solo ve el último (".gz"), así que
# is_archive_file comprueba también los dos últimos sufijos juntos.
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".tgz", ".tbz2", ".txz"}
ARCHIVE_COMPOUND_EXTENSIONS = {".tar.gz", ".tar.bz2", ".tar.xz"}


def is_archive_file(filepath: str) -> bool:
    if get_extension(filepath) in ARCHIVE_EXTENSIONS:
        return True
    suffixes = Path(filepath).suffixes
    return len(suffixes) >= 2 and "".join(suffixes[-2:]).lower() in ARCHIVE_COMPOUND_EXTENSIONS


def build_name_for_media_info(info: MediaInfo, ext: str, templates: dict) -> str:
    """Elige la plantilla según info.media_type/genre_ids y construye el
    nombre -- misma lógica que gui/app.py::_build_name, extraída aquí para
    que core/auto_watcher.py (identificación de libros/cómics en el Modo
    Automático) la reutilice sin duplicar el dispatch de 4 vías.

    templates: dict con las claves "movie_template", "anime_template",
    "comic_template", "libro_template", "tv_template" (mismos nombres que
    las claves de Config, para poder pasar directamente
    {k: config.get(k) for k in (...)} desde el llamador)."""
    if info.media_type == "movie":
        tpl = templates.get("movie_template")
    elif info.media_type == "anime":
        tpl = templates.get("anime_template")
    elif info.media_type == "libro":
        # "comic" en genre_ids viene de ComicVineClient.build_comic_info --
        # distingue cómic de ebook dentro del mismo media_type "libro" (una
        # sola categoría FTP, dos plantillas de nombre).
        is_comic = "comic" in (info.genre_ids or [])
        tpl = templates.get("comic_template" if is_comic else "libro_template")
    else:
        tpl = templates.get("tv_template")
    return build_new_name(info, tpl, ext)
