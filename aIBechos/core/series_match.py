"""
Comparación difusa de nombres de series para reutilizar carpetas FTP ya
existentes cuando el nombre generado hoy no coincide exactamente con el que
se usó la vez anterior (idioma, artículos, nombre corto vs largo, etc.).
"""

import difflib
import re
import unicodedata

_ARTICLES = re.compile(r"^(the|a|an|el|la|los|las|una|uno)\s+")


def normalize_series_name(name: str) -> str:
    """Minúsculas, sin acentos ni puntuación, sin artículo inicial."""
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    name = _ARTICLES.sub("", name)
    return name


_MIN_SUBSTRING_BOOST_LEN = 4
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# Contenido de cualquier grupo entre paréntesis/corchetes/llaves.
_BRACKET_RE = re.compile(r"[(\[{]([^)\]}]*)[)\]}]")


def _plain_words(text: str) -> list:
    """Palabras en minúscula, SIN quitar el artículo inicial -- a
    diferencia de normalize_series_name(), que sí lo quita y por eso no
    sirve para comparar el contenido de un paréntesis palabra a palabra
    ("(The Simpsons)" perdería el "the" y dejaría de casar)."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()


def series_similarity(a: str, b: str, strict: bool = False,
                      allow_annotation: bool = False) -> float:
    """0..1 — cuánto se parecen dos nombres de serie una vez normalizados.

    Años distintos = nunca la misma obra, SIEMPRE (independientemente de
    *strict*): si ambos nombres traen un año identificable (p.ej.
    "Godzilla (1954)" vs "Godzilla (2014)") y no coincide, se devuelve
    0.0 sin más -- por muy parecido que sea el resto del texto, son
    títulos diferentes con el mismo nombre, no el mismo título repetido.
    Si solo uno de los dos trae año (o ninguno), esta comprobación no
    aplica.

    Si uno es PREFIJO literal del otro (nombre corto vs largo) se
    considera una coincidencia fuerte aunque el ratio de caracteres sea
    bajo -- útil para "El 47" vs "El 47 (2024)", o para encontrar el
    mismo contenido subido con otro nombre de release ("Pelicula (2024)"
    vs "Pelicula.2024.OtraVersion.WEB-DL"). Tiene que ser PREFIJO, no
    "contenido en cualquier parte" -- con "contenido en cualquier parte"
    un nombre corto y genérico que aparece al FINAL de un título largo y
    completamente distinto también disparaba el impulso (caso real: la
    carpeta "Arcadia" se fusionó con "Los 3 de Adabo: Cuentos de Arcadia",
    dos series sin ninguna relación, porque "arcadia" es una subcadena
    literal del segundo nombre; nunca aparecía al principio). Con
    *strict=False* (por defecto, usado para reutilizar carpetas y
    detectar duplicados de subida) esto se admite siempre. Con
    *strict=True* (usado para emparejar títulos REALES entre sí, donde
    una palabra de más SÍ importa) solo se admite si comparten el mismo
    año, o si lo que sobra en el nombre largo son dígitos/espacios, no
    letras -- así "Animal" y "Animal Crackers" ya no se consideran el
    mismo título solo porque uno empiece igual que el otro.

    *allow_annotation* (solo tiene efecto con strict=True) relaja ese modo
    lo justo para admitir el patrón "título (título original)":
    "Desencanto (Disenchantment)", "Los Simpson (The Simpsons)". Es el
    modo que necesita la ELECCIÓN DE CARPETA DE DESTINO (ver
    core/ftp_categories.py::find_existing_category_folder), donde el modo
    laxo es peligroso -- "Star Wars", que es lo que queda de "Star Wars
    Xxx, A Porn Parody 2011" tras limpiar el nombre, puntuaba 0.90 contra
    la carpeta "Star Wars Las aventuras de los jóvenes Jedi" y el archivo
    acababa en la categoría infantil (caso real: porno subido a
    /datos2/seriespeques/) -- pero el modo estricto a secas se pasa de
    frenada y rompe la reutilización legítima de carpetas.

    El modo laxo se queda como estaba a propósito: lo usa la detección de
    duplicados, donde un falso positivo solo evita una subida repetida y
    el patrón "nombre corto vs nombre de release largo" es justo lo que
    hay que reconocer."""
    na, nb = normalize_series_name(a), normalize_series_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    year_a, year_b = _YEAR_RE.search(na), _YEAR_RE.search(nb)
    same_year = bool(year_a and year_b and year_a.group() == year_b.group())
    if year_a and year_b and not same_year:
        return 0.0

    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= _MIN_SUBSTRING_BOOST_LEN and longer.startswith(shorter):
        remainder = longer.replace(shorter, "", 1)
        if not strict:
            ratio = max(ratio, 0.90)
        elif same_year or not any(c.isalpha() for c in remainder):
            ratio = max(ratio, 0.90)
        elif allow_annotation and _is_annotation_remainder(a, b, remainder):
            ratio = max(ratio, 0.90)
    return ratio


def _is_annotation_remainder(a: str, b: str, normalized_remainder: str) -> bool:
    """True si lo que le sobra al nombre largo respecto al corto es una
    anotación y no texto libre que continúa el título.

    Cuenta como anotación:
      - solo dígitos/espacios (un año suelto: "Ranma" vs "Ranma 1989")
      - texto que en el nombre ORIGINAL va entre paréntesis o corchetes
        ("Desencanto" vs "Desencanto (Disenchantment)")

    Hay que mirar los nombres originales porque normalize_series_name()
    borra la puntuación, y sin paréntesis no hay forma de distinguir
    "Desencanto (Disenchantment)" de "Star Wars Las aventuras...".
    """
    if not any(c.isalpha() for c in normalized_remainder):
        return True

    original_long = a if len(a) >= len(b) else b

    # Se comparan CONJUNTOS DE PALABRAS, no posiciones dentro del texto:
    # normalize_series_name() quita el artículo inicial, así que contar
    # caracteres sobre el original se desalinea en cuanto hay uno ("Los
    # Simpson" -> "simpson" deja el índice a mitad de palabra).
    bracketed = " ".join(_BRACKET_RE.findall(original_long))
    if not bracketed:
        return False

    bracket_words = set(_plain_words(bracketed))
    remainder_words = set(normalized_remainder.split())
    return bool(remainder_words) and remainder_words <= bracket_words


def best_match(desired: str, candidates, min_ratio: float = 0.55,
               strict: bool = False, allow_annotation: bool = False):
    """Devuelve (mejor_candidato, ratio) entre *candidates*, o (None, 0.0)
    si ninguno alcanza min_ratio.

    *strict* y *allow_annotation* se pasan tal cual a series_similarity --
    ver allí. La combinación strict=True + allow_annotation=True es la que
    necesita la elección de carpeta de destino."""
    best_name, best_ratio = None, 0.0
    for c in candidates:
        r = series_similarity(desired, c, strict=strict, allow_annotation=allow_annotation)
        if r > best_ratio:
            best_name, best_ratio = c, r
    if best_ratio >= min_ratio:
        return best_name, best_ratio
    return None, 0.0


def best_match_with_year(desired: str, candidates, known_year, min_ratio: float = 0.90):
    """Como best_match, pero para cuando *desired* no trae año en el
    propio texto (típico: el título tal cual lo da un servidor de medios,
    p.ej. "Ranma ½") y *known_year* es su año de estreno real ya conocido
    de antemano (p.ej. first_air_date de TMDB) -- caso real que motivó
    esto: dos carpetas reales "Ranma (1989)" y "Ranma (2024)" (remake con
    el mismo nombre base) junto al título sin año de cada una. Meter el
    año a mano en *desired* ("Ranma ½ (1989)") NO basta -- se probó y el
    ratio de series_similarity se queda en ~0.83, por debajo del 0.90,
    porque el resto del nombre ya no es una substring literal del
    candidato una vez el año se cuela en medio del texto (ver "1 2" de la
    fracción "½"). En vez de eso, aquí se comparan *desired* y cada
    candidato con su PROPIO año ya quitado -- así el año sirve solo para
    CONFIRMAR qué candidato es (year debe coincidir con known_year) sin
    diluir el parecido de texto ni arriesgarse a confundir el remake con
    el original. Devuelve (None, 0.0) si no hay known_year o ningún
    candidato con ese año llega a min_ratio tras quitárselo."""
    if not known_year:
        return None, 0.0
    known_year = str(known_year)
    best_name, best_ratio = None, 0.0
    for c in candidates:
        nc = normalize_series_name(c)
        m = _YEAR_RE.search(nc)
        if not m or m.group() != known_year:
            continue
        stripped = _YEAR_RE.sub("", nc, count=1).strip()
        ratio = series_similarity(desired, stripped)
        if ratio > best_ratio:
            best_name, best_ratio = c, ratio
    if best_ratio >= min_ratio:
        return best_name, best_ratio
    return None, 0.0


def match_names_exclusively(candidates: list, targets: list, min_ratio: float = 0.55,
                            strict: bool = True, lax_fallback: bool = False) -> dict:
    """Empareja cada nombre de *candidates* con como mucho un nombre de
    *targets*, garantizando que ningún target se asigne a más de un
    candidato -- a diferencia de llamar a best_match() de forma
    independiente para cada candidato (usado antes en la herramienta de
    liberar espacio para emparejar carpetas del FTP con títulos de
    Jellyfin/Plex), que permitía que dos carpetas con nombres parecidos
    entre sí (p.ej. una serie y su remake, o "X" y "X: la película")
    "robaran" el mismo título, dejando a una de las dos con datos de
    visionado que en realidad eran de la otra.

    strict=True por defecto (a diferencia de series_similarity) porque el
    único uso actual es precisamente ese -- emparejar títulos reales
    entre sí, donde "Animal" y "Animal Crackers" deben seguir siendo dos
    cosas distintas, no la misma con o sin subtítulo.

    *lax_fallback* relaja esa exigencia SOLO para los candidatos que se
    quedaron sin pareja en la pasada estricta: se hace una segunda pasada
    con strict=False (el modo laxo que usa el cruce FTP de "Episodios que
    faltan", donde un prefijo corto SÍ casa con un título largo, p.ej.
    carpeta "Boruto" contra "Boruto: Naruto Next Generations") contra los
    targets que siguen sin asignar. La exclusividad se mantiene en ambas
    pasadas -- ningún target se asigna dos veces, y el candidato que ya
    consiguió pareja estricta no vuelve a competir. Así la carpeta corta
    que era la ÚNICA forma del título en el servidor sigue quedándose con
    los datos de visionado de Jellyfin/Plex, sin reabrir el caso
    "Animal"/"Animal Crackers" cuando existe un match estricto mejor.

    Se calculan TODAS las parejas candidato-target con ratio >=
    min_ratio, se ordenan de más a menos parecidas, y se van asignando en
    ese orden -- el parecido más fuerte tiene prioridad, sin importar en
    qué orden se procesen los candidatos. Devuelve {candidato: target,
    ...} solo para los que consiguieron pareja."""
    def _assign(pairs):
        assigned_targets = set()
        result = {}
        for ratio, c, t in sorted(pairs, key=lambda p: p[0], reverse=True):
            if c in result or t in assigned_targets:
                continue
            result[c] = t
            assigned_targets.add(t)
        return result, assigned_targets

    strict_pairs = [
        (series_similarity(c, t, strict=strict), c, t)
        for c in candidates for t in targets
        if series_similarity(c, t, strict=strict) >= min_ratio]
    result, assigned_targets = _assign(strict_pairs)

    if lax_fallback:
        remaining_candidates = [c for c in candidates if c not in result]
        remaining_targets = [t for t in targets if t not in assigned_targets]
        if remaining_candidates and remaining_targets:
            lax_pairs = [
                (series_similarity(c, t, strict=False), c, t)
                for c in remaining_candidates for t in remaining_targets
                if series_similarity(c, t, strict=False) >= min_ratio]
            lax_result, _ = _assign(lax_pairs)
            result.update(lax_result)
    return result
