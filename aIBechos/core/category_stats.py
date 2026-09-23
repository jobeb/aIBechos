"""
Recuento y tamaño por categoría, compartido entre todos los clientes de
aIBechos que apuntan al mismo servidor FTP -- mismo patrón que
core/upload_stats.py, pero acumulado por CARPETA de nivel superior (una
serie o película completa), no por persona.

Por qué incremental y no un escaneo del árbol del FTP en cada visita a la
pestaña de Estadísticas: recorrer el servidor entero (list_tree_recursive)
es caro. En cambio, cada subida ya conoce su ruta remota completa y su
tamaño (ver gui/app.py::_save_history_entry), y cada borrado ya conoce qué
carpeta se borró y cuánto pesaba (ver _save_deletion_history_entry) --
sumar/restar en esos dos puntos ya existentes es prácticamente gratis.
El árbol completo solo se recorre UNA vez, para sembrar el archivo
compartido con lo que ya había en el servidor antes de que existiera esta
función (ver gui/app.py::_sync_category_stats_from_ftp, "bootstrap").

Formato: {category_id: {"category_name": "...", "folders": {folder_name:
total_bytes, ...}}} -- la clave de nivel superior es el "id" de la
categoría en ftp_categories (estable aunque se renombre la categoría), no
su nombre visible.
"""

import json

from core.appdirs import app_data_dir

_FILENAME = "category_stats.json"

# Versión del ALGORITMO de escaneo (no del formato del dict en sí, que no
# ha cambiado) -- súbela cuando un cambio en cómo se cuenta/agrupa deje
# datos YA calculados y compartidos en el FTP desactualizados respecto a
# la realidad (p.ej. el fix de películas sueltas sin carpeta propia, que
# antes se ignoraban por completo: sin este número de versión, el archivo
# compartido ya tenía datos "válidos" -- aunque mal calculados -- y
# _sync_category_stats_from_ftp nunca volvía a disparar el bootstrap que
# los recalcula, por más que el código de conteo ya estuviera arreglado).
# Ver gui/app.py::_sync_category_stats_from_ftp/_bootstrap_category_stats.
SCAN_VERSION = 2


def wrap_for_remote(data: dict) -> dict:
    """Envuelve `data` (el dict de siempre, category_id -> {...}) con la
    versión de escaneo actual antes de subirlo al FTP compartido."""
    return {"_scan_version": SCAN_VERSION, "data": data}


def unwrap_from_remote(payload) -> "dict | None":
    """Contrario de wrap_for_remote(). None si `payload` no es un dict con
    la versión de escaneo ACTUAL -- tanto si está corrupto/vacío como si
    es de una versión de escaneo antigua (antes de un fix de conteo, ver
    SCAN_VERSION) que necesita recalcularse desde cero con un bootstrap
    nuevo, no seguir aplicándose tal cual."""
    if not isinstance(payload, dict) or payload.get("_scan_version") != SCAN_VERSION:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _path():
    return app_data_dir() / _FILENAME


