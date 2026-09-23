"""
Empareja la raíz de una categoría FTP (ruta relativa vista desde el cliente
FTP, p.ej. "/datos2/series/") con la carpeta de biblioteca que Jellyfin
reporta con su ruta ABSOLUTA del sistema de archivos real del servidor
(p.ej. "/home/administrador/datos2/series") -- comparando los últimos
segmentos de ruta, que suelen coincidir aunque el prefijo de montaje sea
distinto entre lo que ve el FTP y lo que ve Jellyfin desde el propio disco.
"""

from typing import Optional


def _segments(path: str) -> list:
    return [p for p in (path or "").replace("\\", "/").split("/") if p]


def match_root_to_folder(category_root: str, jellyfin_folders: list) -> Optional[dict]:
    """Busca en *jellyfin_folders* (lista de dicts con al menos "Path") la
    carpeta cuyos últimos segmentos de ruta coinciden exactamente con los de
    *category_root*. Devuelve el dict de la carpeta encontrada, o None."""
    root_segments = [s.lower() for s in _segments(category_root)]
    if not root_segments:
        return None
    n = len(root_segments)
    for folder in jellyfin_folders or []:
        path_segments = [s.lower() for s in _segments(folder.get("Path", ""))]
        if len(path_segments) >= n and path_segments[-n:] == root_segments:
            return folder
    return None
