"""
Consulta a eldoblaje.com (base de datos de doblaje al castellano
mantenida por la comunidad) para dar a la IA datos reales y verificables
sobre hasta qué episodio llega el doblaje castellano de una serie -- en
vez de dejar que "adivine" el dato (ver
core/missing_episodes_ai.py::DUB_CHECK_MODEL: probado en vivo que incluso
el modelo más fiable, sin este texto, puede quedarse sin datos reales
para responder; con el texto de eldoblaje.com como contexto, extrae el
dato correcto de verdad, confirmado con Bleach -- "Consta de 366
episodios, de los que solo fueron doblados los 109 primeros").

Sin API de pago -- consulta directa a las páginas públicas del propio
sitio (GET plano + extracción con expresiones regulares, sin
BeautifulSoup ni ninguna dependencia nueva, mismo criterio que el resto
de esta app para HTML/listados sencillos, ver
core/ftp_client.py::_parse_recursive_list_sections).
"""

import html
import re

import requests

from core.applog import get_logger

_log = get_logger("aIBechos.eldoblaje", "ai_fallback.log")

_BASE = "https://www.eldoblaje.com/datos"

# Cada resultado de búsqueda es un <a href="FichaPelicula.asp?id=NNN"
# class="bodyclass">NOMBRE</a> -- películas, series, actores y otras
# fichas conviven en la misma lista, distinguibles solo por el texto
# adjunto (p.ej. "BLEACH [serie de animación]" vs "BLEACH" a secas para
# la película). No hay parámetro de tipo en la URL de búsqueda.
_RESULT_RE = re.compile(r'href="FichaPelicula\.asp\?id=(\d+)"[^>]*>([^<]+)</a>', re.IGNORECASE)

