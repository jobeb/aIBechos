"""
Helper para construir queries de aMule con template por serie.

El usuario configura en config.json:
  series_search_patterns = {"That Time I Got Reincarnated as a Slime": "Slime {temporada}x{episodio:02d}"}

Vars disponibles en el template:
  {serie}, {temporada}, {temporada:02d}, {episodio}, {episodio:02d}, {año}
Si el template no contiene {temporada}/{episodio}, se añade " {temporada}x{episodio:02d}" al final.
Clave = nombre exacto TMDB (r['name']), case-sensitive tal cual lo devuelve TMDB.
"""

import re


_DEFAULT_AMULE_SUFFIX = " {temporada}x{episodio:02d}"


def filename_stem_to_search_terms(filename: str) -> str:
    """'Doctor.Who.1108.EI.imperio.de.la.muerte.720p.mkv' ->
    'Doctor Who 1108 EI imperio de la muerte 720p'.

    Términos de búsqueda a partir de un nombre de archivo para cuando no
    se reconoce numeración SxxExx/NxNN (ver _slim_query_for): aMule
    rechaza puntos/guiones en la expresión ("syntax error / Undefined
    search expression"), así que se normalizan a espacios. Paréntesis,
    corchetes y comillas también fuera: el parser booleano de aMule los
    interpreta (un paréntesis sin cerrar o una comilla suelta rompen la
    expresión) y como keywords de Kad no aportan nada. Sin extensión,
    sin dobles espacios."""
    stem = (filename or "")
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    terms = re.sub(r"[._\-()\[\]{}\"'’`]+", " ", stem)
    terms = re.sub(r"\s+", " ", terms).strip()
    return terms


def sanitize_search_query(query: str) -> str:
    """Quita ()[]{} de una query ya construida (su contenido se conserva
    como términos): el parser booleano de aMule puede rechazarlos
    ("syntax error / Undefined search expression", visto de verdad con
    un capítulo de Doctor Who) y como keywords de Kad no aportan nada.
    Las comillas NO se tocan aquí: un template personalizado podría
    usarlas a propósito para frase exacta (en el fallback de nombre de
    archivo ya las quita filename_stem_to_search_terms)."""
    q = re.sub(r"[()\[\]{}]+", " ", query or "")
    return re.sub(r"\s+", " ", q).strip()


def _normalize(s: str) -> str:
    return (s or "").strip()


def _maybe_add_castellano(q: str, prefers: bool) -> str:
    if prefers and "castellano" not in q.lower():
        return f"{q} castellano" if q else "castellano"
    return q


def build_amule_query(series_name: str, season: int, episode: int,
                      year: str = "", templates: dict | None = None,
                      prefers_castellano: bool = False) -> str:
    """
    Construye la query para EcClient.iter_search.

    - Si hay template para series_name en templates, lo usa.
    - Si el template no trae {temporada}/{episodio}, le añade el sufijo por defecto.
    - Si no hay template, devuelve f"{series_name} {season}x{episode:02d}".
    - Para películas (season/episode None): devuelve el template formateado sin sufijo,
      o series_name si no hay template.
    - Si prefers_castellano=True y la query no trae 'castellano', lo añade como
      indicador (el nombre del archivo original contenía Castellano/castellano).

    templates: dict {nombre_TMDB: template_str} o None.
    """
    templates = templates or {}
    # Búsqueda exacta por nombre TMDB; fallback case-insensitive por si el usuario
    # escribió con distinta capitalización.
    tmpl = templates.get(series_name)
    if tmpl is None:
        # fallback case-insensitive / strip
        low = series_name.strip().lower()
        for k, v in templates.items():
            if k.strip().lower() == low:
                tmpl = v
                break

    if tmpl is None or not str(tmpl).strip():
        # Sin template → comportamiento de siempre
        if season is not None and episode is not None:
            return _maybe_add_castellano(f"{series_name} {season}x{episode:02d}", prefers_castellano)
        return _maybe_add_castellano(series_name, prefers_castellano)

    tmpl = str(tmpl)

    # Si es query de episodio y el template no menciona temporada/episodio,
    # añadir sufijo para no perder el nº de capítulo.
    if season is not None and episode is not None:
        if "{temporada}" not in tmpl and "{episodio}" not in tmpl:
            # Tampoco variantes sin llaves tipo SxxEyy; asumimos que quiere base + sufijo
            tmpl = tmpl.rstrip() + _DEFAULT_AMULE_SUFFIX

    # Preparar vars para format
    fmt_vars = {
        "serie": series_name,
        "temporada": season if season is not None else "",
        "episodio": episode if episode is not None else "",
        "año": year or "",
        "ano": year or "",
    }
    # Soportar :02d via format spec: Python ya lo maneja si pasamos int
    # Necesitamos pasar ints para que {temporada:02d} funcione
    if season is not None:
        fmt_vars["temporada"] = season
    if episode is not None:
        fmt_vars["episodio"] = episode

    try:
        # Usar str.format con los vars; si faltan vars, deja placeholder
        q = tmpl.format(**fmt_vars)
    except Exception:
        # Si el template tiene sintaxis rota, fallback a comportamiento por defecto
        if season is not None and episode is not None:
            return f"{series_name} {season}x{episode:02d}"
        return series_name

    # Limpiar dobles espacios
    q = re.sub(r"\s{2,}", " ", q).strip()
    # Si hay template personalizado se respeta tal cual (no añadir castellano automáticamente)
    has_custom = tmpl is not None and str(tmpl).strip() != ""
    if not has_custom and prefers_castellano and "castellano" not in q.lower():
        q = f"{q} castellano" if q else "castellano"
    return q if q else f"{series_name} {season}x{episode:02d}" if season is not None else series_name