def load_local_cache() -> dict:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_local_cache(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolve_category_and_folder(remote_path: str, ftp_categories: dict, is_folder_path: bool = False):
    """A partir de una ruta FTP y la configuración de categorías
    (config_data["ftp_categories"]), averigua a qué categoría pertenece y
    cuál es el nombre de su carpeta de nivel superior (serie/película
    completa). Si varias categorías tienen raíces una dentro de otra, gana
    la coincidencia más larga (la más específica). Devuelve (category_id,
    category_name, folder_name), o None si no coincide ninguna raíz
    configurada.

    is_folder_path=False (subidas, el valor por defecto): remote_path es
    la ruta COMPLETA de un ARCHIVO -- el primer segmento tras la raíz es
    la carpeta de nivel superior. Si el archivo está suelto directamente
    en la raíz (sin carpeta propia -- caso típico de películas guardadas
    como un único archivo, sin envolver en su propia carpeta), se agrupa
    por su nombre base (ver core.cleanup_candidates.file_base_name) para
    que el vídeo y sus archivos compañeros, si los hubiera, cuenten como
    un único elemento -- mismo criterio que group_loose_files_by_name en
    Liberar espacio. (Antes esta función ignoraba estos archivos sueltos
    por completo, lo que hacía que el panel de Estadísticas contara muchas
    menos películas de las reales.)

    is_folder_path=True (borrados): remote_path YA es la ruta de la propia
    carpeta de nivel superior, o del grupo de archivos sueltos (ver
    CleanupItem.ftp_path para ambos casos: delete_folder_recursive borra
    la carpeta entera; para un grupo de sueltos, ftp_path es
    "{root}/{nombre_base}", una ruta virtual que no existe como archivo
    real, pero identifica al grupo igual que una carpeta) -- el segmento
    tras la raíz es directamente ese nombre, sin necesitar un segmento
    adicional detrás."""
    remote_path = (remote_path or "").replace("\\", "/").rstrip("/")
    best = None
    for cat_list in (ftp_categories or {}).values():
        for cat in cat_list:
            root = (cat.get("root") or "").rstrip("/")
            if not root:
                continue
            prefix = root + "/"
            if remote_path.startswith(prefix) and (best is None or len(root) > len(best[0])):
                best = (root, cat)
    if best is None:
        return None
    root, cat = best
    rest = remote_path[len(root) + 1:]
    if not rest:
        return None
    if "/" in rest:
        folder_name = rest.split("/", 1)[0]
    elif is_folder_path:
        folder_name = rest
    else:
        from core.cleanup_candidates import file_base_name
        folder_name = file_base_name(rest)
    return cat.get("id", root), cat.get("name", root), folder_name


def add_folder_bytes(data: dict, category_id: str, category_name: str,
                      folder_name: str, size_bytes: int) -> dict:
    """Devuelve un dict NUEVO (no muta `data`) con size_bytes sumado al
    total de folder_name dentro de category_id -- una subida más de esa
    misma serie/película acumula, no reemplaza."""
    result = dict(data)
    cat_entry = dict(result.get(category_id) or {"category_name": category_name, "folders": {}})
    cat_entry["category_name"] = category_name
    folders = dict(cat_entry["folders"])
    folders[folder_name] = folders.get(folder_name, 0) + size_bytes
    cat_entry["folders"] = folders
    result[category_id] = cat_entry
    return result


def remove_folder(data: dict, category_id: str, folder_name: str) -> dict:
    """Devuelve un dict NUEVO con folder_name quitado por completo de
    category_id -- un borrado siempre es de la carpeta entera
    (delete_folder_recursive), nunca queda nada parcial que restar. Sin
    efecto si esa carpeta no estaba registrada."""
    folders = data.get(category_id, {}).get("folders", {})
    if folder_name not in folders:
        return data
    result = dict(data)
    cat_entry = dict(result[category_id])
    new_folders = dict(cat_entry["folders"])
    new_folders.pop(folder_name, None)
    cat_entry["folders"] = new_folders
    result[category_id] = cat_entry
    return result


def category_summaries(data: dict) -> list:
    """[{"name", "count", "bytes"}, ...] -- para pintar el panel "Por
    categoría" directamente, sin que gui/stats_view.py tenga que conocer
    la forma interna del dict."""
    out = []
    for cat_entry in (data or {}).values():
        folders = cat_entry.get("folders", {})
        out.append({
            "name": cat_entry.get("category_name", "?"),
            "count": len(folders),
            "bytes": sum(folders.values()),
        })
    return out


def total_bytes(data: dict) -> int:
    return sum(s["bytes"] for s in category_summaries(data))


def bytes_by_disk(data: dict, ftp_categories: dict) -> dict:
    """Agrupa category_summaries() por disco -- el primer segmento de la
    raíz de cada categoría, mismo criterio que
    gui/app.py::_disks_from_categories, para que dos categorías que
    comparten disco físico (p.ej. "Series" y "SeriesPeques" bajo
    /datos2/) aparezcan juntas en la misma barra en vez de una por
    categoría. Devuelve {disk_name: [{"name", "count", "bytes"}, ...]}."""
    id_to_root = {}
    for cat_list in (ftp_categories or {}).values():
        for cat in cat_list:
            id_to_root[cat.get("id")] = cat.get("root", "")
    result: dict = {}
    for category_id, cat_entry in (data or {}).items():
        root = id_to_root.get(category_id, "")
        segments = [s for s in (root or "").replace("\\", "/").split("/") if s]
        if not segments:
            continue
        folders = cat_entry.get("folders", {})
        result.setdefault(segments[0], []).append({
            "name": cat_entry.get("category_name", "?"),
            "count": len(folders),
            "bytes": sum(folders.values()),
        })
    return result
