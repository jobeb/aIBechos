"""
Candidatos a "adelgazar" en Liberar espacio: capítulos (o películas) cuyo
peso está lejos por arriba del tamaño objetivo y que por tanto conviene
re-descargar en una versión más ligera.

Lógica pura (sin tkinter ni FTP), reutilizando lo que ya existe en
core/download_quality.py:

- Tamaño típico: _typical_from_sizes() (buckets de 200 MB + mayoría
  estricta, si no mediana global).
- Rango por resolución: _size_range_for_resolution() (distinto para
  capítulo de serie y película).

Cadena de resolución del objetivo ("target"), por serie:

1. `desired_bytes` configurado por el usuario para esa serie -> manda
   siempre (el usuario sabe lo que quiere para ESA serie).
2. Si no hay deseado: típico de _typical_from_sizes(sizes).
3. Salvaguarda "todo pesado": si el típico supera el techo del rango por
   resolución del propio archivo (p.ej. toda la serie en REMUX de 3 GB
   cuando un capítulo 1080p no debería pasar de 2 GB), el típico está
   inflado y se usa el techo del rango en vez de la mediana -- sin esto,
   una serie entera pesada nunca propondría nada.
4. Películas (is_movie=True): sin mediana útil; target = deseado si
   existe, si no el techo del rango por resolución del nombre.

Un archivo es candidato si size >= target * ratio (por defecto 2.0, el
mismo umbral que score_download usa para outliers) y el ahorro absoluto
supera min_saving_bytes (por defecto 100 MB, para no proponer ruido).
"""

from __future__ import annotations


DEFAULT_SLIM_RATIO = 2.0
DEFAULT_MIN_SAVING_BYTES = 100 * 1024 * 1024
# Clave de config (config.py::DEFAULTS): {nombre_normalizado: MB}.
# Se guarda en MB porque es lo que el usuario escribe; aquí se convierte
# a bytes. Si algún valor llegase en bytes (migración manual), se detecta
# por magnitud (ver desired_mb_to_bytes).
SLIM_DESIRED_CONFIG_KEY = "slim_desired_sizes"