def build_amule_season_query(series_name: str, season: int,
                             templates: dict | None = None,
                             prefers_castellano: bool = False) -> str:
    """Query para 'toda la temporada' (botón 3x). Reusa template base sin episodio."""
    templates = templates or {}
    tmpl = templates.get(series_name)
    if tmpl is None:
        low = series_name.strip().lower()
        for k, v in templates.items():
            if k.strip().lower() == low:
                tmpl = v
                break
    has_custom = tmpl is not None and str(tmpl).strip() != ""
    if tmpl is None or not str(tmpl).strip():
        return _maybe_add_castellano(f"{series_name} {season}x", prefers_castellano)

    tmpl = str(tmpl)
    # Para temporada, si el template trae {episodio}, lo quitamos y dejamos solo base
    # Ej: "Slime {temporada}x{episodio:02d}" → "Slime 3x"
    if "{episodio" in tmpl:
        # Reemplazar la parte de episodio por vacío y limpiar
        tmpl = re.sub(r"\s*\{episodio[^}]*\}", "", tmpl)
        tmpl = re.sub(r"\s*x\s*$", "x", tmpl)  # evitar "Slime 3 x"
        # Si aún queda "x" suelta sin episodio, asegurar formato " {temporada}x"
        if "{temporada" not in tmpl:
            tmpl = tmpl.rstrip() + f" {season}x"
        else:
            try:
                q = tmpl.format(serie=series_name, temporada=season, episodio="", año="")
                q = re.sub(r"\s{2,}", " ", q).strip()
                # Asegurar sufijo x
                if not q.endswith("x"):
                    q = q.rstrip() + "x"
                if has_custom:
                    return q
                return _maybe_add_castellano(q, prefers_castellano)
            except Exception:
                return _maybe_add_castellano(f"{series_name} {season}x", prefers_castellano)
        try:
            q = tmpl.format(serie=series_name, temporada=season, episodio="", año="")
            q = re.sub(r"\s{2,}", " ", q).strip()
            if has_custom:
                return q
            return _maybe_add_castellano(q, prefers_castellano)
        except Exception:
            return _maybe_add_castellano(f"{series_name} {season}x", prefers_castellano)

    # Template sin episodio → solo formatear temporada
    try:
        q = tmpl.format(serie=series_name, temporada=season, episodio="", año="")
        q = re.sub(r"\s{2,}", " ", q).strip()
        if season is not None and "{temporada" not in str(templates.get(series_name, "")):
            # Si el template era solo "Slime" sin vars, añadir temporada
            if str(q).strip().lower() == str(templates.get(series_name, "")).strip().lower():
                q = f"{q} {season}x"
        if has_custom:
            return q
        return _maybe_add_castellano(q, prefers_castellano)
    except Exception:
        return _maybe_add_castellano(f"{series_name} {season}x", prefers_castellano)