# El bloque de texto libre "Más información" (donde el sitio suele
# indicar cuántos episodios se doblaron de verdad) vive en un <font
# color="#333333"> justo después de la cabecera de esa sección --
# extraído verificando contra el HTML real de varias fichas antes de
# escribir este regex, no adivinado.
_INFO_RE = re.compile(
    r'arial18white">\s*M&aacute;s\s+informaci&oacute;n.*?'
    r'<font color="#333333">\s*(.*?)\s*</font>',
    re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def search_series(title: str, timeout: int = 10) -> list[dict]:
    """[{"id": int, "name": str}, ...] -- solo resultados marcados como
    serie (de animación o no), nunca películas ni fichas de actores/
    directores -- para *title* en eldoblaje.com. [] si no hay resultados
    o falla cualquier cosa (sin conexión, formato inesperado del
    sitio...) -- nunca lanza, quien llama debe poder seguir funcionando
    igual que si esta consulta no existiera."""
    if not title:
        return []
    try:
        resp = requests.get(f"{_BASE}/KeywordResults.asp", params={"keyword": title}, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        _log.warning("eldoblaje.com: fallo al buscar '%s': %s", title, e)
        return []
    results = []
    for m in _RESULT_RE.finditer(resp.text):
        name = html.unescape(m.group(2)).strip()
        if "serie" not in name.lower():
            continue
        results.append({"id": int(m.group(1)), "name": name})
    return results


def get_dub_summary(fichapelicula_id: int, timeout: int = 10) -> str:
    """Texto libre de la sección "Más información" de esa ficha -- "" si
    no se encuentra esa sección (ficha sin ese apartado relleno) o falla
    cualquier cosa. Nunca lanza."""
    if not fichapelicula_id:
        return ""
    try:
        resp = requests.get(f"{_BASE}/FichaPelicula.asp", params={"id": fichapelicula_id}, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        _log.warning("eldoblaje.com: fallo al leer ficha %s: %s", fichapelicula_id, e)
        return ""
    m = _INFO_RE.search(resp.text)
    if not m:
        return ""
    text = _TAG_RE.sub(" ", m.group(1))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---- Corte de doblaje automático (sin IA) ----
#
# El texto libre de "Más información" es la verdad de referencia sobre el
# doblaje al castellano (base de datos mantenida por la comunidad), pero es
# prosa, no un dato estructurado -- por eso hasta ahora solo lo leía la IA
# (ver _ask_ai_about_current_missing_ep_show). parse_dub_cutoff() extrae de
# esa prosa lo mismo que la IA: {temporada: último_episodio_doblado},
# formato que entiende filter_missing_by_dub_cutoff (0 = esa temporada no
# tiene NADA doblado). Solo produce entradas cuando el texto lo dice
# EXPLÍCITO (temporadas "hasta la N", "temporada N sin doblar", "los N
# primeros doblados"...); si no hay nada concluyente devuelve {} y quien
# llama sigue con el chequeo TMDB como si esta consulta no existiera --
# un falso "doblado" ocultaría capítulos de verdad, un falso "no doblado"
# escondería lo contrario, así que ante la duda no se dice nada.

_NUM_WORDS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12,
    "primera": 1, "primero": 1, "segunda": 2, "segundo": 2,
    "tercera": 3, "tercero": 3, "cuarta": 4, "cuarto": 4,
    "quinta": 5, "quinto": 5, "sexta": 6, "sexto": 6,
}

# "hasta la temporada 2" / "dobladas hasta la tercera" / "las 2 primeras
# temporadas dobladas" -- todo lo posterior a cap se considera sin doblar.
_UNTIL_SEASON_RE = re.compile(
    r"(?:hasta\s+(?:la\s+)?temporada\s+(\d+|primera|segunda|tercera|cuarta|quinta|sexta)"
    r"|(?:las\s+)?(\d+|una|dos|tres|cuatro|cinco|seis)\s+primeras?\s+temporadas?\s+dobladas?)",
    re.IGNORECASE)
# "temporada 3 sin doblar" / "la 3ª temporada no fue doblada" / "temporada 2
# pendiente de doblaje" -- mención de temporada + negación cerca.
_SEASON_REF_RE = re.compile(
    r"(?:temporada\s+(\d+)|(\d+)\s*[ªa]?\s*temporada)",
    re.IGNORECASE)
_NEGATIVE_RE = re.compile(
    r"sin\s+doblar(?:se)?|no\s+(?:fue|fueron|ha\s+sido|han\s+sido|se|est[áa])[^.]{0,40}dobla"
    r"|no\s+consta[^.]{0,40}dobla|sin\s+doblaje|no\s+doblada|pendiente\s+de\s+doblaje",
    re.IGNORECASE)
# "solo fueron doblados los 109 primeros" / "se doblaron 50 episodios" --
# cuenta ABSOLUTA de episodios (de la serie entera): necesita season_sizes
# {temporada: nº_episodios} para repartirla por temporadas (ver abajo).
_ABSOLUTE_RE = re.compile(
    r"(?:solo\s+)?(?:fueron|han\s+sido|se)\s+doblados?\s+(?:los\s+)?(\d+)\s+primeros?"
    r"|[úu]nicamente\s+se\s+doblaron\s+(\d+)",
    re.IGNORECASE)
# Toda la serie sin doblar (sin mencionar temporadas): "la serie no fue
# doblada" / "no se dobló" / "sin doblaje al castellano" / "no consta su
# doblaje". Solo vale si el texto NO da ningún corte por temporadas más
# específico (lo específico manda).
_WHOLE_NEGATIVE_RE = re.compile(
    r"la\s+serie\s+no\s+(?:fue|ha\s+sido)\s+doblada|no\s+se\s+dobl[óo]"
    r"|sin\s+doblaje(?:\s+al\s+castellano)?|no\s+(?:consta|existe|hay)\s+(?:su\s+|el\s+)?doblaje",
    re.IGNORECASE)


def _word_number(token: str) -> int | None:
    """Dígitos o palabra ("dos", "tercera") a int -- None si no es número."""
    token = (token or "").strip().lower()
    if token.isdigit():
        return int(token)
    return _NUM_WORDS.get(token)


def has_absolute_dub_count(text: str) -> bool:
    """True si el texto trae una cuenta absoluta de doblados ("los 109
    primeros") -- quien llama lo usa para saber que merece la pena pedir
    los tamaños de temporada a TMDB y reintentarlo con season_sizes, en vez
    de rendirse al primer {} (ver parse_dub_cutoff)."""
    return bool(text) and bool(_ABSOLUTE_RE.search(text))


def parse_dub_cutoff(text: str, seasons: list[int] | tuple[int, ...],
                     season_sizes: dict | None = None) -> dict:
    """{temporada: último_episodio_doblado} a partir del texto libre de
    eldoblaje.com -- 0 significa "nada doblado de esa temporada" (ver
    filter_missing_by_dub_cutoff: ep <= 0 no deja pasar ningún episodio).
    *seasons* acota sobre qué temporadas se emite veredicto (normalmente
    las que faltan de esa serie); lo que el texto declara doblado ENTERO
    no necesita entrada (visible por defecto, igual que un corte ausente
    en el veredicto de la IA). *season_sizes* ({temporada: nº_episodios})
    solo hace falta para repartir cuentas absolutas ("los 109 primeros");
    sin él, esas se ignoran. Sin nada concluyente (o sin texto) → {}."""
    result: dict = {}
    if not text or not seasons:
        return result
    scope = set(seasons)

    m = _UNTIL_SEASON_RE.search(text)
    if m:
        cap = _word_number(m.group(1) or m.group(2) or "")
        if cap is not None:
            for s in scope:
                if s > cap:
                    result[s] = 0
            # "Hasta la N" ya es un veredicto completo: lo posterior queda
            # en 0 y lo anterior visible (sin entrada). No se mezcla con
            # negativos sueltos que puedan contradecirlo.
            return result

    for sm in _SEASON_REF_RE.finditer(text):
        season = int(sm.group(1) or sm.group(2))
        if season not in scope or season in result:
            continue
        # Ventana acotada a LA FRASE de la mención (hasta el punto anterior
        # y el siguiente, máx. 60 caracteres por lado): sin esto, el "no
        # fue doblada" de la frase de la T3 contaminaba a la T2 vecina
        # ("La temporada 2 se dobló en 2005. La temporada 3 no fue
        # doblada." marcaba 0 en AMBAS).
        back = text[max(0, sm.start() - 60):sm.start()].rsplit(".", 1)[-1]
        fwd = text[sm.end():sm.end() + 60].split(".", 1)[0]
        window = back + text[sm.start():sm.end()] + fwd
        if _NEGATIVE_RE.search(window):
            result[season] = 0

    m = _ABSOLUTE_RE.search(text)
    if m and season_sizes:
        total = int(m.group(1) or m.group(2))
        rest = total
        for s in sorted(scope):
            size = season_sizes.get(s, season_sizes.get(str(s), 0)) or 0
            if size <= 0:
                continue
            if rest >= size:
                rest -= size
                continue
            result[s] = rest
            for later in sorted(scope):
                if later > s:
                    result[later] = 0
            break

    if not result and _WHOLE_NEGATIVE_RE.search(text):
        # Sin corte más específico y serie declarada sin doblar entera.
        for s in scope:
            result[s] = 0
    return result