def desired_mb_to_bytes(value) -> int | None:
    """MB (lo que escribe el usuario) -> bytes. Tolera valores que ya
    vengan en bytes (p.ej. edición manual del JSON): por encima de 100000
    no pueden ser MB (serían 100 TB), así que se toman tal cual."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 100000:
        return int(v)
    return int(v * 1024 * 1024)


def get_desired_bytes(desired_map: dict | None, series_name: str) -> int | None:
    """Busca el tamaño deseado para *series_name* en el mapa de config
    (claves = normalize_series_name). Devuelve bytes o None."""
    if not desired_map or not series_name:
        return None
    try:
        from core.series_match import normalize_series_name
        key = normalize_series_name(series_name)
    except Exception:
        key = (series_name or "").strip().lower()
    # Compat: aceptar también la clave sin normalizar por si el mapa se
    # editó a mano con el nombre tal cual.
    for cand in (key, (series_name or "").strip(), (series_name or "").strip().lower()):
        if cand in desired_map:
            b = desired_mb_to_bytes(desired_map[cand])
            if b:
                return b
    return None


def resolution_cap_bytes(filename: str = "", is_movie: bool = False) -> int | None:
    """Techo del rango razonable para *filename* según su resolución
    (ver _size_range_for_resolution). None si no se puede determinar."""
    try:
        from core.download_quality import _size_range_for_resolution
        _lo, hi = _size_range_for_resolution(filename or "", is_movie)
        return int(hi) if hi else None
    except Exception:
        return None


def resolve_target_size(sizes: list[int] | None, filename: str = "",
                        is_movie: bool = False,
                        desired_bytes: int | None = None) -> tuple[int | None, str | None]:
    """(target_bytes, origen) para un archivo. *sizes* son los tamaños de
    sus "hermanos" (misma serie/temporada) para la mediana; *filename* se
    usa para el techo por resolución; *desired_bytes* manda siempre.
    origen ∈ {"desired", "typical", "resolution_cap"} o (None, None)."""
    if desired_bytes and desired_bytes > 0:
        return int(desired_bytes), "desired"
    typical = None
    try:
        from core.download_quality import _typical_from_sizes
        if sizes:
            typical = _typical_from_sizes([int(s) for s in sizes if s and int(s) > 0])
    except Exception:
        typical = None
    cap = resolution_cap_bytes(filename, is_movie)
    if typical and typical > 0:
        # Serie entera pesada: la mediana también está inflada -> techo.
        if cap and typical > cap:
            return cap, "resolution_cap"
        return int(typical), "typical"
    if cap:
        return cap, "resolution_cap"
    return None, None


def is_slim_candidate(file_size: int, target: int | None,
                      ratio: float = DEFAULT_SLIM_RATIO,
                      min_saving_bytes: int = DEFAULT_MIN_SAVING_BYTES) -> bool:
    """True si *file_size* está lo bastante por encima de *target* como
    para proponer adelgazarlo: ratio alcanzado Y ahorro absoluto mínimo
    (evita proponer capítulos de 90 MB vs típico de 40 MB)."""
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        r = DEFAULT_SLIM_RATIO
    if not target or target <= 0 or not file_size or file_size <= 0:
        return False
    if r <= 1.0:
        r = DEFAULT_SLIM_RATIO
    if file_size < target * r:
        return False
    try:
        if (file_size - target) < int(min_saving_bytes or 0):
            return False
    except (TypeError, ValueError):
        pass
    return True


def _parse_season(filename: str) -> int | None:
    try:
        from core.download_quality import _parse_season_episode
        se = _parse_season_episode(filename or "")
        return se[0] if se else None
    except Exception:
        return None


def find_slim_candidates(files: list, series_name: str = "",
                         is_movie: bool = False,
                         desired_bytes: int | None = None,
                         ratio: float = DEFAULT_SLIM_RATIO,
                         min_saving_bytes: int = DEFAULT_MIN_SAVING_BYTES,
                         sizes_for_typical: list[int] | None = None) -> list[dict]:
    """Filtra *files* ([(nombre, tamaño), ...]) a los adelgazables.

    - Ignora no-vídeo (nfo/jpg/srt...) via core.renamer.is_video_file.
    - *sizes_for_typical*: tamaños hermanos para la mediana; por defecto
      los de *files* (solo vídeo >0). Para una temporada concreta, pasar
      aquí los de esa temporada aunque *files* sea el árbol entero.
    - Devuelve [{name, size, target, source, ratio, saving}, ...]
      ordenados de mayor a menor peso.
    """
    try:
        from core.renamer import is_video_file
    except Exception:
        def is_video_file(n):  # fallback mínimo
            return (n or "").lower().rsplit(".", 1)[-1] in (
                "mkv", "mp4", "avi", "ts", "m2ts", "wmv", "mov", "webm")

    video = [(n, int(s)) for n, s in (files or [])
             if n and s and int(s) > 0 and is_video_file(n)]
    if not video:
        return []
    if sizes_for_typical is None:
        sizes_for_typical = [s for _n, s in video]
    out = []
    for name, size in video:
        target, source = resolve_target_size(
            sizes_for_typical, filename=name, is_movie=is_movie,
            desired_bytes=desired_bytes)
        if not target:
            continue
        if is_slim_candidate(size, target, ratio, min_saving_bytes):
            out.append({
                "name": name,
                "size": size,
                "target": target,
                "source": source,
                "ratio": size / target if target else 0,
                "saving": size - target,
                "season": _parse_season(name),
            })
    out.sort(key=lambda d: d["size"], reverse=True)
    return out


def find_oversize_flat(files: list, series_name: str = "",
                      is_movie: bool = False,
                      desired_bytes: int | None = None,
                      ratio: float = DEFAULT_SLIM_RATIO,
                      min_saving_bytes: int = DEFAULT_MIN_SAVING_BYTES) -> list[dict]:
    """Filas planas de gordos para la vista "Por capítulo" de Liberar
    espacio: los mismos candidatos que find_slim_candidates, pero en una
    sola lista con temporada/episodio parseados.

    - Series: la mediana se calcula por temporada (hermanos de verdad, no
      mezcla calidades de temporadas distintas).
    - Películas: no hay temporadas; la mediana se calcula por resolución
      (ver resolution_label): un grupo de una sola peli no tiene mediana
      y cae al techo por resolución de su nombre.
    - *files*: [(nombre, tamaño), ...]. Devuelve [{name, season, episode,
      size, target, source, ratio, saving, resolution}] ordenados de mayor
      a menor ratio (los más desviados primero).
    """
    try:
        from core.download_quality import _parse_season_episode, resolution_label
    except Exception:
        def _parse_season_episode(n):
            return None

        def resolution_label(n):
            return "SD"

    groups: list = []  # [(sizes_para_mediana, [(nombre, tamaño), ...])]
    if not is_movie:
        by_season: dict = {}
        for name, size in (files or []):
            try:
                se = _parse_season_episode(name or "")
                key = se[0] if se else 0
            except Exception:
                key = 0
            by_season.setdefault(key, []).append((name, size))
        for grp in by_season.values():
            groups.append(([s for _n, s in grp], grp))
    else:
        by_res: dict = {}
        for name, size in (files or []):
            try:
                key = resolution_label(name or "")
            except Exception:
                key = "SD"
            by_res.setdefault(key, []).append((name, size))
        for grp in by_res.values():
            # Grupo de 1: sin mediana posible -> lista vacía para que
            # resolve_target_size caiga al techo por resolución.
            groups.append(([s for _n, s in grp] if len(grp) > 1 else [], grp))

    out = []
    for sizes, grp in groups:
        for d in find_slim_candidates(
                list(grp), series_name, is_movie,
                desired_bytes, ratio, min_saving_bytes,
                sizes_for_typical=sizes):
            try:
                se = _parse_season_episode(d["name"] or "")
            except Exception:
                se = None
            d["season"] = se[0] if se else None
            d["episode"] = se[1] if se else None
            try:
                d["resolution"] = resolution_label(d["name"] or "")
            except Exception:
                d["resolution"] = ""
            out.append(d)
    out.sort(key=lambda d: d.get("ratio") or 0, reverse=True)
    return out


def group_by_season(candidates: list[dict]) -> dict:
    """{temporada: [candidatos]}; sin número reconocido -> clave 0."""
    grouped: dict = {}
    for c in candidates or []:
        key = c.get("season")
        try:
            key = int(key) if key is not None else 0
        except (TypeError, ValueError):
            key = 0
        grouped.setdefault(key, []).append(c)
    return grouped
