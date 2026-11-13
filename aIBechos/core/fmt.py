"""Formato de tamaños y velocidades para mostrar."""

import re


def fmt_speed(bps):
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


def fmt_size(nbytes):
    for unit, divisor in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if nbytes >= divisor:
            return f"{nbytes / divisor:.1f} {unit}"
    return f"{nbytes} B"


def parse_size_human(s: str) -> float:
    """Bytes (aprox.) a partir de un size_human de aMule como "450,5 MB"
    (texto, con coma decimal según la locale) -- para ordenar la tabla de
    Descargar por tamaño real y no por orden alfabético del texto."""
    if not s:
        return 0.0
    parts = s.strip().split()
    if not parts:
        return 0.0
    try:
        val = float(parts[0].replace(",", "."))
    except ValueError:
        return 0.0
    if len(parts) > 1:
        mult = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
        return val * mult.get(parts[1].upper(), 1)
    return val


def fmt_transfer(bps: int) -> str:
    """Cantidad sin "/s" (la pestaña Descargas añade "/s" detrás): B/KB/MB
    con los decimales que usa la cola de aMule."""
    if bps < 1024:
        return f"{bps} B"
    if bps < 1024 * 1024:
        return f"{bps/1024:.1f} KB"
    return f"{bps/1024/1024:.2f} MB"


# Año al final de un título tal como viene del servidor: "Los Vengadores
# (2012)", "La singular vida de Ibelin 2024", "Sherlock Holmes 2 -2011".
_TRAILING_YEAR_RE = re.compile(r"[\s._-]*[(\[]?\s*((?:18|19|20)\d{2})\s*[)\]]?\s*$")


def title_with_year(name: str, year) -> tuple[str, str | None]:
    """("Título (año)", año_distinto|None) para enseñar *name* con su año
    UNA vez: los nombres de Jellyfin/Plex/carpeta ya suelen llevarlo (en 618
    de 955 películas de Liberar espacio salía "Los Vengadores (2012)
    (2012)"). Si el nombre trae un año DISTINTO al de los metadatos se deja
    tal cual y se devuelve el de los metadatos aparte: suele ser una ficha
    mal identificada ("Iron Man 3 (2013)" con año 2008). Un título que es
    solo un año ("1923", "1984") no se toca."""
    name = (name or "").strip()
    y = str(year or "").strip()
    if not y.isdigit():
        return name, None
    m = _TRAILING_YEAR_RE.search(name)
    base = name[:m.start()].strip() if m else name
    if not m or not base:
        # Nombre de archivo en bruto con el año en medio ("El hombre
        # menguante (2025)-BDRIP.1080p..."): ya lo dice, no se repite.
        if base and re.search(rf"(?<!\d){y}(?!\d)", name):
            return name, None
        return f"{name} ({y})", None
    if int(m.group(1)) == int(y):
        return f"{base} ({y})", None
    return name, y
