"""
Detección de contenido para adultos a partir del nombre de archivo.

Existe por un caso real: "Star Wars Xxx, A Porn Parody 2011 -- (...actrices...).mkv"
acabó subido a /datos2/seriespeques/, la carpeta infantil del servidor.
detect_episode() tira "Xxx", "A Porn Parody" y el reparto como basura del
nombre, dejando "Star Wars" a secas, que casaba con una carpeta ya existente
de una serie preescolar de Star Wars.

La conclusión es que esos términos no son basura: son la señal más fiable que
trae el nombre, y hay que mirarla ANTES de limpiarlo.

Criterio deliberadamente asimétrico. Un falso positivo (una película normal
marcada como adulta) solo hace que el archivo se salte la clasificación
automática y haya que tratarlo a mano: molesto y poco más. Un falso negativo
mete porno en la carpeta de los niños. Ante la duda, marcar.
"""

import re

# Términos inequívocos. Se buscan como palabra suelta (\b) sobre el nombre
# normalizado, no como subcadena, o "sex" saltaría dentro de "Middlesex" y
# "essex", y "anal" dentro de "analógico" o "Analyze That".
_ADULT_TERMS = [
    "xxx", "porn", "porno", "pornography", "hentai", "milf", "gangbang",
    "creampie", "blowjob", "handjob", "threesome", "bukkake", "deepthroat",
    "cumshot", "hardcore sex", "softcore", "erotica", "erotico", "erotica",
    "brazzers", "bangbros", "naughtyamerica", "realitykings", "evilangel",
    "blacked", "tushy", "vixen", "pornhub", "xvideos", "xhamster", "youporn",
    "redtube", "onlyfans", "camgirl", "jav", "uncensored",
    "nsfw", "adultos", "solo adultos",
]

# Expresiones de varias palabras: aquí sí interesa la frase completa, porque
# "parody" a secas es de lo más normal (Spaceballs, Scary Movie...) y solo
# levanta sospecha cuando va junto a lo otro.
_ADULT_PHRASES = [
    r"\bporn\s+parod(y|ia)\b",
    r"\bparodia\s+porno\b",
    r"\bversion\s+porno\b",
    r"\badult\s+(movie|film|parody)\b",
    r"\bpelicula\s+para\s+adultos\b",
    r"\bsex\s+(tape|scene|video)\b",
    r"\b18\s*\+\b",
]

_WORD_RE = {t: re.compile(r"\b" + re.escape(t) + r"\b") for t in _ADULT_TERMS}
_PHRASE_RE = [re.compile(p) for p in _ADULT_PHRASES]

# Estudios cuyo catálogo entero es porno: si el nombre trae uno, da igual el
# resto. Van aparte porque son nombres propios, no términos genéricos.
_ADULT_STUDIOS = [
    "axel braun", "digital playground", "wicked pictures", "adam and eve",
    "private black label", "marc dorcel", "kink com",
]


def _normalize(name: str) -> str:
    """Minúsculas y separadores unificados a espacios, para que '.', '_' y
    '-' no impidan que \\b encuentre las palabras."""
    return re.sub(r"[^a-z0-9+]+", " ", (name or "").lower()).strip()


def looks_adult(name: str) -> bool:
    """True si *name* (nombre de archivo, con o sin extensión) tiene marcas
    claras de contenido para adultos.

    Pensado para llamarse con el nombre ORIGINAL, antes de que
    detect_episode() lo limpie -- después de limpiarlo la señal ya no está.
    """
    norm = _normalize(name)
    if not norm:
        return False

    for studio in _ADULT_STUDIOS:
        if studio in norm:
            return True

    for rx in _PHRASE_RE:
        if rx.search(norm):
            return True

    for term, rx in _WORD_RE.items():
        if rx.search(norm):
            return True

    return False


def adult_reason(name: str) -> str:
    """Qué disparó la detección, para poder explicarlo en el log y en la
    interfaz en vez de soltar un 'omitido' sin motivo."""
    norm = _normalize(name)
    hits = []

    for studio in _ADULT_STUDIOS:
        if studio in norm:
            hits.append(studio)
    for p, rx in zip(_ADULT_PHRASES, _PHRASE_RE):
        m = rx.search(norm)
        if m:
            hits.append(m.group(0))
    for term, rx in _WORD_RE.items():
        if rx.search(norm):
            hits.append(term)

    return ", ".join(sorted(set(hits))) if hits else ""
