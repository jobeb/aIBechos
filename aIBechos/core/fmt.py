"""Formato de tamaños y velocidades para mostrar."""


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
