"""Puntuación de resultados de búsqueda de aMule para destacar el capítulo
que más conviene bajar (ver pestaña Descargas de la app).

El primer resultado "ideal" no obliga a ordenar la tabla: solo se usa para
pintar de un color distinto la fila del MEJOR candidato. Criterios que
cuenta el score (de mayor a menor peso):

  * Idioma/audio: español/castellano/spanish/spa (patrón "spa"); un
    release con etiqueta V.O.S. explícita se excluye del todo (original +
    subtítulos, sin doblaje), aunque mencione "spanish/subs" — eso
    describe muchas veces solo los subtítulos — y aunque sea lo único
    disponible. Solo "dual" (doble audio real) lo mantiene como
    emergencia. Igual para italiano SIN español (is_italian_only),
    VOSTFR/francés SIN español (is_french_content), alemán SIN español
    (is_german_content) y portugués/brasileño SIN español
    (is_portuguese_content): con pista en español también, no se
    excluyen. Ni esos idiomas sin español ni el porno se descargan nunca.
  * Calidad de imagen: 4K/2160p > 1080p > 720p > SD
  * Contenedor/flags de grupo notable: MKV > MP4 > AVI; 1xH26x, WEB-DL, HDTV
  * Fiabilidad: más fuentes (sources) y completo (broadcast completo)
  * Tamaño: razonable para la resolución (ni un capítulo de 50 KB ni un
    episodio de 10 GB suelen ser el candidato ideal; se premia el rango
    coherente).
"""

import os
import re
from collections import Counter
from dataclasses import dataclass

from core.amule_client import AmuleSearchResult
from core.series_match import normalize_series_name, series_similarity


_LANG_RE = re.compile(r"\b(?:spa|spanish|español|espanol|castellano|latino|dual)\b",
                      re.IGNORECASE)
# Catalán SIN español: un release solo en catalán se penaliza fuerte para
# que jamás gane a uno en español. Con pista en español también (dual), no
# se penaliza. "cat" suelto
# NO cuenta (aparece en "categoría", "cat-1", "Catwoman"...), pero sí:
#   * el idioma por su nombre (català/catalan/catalá/catala),
#   * vosc ("versió original subtitulada en català"),
#   * "Cat.Subs" / "Catsubs" / "Cat-Subs" (subtítulos en catalán, patrón
#     habitual en nombres de aMule, p.ej.
#     "Crímenes - 1x11...Cat.Subs.x264-Hera_72 (Crims).mkv"),
#   * el tag de idioma "[Cat]" / "(CAT)" del nombre.
_CAT_RE = re.compile(
    r"catal[àa]n?\b|vosc\b|cat(?:\.|_|-|\s)?subs?\b|\[cat\]|\(cat\)",
    re.IGNORECASE)
# Porno explícito (XXX, porn...). Un resultado adulto NUNCA debe ser elegido
# como mejor candidato ni descargarse automáticamente; se excluye en
# best_result (is_adult_content) y, por si alguien usa score_download suelto,
# puntúa 0 (no pasa el umbral MIN_BEST_SCORE). Palabras deliberadamente
# inequívocas: no se incluyen "sex"/"adult"/"erotic" porque aparecen en
# títulos legítimos (Sex and the City, Sex Education, Adult Swim...).
_PORN_RE = re.compile(
    r"\b(?:xxx|porn|porno|hardcore|milf|hentai|onlyfans|bondage|jav|"
    r"creampie|gangbang|bigboobs|bigtits)\b", re.IGNORECASE)
# V.O.S. / VOSE / VOSI / VOSTFR / "versión original subtitulada": audio en el
# idioma ORIGINAL con subtítulos, SIN doblaje en español. El usuario ha pedido
# exclusión total (no solo penalizar): un release V.O.S./VOSTFR nunca debe ser
# elegido ni descargado, aunque sea lo único disponible — igual que el porno
# o el italiano sin español. Ver is_vos_content() / is_french_content().
# Las letras pueden venir separadas por puntos Y/O ESPACIOS (real: "El Arca
# 3x01 ... V O S ..." se coló porque solo se admitían puntos) y con la E
# final también punteada ("V.O.S.E."). La guarda inicial (?<![A-Za-z0-9])
# evita que palabras normales con "vos" dentro ("objetivos", "nuevos",
# "vivos") se marquen como V.O.S. por accidente — mismo criterio que _ITA_RE
# para SUB_ITA; el "_" sí deja pasar ("SUB_VOS" es etiqueta de verdad).
_VOS_RE = re.compile(
    r"(?<![A-Za-z0-9])v[\s.]*o[\s.]*s[\s.]*(?:e|i)?(?:[_-]?es)?(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])vers[ií]on\s+original(?:\s+subtitulad[oa])?\b",
    re.IGNORECASE)
# Francés: VOSTFR/VOSTA/VF/FR y marcadores de idioma francés. Se excluye
# igual que VOS (ver is_french_content). Cubre "Tensei Shitara Slime Datta
# Ken 4xXX VOSTFR Web (by OtakuNashi).ts" y el FR suelto ("...-FR",
# "[FR]", "F.R.", "FR 1080p": caso real Doctor Who 3x12 que se coló
# porque solo estaban VOSTFR/VF/FRENCH), TRUEFRENCH (tag de escena),
# VFF/VQ (versiones francesa/quebequesa). Las guardas evitan que "fr"
# dentro de palabras normales ("Alfred", "Franklin"...) marque.
_FRA_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:vostfr|vosta|truefrench|vff|vf|fr|f\.r\.?|french|français|francais|vq)(?![A-Za-z0-9])",
    re.IGNORECASE)
# Italiano (ITA/Italiano/Italiana): ver is_italian_only() -- si el release es
# italiano y no trae NINGÚN rastro de español, se EXCLUYE (como el porno); si
# además lleva español (dual ENG-SPA, "Spanish subs"...), solo se penaliza.
# Usa look-behind/ahead para cazar SUB_ITA / AAC_ITA donde '_' rompe \b.
_ITA_RE = re.compile(r"(?<![A-Za-z0-9])(?:ita|italian|italiano|italiana)(?![A-Za-z0-9])", re.IGNORECASE)
# Marcadores de TÍTULO en italiano (palabras-función y lexemas inequívocos,
# ausentes del castellano/inglés) por si el nombre no lleva el token ITA pero
# el título está traducido al italiano (p.ej. "Un Caso Di Chiaroscuro" o
# "La Mossa Della Bella" 2x06, "Visitatori" 2x13, "La Riunione Che Danza" 2x16). Se exigen >=2
# para no falsear con palabras sueltas compartidas ("la", "un"...).
# NOTA: el grupo "kagome" ya NO es italiano fijo del motor: es una entrada
# normal de la lista de bloqueados (ver DEFAULTS p2p_blocked_groups), visible
# y editable como cualquier otra.
_ITA_TITLE_RE = re.compile(
    r"\b(?:il|lo|gli|le|di|del|della|dello|dei|delle|degli|nel|nella|nello|"
    r"nei|negli|sul|sulla|sullo|sui|sulle|dal|dalla|dai|dalle|dagli|"
    r"sempre|dopo|perche|perché|senza|dove|quando|tutto|tutta|tutti|tutte|"
    r"niente|nulla|troppo|ancora|adesso|davvero|amore|morte|notte|giorno|"
    r"storia|famiglia|fratelli|uomini|donne|ragazzi|bambini|signore|grazie|"
    r"ecco|avanti|basta|ciao|bella|bello|belle|belli|mossa|mosse|visitatori|visitatore|speranza|riconciliazione|preparativi|inviti|udienze|avventori|vigilia|apertura|decisione|folla|riunione|danza|che)\b", re.IGNORECASE)
# Alemán (GERMAN/Deutsch/tag GER suelto "...-GER", "[GER]"): ver
# is_german_content() -- mismo trato que el italiano (exclusión sin
# español, -60 con español). Guardas como _ITA_RE para no marcar
# palabras normales.
_GER_RE = re.compile(r"(?<![A-Za-z0-9])(?:german|deutsch|ger)(?![A-Za-z0-9])",
                     re.IGNORECASE)
# Portugués (Portuguese/Português/Brasileiro/DUBLADO/LEGENDADO): ver
# is_portuguese_content(). DUBLADO/LEGENDADO son los tags brasileños de
# "doblado/subtitulado (al portugués)". "Nacional" NO entra a propósito:
# falsea con títulos en español ("cine nacional"...).
_POR_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:portuguese|portugu[eê]s|brasileir[oa]|dublado|legendado)(?![A-Za-z0-9])",
    re.IGNORECASE)
# Palabras italianas muy distintivas para el título (una sola basta si viene con Kagome o sola en el título)
_ITA_TITLE_DISTINCTIVE_RE = re.compile(
    r"\b(?:della|delle|degli|dalla|dalle|degli|riunione|mossa|mosse|visitatori|visitatore|speranza|riconciliazione|preparativi|inviti|udienze|avventori|vigilia|apertura|decisione|folla|danza|che)\b",
    re.IGNORECASE)

def _episode_title_part(name: str) -> str:
    """Extrae el título del episodio (lo que va después de 2x16 / S02E16) hasta el siguiente tag técnico.
    Ej. 'Tensei ... 2x16 - La Riunione Che Danza 1080P By Kagome.mkv' → 'La Riunione Che Danza'."""
    if not name:
        return ""
    m = _EPISODE_SCAN.search(name)
    if not m:
        return ""
    after = name[m.end():]
    # quitar separadores iniciales
    after = re.sub(r'^[\s\-\._]+', '', after)
    # cortar antes de tags técnicos o grupo
    # 1080P, 720P, 480P, By, (, [, ., etc.
    cut = re.split(r'\b(?:1080p|720p|480p|576p|2160p|4k|by|\(|\[)\b', after, flags=re.IGNORECASE)
    title = cut[0] if cut else after
    # quitar extensión y separadores finales
    title = re.sub(r'\.[a-z0-9]{2,4}$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[\s\-\._]+$', '', title)
    return title.strip()

# "dual" = doble pista de audio real (típicamente SPA+ENG): el único
# marcador que mantiene elegible un release con etiqueta V.O.S. explícita
# (dual con castellano = emergencia). "Spanish", "castellano", "spa",
# "latino" o "subs" solos NO valen: en aMule describen muy a menudo solo
# los subtítulos (real 2026-09-18: "... (V.O.S. Spa-Eng) - Spanish subs
# integrados by JuAnItO" se descargó como si fuera castellano).
_DUAL_RE = re.compile(r"\bdual\b", re.IGNORECASE)
# Muestras/trailers NO son el capítulo completo: penalizan fuerte.
_SAMPLE_RE = re.compile(r"\b(?:sample|muestra|preview|trailer|demo)\b",
                        re.IGNORECASE)
# Capturas de cine / screeners / encodes de baja calidad.
_SCR_RE = re.compile(r"\b(?:cam|screener|dvdscr|telecine|telesync|hdtc)\b",
                     re.IGNORECASE)
# Versiones corregidas: premian (proper/repack corrigen un release malo).
_PROPER_RE = re.compile(r"\b(?:proper|repack|repackage)\b", re.IGNORECASE)

# Extensiones que NO son un vídeo (un ".emulecollection", un ".srt", un
# ".nfo"... no se pueden bajar como capítulo). Penalizan fuerte.
_NON_VIDEO_EXTS = {
    "srt", "sub", "idx", "txt", "nfo", "jpg", "jpeg", "png", "gif",
    "emulecollection", "cue", "md5", "sfv", "pdf", "epub", "log", "ini",
    "db", "torrent", "magnet",
}
# Indicadores técnicos genéricos (codecs/contenedor: NO son proveedores y no
# dan bonus de confianza). Los nombres de grupos/colectores NO van aquí: todo
# bonus de proveedor sale solo de la lista efectiva de confianza (ver
# set_provider_lists), que el usuario ve y edita entera en Ajustes →
# Servidor → Preferencias descargas.
_GRUPOS_RE = re.compile(r"\b(?:dts|x264|hevvc|x265)\b",
                        re.IGNORECASE)
# Grupos de P2P de confianza (bonus +25 en el scoring): la lista VIVA está
# en config.py::p2p_trusted_groups y se edita en Ajustes → Servidor →
# Preferencias descargas, apartado Proveedores (ver set_provider_lists).
# _BUILTIN_TRUSTED son solo los valores INICIALES (MISMA lista que
# config.py::DEFAULTS -- mantener sincronizadas) y el estado inicial del
# módulo, para que el scoring funcione igual aunque nadie haya llamado al
# setter (tests, watcher standalone). NO hay bonus escondido: la lista del
# box es AUTORITATIVA y aparece entera en la configuración del servidor --
# si el usuario quita "grupots", deja de puntuar.
_BUILTIN_TRUSTED = ["exploradoresp2p", "grupots", "hispashare",
                    "hispashare.org", "nocturniap2p"]

# Filtros de idioma del motor (ver _VOS_RE/_FRA_RE/_ITA_RE/_GER_RE/_POR_RE y
# sus is_*_content): siempre activos. Ya NO se mezclan con las listas de
# proveedores: se describen en su propio apartado "Idioma" en Ajustes →
# Servidor → Preferencias descargas (ver LANGUAGE_FILTERS_INFO). Si el motor
# gana un idioma, añadirlo ahí y aquí.
BUILTIN_BLOCKED_REFERENCE_LINES = (
    "# Fijos del sistema, siempre activos (no editables):",
    "# VOSTFR, TRUEFRENCH, FR, VFF, VQ, ITA/italiano, GERMAN/GER,",
    "# DUBLADO/portugués/brasileiro, VOS/VOSE/VOSI.",
)
# Texto para el apartado "Idioma" de Preferencias descargas: resume los
# filtros de idioma del motor sin mezclarlos con proveedores. Mantener
# sincronizado con las regex _VOS_RE/_FRA_RE/_ITA_RE/_GER_RE/_POR_RE/_CAT_RE.
LANGUAGE_FILTERS_INFO = (
    "VOS / VOSE / VOSI (versión original subtitulada, sin doblaje)",
    "Francés sin español: VOSTFR, TRUEFRENCH, FR, VFF, VQ...",
    "Italiano sin español: ITA / italiano (o título en italiano)",
    "Alemán sin español: GERMAN / GER / Deutsch",
    "Portugués/brasileño sin español: DUBLADO, LEGENDADO...",
    "Catalán sin español: penaliza fuerte (-60), no excluye",
    "Con pista en español o 'dual', estos filtros no excluyen",
)

_TRUSTED: list = [t.lower() for t in _BUILTIN_TRUSTED]
_TRUSTED_RES: list = []
_USER_BLOCKED: list = []
_USER_BLOCKED_RES: list = []


def _compile_guarded(entries: list) -> list:
    """Compila entradas de texto plano a regex con guardas alfanuméricas
    (como _ITA_RE): "fr" no casa dentro de "Alfred", "grupots" no casa
    en "xgrupots" ni "grupots2"."""
    out = []
    for e in entries or []:
        try:
            s = str(e or "").strip().lower()
            if not s:
                continue
            out.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])",
                                    re.IGNORECASE))
        except Exception:
            continue
    return out


_TRUSTED_RES = _compile_guarded(_TRUSTED)


def parse_provider_lines(text: str) -> list:
    """Texto de un box de proveedores (uno por línea) → lista limpia para
    config.json: ignora vacíos, duplicados (insensible a mayúsculas) y
    comentarios ("#" al inicio, por compatibilidad con boxes antiguos que
    mostraban los filtros de idioma como referencia). Conserva la grafía
    escrita para mostrarla. Pura, testeable."""
    out = []
    seen = set()
    try:
        for line in (text or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
    except Exception:
        pass
    return out


# Etiquetas de cada idioma para el campo único de Idioma (ver
# parse_lang_box/format_lang_box).
LANG_KIND_LABELS = {
    "vos": "VOS / VOSE / VOSI",
    "fr": "Francés",
    "it": "Italiano",
    "de": "Alemán",
    "pt": "Portugués",
    "ca": "Catalán (penaliza)",
}
_LANG_HEADER_RE = re.compile(r"\[\s*([A-Za-z]+)\s*\]")


def parse_lang_box(text: str) -> dict:
    """Campo único de Idioma → {kind: lista}. El campo agrupa los seis
    idiomas por secciones con cabecera `[kind]` (p.ej. `[fr]`), que puede
    llevar etiqueta detrás (`[fr] Francés`). Reglas: líneas vacías y `#`
    se ignoran; una cabecera desconocida cierra la sección actual (sus
    líneas se ignoran); las líneas antes de la primera cabecera se
    ignoran; duplicados insensibles a mayúsculas fuera. Pura, testeable."""
    out = {k: [] for k in LANG_KINDS}
    try:
        seen = {k: set() for k in LANG_KINDS}
        current = None
        for line in (text or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _LANG_HEADER_RE.match(s)
            if m:
                kind = m.group(1).lower()
                current = kind if kind in LANG_KINDS else None
                continue
            if current is None:
                continue
            if s.lower() in seen[current]:
                continue
            seen[current].add(s.lower())
            out[current].append(s)
    except Exception:
        pass
    return out


def format_lang_box(langs: dict) -> str:
    """{kind: lista} → texto del campo único de Idioma, con una sección
    `[kind] etiqueta` por idioma (ver LANG_KIND_LABELS). Pura, testeable."""
    try:
        get = langs.get if hasattr(langs, "get") else (lambda k, d=None: d)
    except Exception:
        get = lambda k, d=None: d  # noqa: E731
    lines = []
    for kind in LANG_KINDS:
        lines.append(f"[{kind}] {LANG_KIND_LABELS.get(kind, kind)}")
        try:
            entries = get(kind, []) or []
        except Exception:
            entries = []
        for it in entries:
            try:
                s = str(it or "").strip()
            except Exception:
                continue
            if s:
                lines.append(s)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def set_provider_lists(trusted=None, blocked=None) -> None:
    """Fija las listas EFECTIVAS del scoring (llamado al arrancar la app y
    al guardar Ajustes / aplicar sync de servidor).

    - *trusted*: lista COMPLETA de proveedores de confianza (reemplaza a los
      valores iniciales, no se suma). None = no tocar.
    - *blocked*: proveedores/marcas bloqueadas (los filtros de idioma del
      motor siguen aparte, en su propio apartado, siempre activos).
      None = no tocar.
    Nunca lanza excepción (una lista rota no debe romper el scoring)."""
    global _TRUSTED, _TRUSTED_RES, _USER_BLOCKED, _USER_BLOCKED_RES
    try:
        if trusted is not None:
            if isinstance(trusted, str):
                trusted = [trusted]
            seen, clean = set(), []
            for it in trusted:
                try:
                    s = str(it or "").strip().lower()
                except Exception:
                    continue
                if s and s not in seen:
                    seen.add(s)
                    clean.append(s)
            _TRUSTED = clean
            _TRUSTED_RES = _compile_guarded(clean)
        if blocked is not None:
            if isinstance(blocked, str):
                blocked = [blocked]
            seen, clean = set(), []
            for it in blocked:
                try:
                    s = str(it or "").strip().lower()
                except Exception:
                    continue
                if s and s not in seen:
                    seen.add(s)
                    clean.append(s)
            _USER_BLOCKED = clean
            _USER_BLOCKED_RES = _compile_guarded(clean)
    except Exception:
        pass


def set_user_provider_lists(trusted=None, blocked=None) -> None:
    """Alias antiguo de set_provider_lists (misma firma)."""
    return set_provider_lists(trusted, blocked)


def trusted_provider_match(name: str):
    """Entrada de la lista efectiva de confianza que aparece en *name*
    (con guardas), o None. Para la etiqueta del explain."""
    try:
        n = name or ""
        for rx in _TRUSTED_RES:
            m = rx.search(n)
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def is_trusted_provider(name: str) -> bool:
    """True si el nombre contiene algún proveedor de la lista efectiva de
    confianza (la que se ve y edita entera en Ajustes)."""
    return trusted_provider_match(name) is not None


def is_user_trusted(name: str) -> bool:
    """Alias de is_trusted_provider (compatibilidad)."""
    return is_trusted_provider(name)


def is_user_blocked(name: str) -> bool:
    """True si el nombre contiene alguna marca extra bloqueada por el
    usuario (ver set_provider_lists), SIN rastro de español: se excluye
    igual que el francés (ver is_french_content). Con español además,
    se permite (el audio es lo que importa)."""
    try:
        if not name or _LANG_RE.search(name):
            return False
        return any(rx.search(name) for rx in _USER_BLOCKED_RES)
    except Exception:
        return False


# ── Listas editables de idioma y otros filtros ──────────────────────────
# Cada clave tiene su lista en config (ver config.py::DEFAULTS y
# core/server_config.py) y se edita en Ajustes → Servidor → Preferencias
# descargas, apartados Idioma / Otros filtros. Los valores aquí son los
# INICIALES (misma grafía que las regex de arriba, en texto plano) y el
# estado inicial del módulo. HÍBRIDO: el motor inteligente (regex con
# guardas, títulos italianos, V.O.S. espaciado...) sigue fijo y estas
# listas AÑADEN marcadores con el mismo matching con guardas que los
# proveedores (ver _compile_guarded).
LANG_KINDS = ("vos", "fr", "it", "de", "pt", "ca")
LANG_CONFIG_KEYS = {
    "vos": "p2p_lang_vos", "fr": "p2p_lang_fr", "it": "p2p_lang_it",
    "de": "p2p_lang_de", "pt": "p2p_lang_pt", "ca": "p2p_lang_ca",
}
BUILTIN_LANG_MARKERS = {
    "vos": ["vos", "vose", "vosi", "versión original", "version original"],
    "fr": ["vostfr", "vosta", "truefrench", "vff", "vf", "fr", "f.r.",
           "french", "francais", "français", "vq"],
    "it": ["ita", "italian", "italiano", "italiana"],
    "de": ["german", "deutsch", "ger"],
    "pt": ["portuguese", "portugués", "portugues", "brasileiro",
           "brasileira", "dublado", "legendado"],
    # OJO: "cat" suelto NO vale (falsea con "categoría", "Catwoman"...):
    # el "[Cat]"/"Cat.Subs" lo sigue cazando el motor (_CAT_RE).
    "ca": ["catalán", "catalan", "catalá", "catala", "català", "vosc"],
}
BUILTIN_ADULT = ["xxx", "porn", "porno", "hardcore", "milf", "hentai",
                 "onlyfans", "bondage", "jav", "creampie", "gangbang",
                 "bigboobs", "bigtits"]
BUILTIN_SAMPLE = ["sample", "muestra", "preview", "trailer", "demo"]
BUILTIN_SCR = ["cam", "screener", "dvdscr", "telecine", "telesync", "hdtc"]
BUILTIN_NONVIDEO_EXTS = sorted(_NON_VIDEO_EXTS)

_USER_LANG: dict = {k: [] for k in LANG_KINDS}
_USER_LANG_RES: dict = {k: [] for k in LANG_KINDS}
_USER_ADULT: list = []
_USER_ADULT_RES: list = []
_USER_SAMPLE: list = []
_USER_SAMPLE_RES: list = []
_USER_SCR: list = []
_USER_SCR_RES: list = []
_USER_NONVIDEO: set = set()


def _clean_word_list(entries) -> list:
    """Limpia una lista de texto plano (minúsculas, sin vacíos ni
    duplicados). Nunca lanza excepción."""
    if isinstance(entries, str):
        entries = [entries]
    seen, clean = set(), []
    try:
        for it in entries or []:
            try:
                s = str(it or "").strip().lower()
            except Exception:
                continue
            if s and s not in seen:
                seen.add(s)
                clean.append(s)
    except Exception:
        pass
    return clean


def set_filter_lists(langs=None, adult=None, sample=None, scr=None,
                     exts=None) -> None:
    """Fija las listas EFECTIVAS de idioma y otros filtros (llamado al
    arrancar la app y al guardar Ajustes / aplicar sync de servidor).

    - *langs*: dict {kind: lista} con kinds de LANG_KINDS (solo añaden
      marcadores al motor de ese idioma). None = no tocar; un kind
      ausente tampoco se toca.
    - *adult* / *sample* / *scr*: marcadores extra (se suman a los del
      motor). None = no tocar.
    - *exts*: extensiones no-vídeo extra (se suman a las del motor,
      sin punto, en minúsculas). None = no tocar.
    Nunca lanza excepción."""
    global _USER_LANG, _USER_LANG_RES, _USER_ADULT, _USER_ADULT_RES
    global _USER_SAMPLE, _USER_SAMPLE_RES, _USER_SCR, _USER_SCR_RES
    global _USER_NONVIDEO
    try:
        if langs is not None:
            try:
                items = langs.items() if hasattr(langs, "items") else []
            except Exception:
                items = []
            for kind, entries in items:
                if kind not in LANG_KINDS:
                    continue
                clean = _clean_word_list(entries)
                _USER_LANG[kind] = clean
                _USER_LANG_RES[kind] = _compile_guarded(clean)
        if adult is not None:
            _USER_ADULT = _clean_word_list(adult)
            _USER_ADULT_RES = _compile_guarded(_USER_ADULT)
        if sample is not None:
            _USER_SAMPLE = _clean_word_list(sample)
            _USER_SAMPLE_RES = _compile_guarded(_USER_SAMPLE)
        if scr is not None:
            _USER_SCR = _clean_word_list(scr)
            _USER_SCR_RES = _compile_guarded(_USER_SCR)
        if exts is not None:
            if isinstance(exts, str):
                exts = [exts]
            clean_exts = set()
            try:
                for it in exts or []:
                    try:
                        s = str(it or "").strip().lower().lstrip(".")
                    except Exception:
                        continue
                    if s:
                        clean_exts.add(s)
            except Exception:
                pass
            _USER_NONVIDEO = clean_exts
    except Exception:
        pass


def _lang_hit(kind: str, name: str):
    """Marcador de la lista editable de idioma *kind* que aparece en
    *name* (con guardas), o None."""
    try:
        for rx in _USER_LANG_RES.get(kind) or []:
            m = rx.search(name or "")
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def adult_match(name: str):
    """Marcador adulto de la lista editable en *name*, o None."""
    try:
        for rx in _USER_ADULT_RES:
            m = rx.search(name or "")
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def sample_match(name: str):
    """Marcador de muestra/trailer de la lista editable en *name*, o None."""
    try:
        for rx in _USER_SAMPLE_RES:
            m = rx.search(name or "")
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def scr_match(name: str):
    """Marcador de cam/screener de la lista editable en *name*, o None."""
    try:
        for rx in _USER_SCR_RES:
            m = rx.search(name or "")
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def is_non_video_ext(ext: str) -> bool:
    """True si *ext* (sin punto, minúsculas) es no-vídeo: motor o lista
    editable del usuario."""
    try:
        e = str(ext or "").strip().lower().lstrip(".")
        return e in _NON_VIDEO_EXTS or e in _USER_NONVIDEO
    except Exception:
        return False


def has_other_language(name: str) -> bool:
    """True si el nombre trae otro idioma (catalán, italiano, alemán o
    portugués, motor o listas editables). Sin español se excluye antes
    (ver is_*_only); con español además, resta el peso no_spanish una
    sola vez (ver score_download)."""
    if not name:
        return False
    try:
        return bool(_CAT_RE.search(name) or _lang_hit("ca", name)
                    or _ITA_RE.search(name) or _lang_hit("it", name)
                    or _GER_RE.search(name) or _lang_hit("de", name)
                    or _POR_RE.search(name) or _lang_hit("pt", name))
    except Exception:
        return False


# ── Pesos personalizables de la puntuación ──────────────────────────────
# Valores de fábrica = comportamiento actual. La config solo guarda los que
# el usuario cambie (p2p_score_weights, ver config.py); el resto vale esto.
# Claves desconocidas o no numéricas al aplicar se ignoran (nunca rompen).
SCORE_WEIGHT_DEFAULTS = {
    "episode_match": 50.0, "episode_mismatch": -40.0,
    "year_match": 15.0, "year_mismatch": -40.0,
    "title_overlap_2": 12.0, "title_overlap_1": 5.0, "title_overlap_0": -40.0,
    "spanish": 18.0, "non_spanish": -60.0,
    "res_4k": 30.0, "res_1080": 25.0, "res_720": 20.0, "res_sd": 8.0,
    "ext_mkv": 8.0, "ext_mp4": 6.0, "ext_avi": 4.0, "ext_mov": 3.0,
    "ext_divx": 2.0, "ext_nonvideo": -25.0,
    "tech_generic": 3.0, "trusted": 25.0,
    "source_hint": 2.0, "proper": 4.0,
    "sample": -15.0, "scr": -10.0, "complete": 4.0,
    "sources_base": 1.5, "sources_mid": 0.7, "sources_high": 0.3,
    "sources_bonus_100": 5.0, "sources_bonus_150": 5.0,
    "size_ok": 2.0, "size_bad": -30.0,
    "size_in_range": 3.0, "size_out_range": -15.0,
    "min_best_score": 15.0,
}
# Etiquetas cortas para la GUI (sección Puntuación): (grupo, etiqueta).
SCORE_WEIGHT_LABELS = {
    "episode_match": ("Episodio", "Coincide"),
    "episode_mismatch": ("Episodio", "Distinto"),
    "year_match": ("Año peli", "Coincide"),
    "year_mismatch": ("Año peli", "Distinto"),
    "title_overlap_2": ("Título serie", "2+ palabras"),
    "title_overlap_1": ("Título serie", "1 palabra"),
    "title_overlap_0": ("Título serie", "Otra serie"),
    "spanish": ("Idioma", "Español"),
    "non_spanish": ("Idioma", "No español"),
    "res_4k": ("Calidad", "4K/2160p"),
    "res_1080": ("Calidad", "1080p"),
    "res_720": ("Calidad", "720p"),
    "res_sd": ("Calidad", "SD"),
    "ext_mkv": ("Contenedor", ".mkv"),
    "ext_mp4": ("Contenedor", ".mp4"),
    "ext_avi": ("Contenedor", ".avi"),
    "ext_mov": ("Contenedor", ".mov"),
    "ext_divx": ("Contenedor", ".divx"),
    "ext_nonvideo": ("Contenedor", "Ext. no-vídeo"),
    "tech_generic": ("Señales", "Indicador técnico"),
    "trusted": ("Señales", "Proveedor confianza"),
    "source_hint": ("Señales", "Pista fuente"),
    "proper": ("Señales", "Proper/repack"),
    "sample": ("Penalizaciones", "Muestra/trailer"),
    "scr": ("Penalizaciones", "Cam/screener"),
    "complete": ("Fuentes", "Completo"),
    "sources_base": ("Fuentes", "Coef. base (×10)"),
    "sources_mid": ("Fuentes", "Coef. medio (+20)"),
    "sources_high": ("Fuentes", "Coef. alto (+70)"),
    "sources_bonus_100": ("Fuentes", "Bonus >100"),
    "sources_bonus_150": ("Fuentes", "Bonus >150"),
    "size_ok": ("Tamaño", "Proporcionado"),
    "size_bad": ("Tamaño", "Desviado"),
    "size_in_range": ("Tamaño", "En rango"),
    "size_out_range": ("Tamaño", "Fuera de rango"),
    "min_best_score": ("Umbral", "Mínimo destacar"),
}
# Explicación de cada peso para los tooltips de la GUI (sección
# Puntuación). Una o dos líneas: qué significa que un release lo gane.
SCORE_WEIGHT_TIPS = {
    "episode_match": "El resultado es el capítulo pedido: misma temporada y episodio, y misma serie.",
    "episode_mismatch": "El resultado es de otra temporada o episodio: resta para que no gane por calidad.",
    "year_match": "La película declara el año pedido en su nombre.",
    "year_mismatch": "La película declara OTRO año (remake o relanzamiento): no debe ganar.",
    "title_overlap_2": "El título de la serie coincide en 2 o más palabras con lo buscado.",
    "title_overlap_1": "Coincide una sola palabra del título de la serie.",
    "title_overlap_0": "Parece de OTRA serie (título sin relación): resta fuerte.",
    "spanish": "Marca de español: spa, spanish, español, castellano, latino o dual.",
    "non_spanish": "El release trae otro idioma (catalán, italiano, alemán, portugués). Solo extranjero se excluye del todo; con español además, resta esto.",
    "res_4k": "Resolución 4K/2160p. Solo puntúa la primera resolución que coincida.",
    "res_1080": "Resolución 1080p. Solo puntúa la primera resolución que coincida.",
    "res_720": "Resolución 720p. Solo puntúa la primera resolución que coincida.",
    "res_sd": "Resolución SD/576p/480p/DivX. Solo puntúa la primera que coincida.",
    "ext_mkv": "Contenedor MKV, el preferido.",
    "ext_mp4": "Contenedor MP4.",
    "ext_avi": "Contenedor AVI.",
    "ext_mov": "Contenedor MOV.",
    "ext_divx": "Contenedor DivX.",
    "ext_nonvideo": "No es un vídeo descargable (srt, nfo, torrent...): resta.",
    "tech_generic": "Indicios técnicos genéricos (x264, x265, dts...).",
    "trusted": "Lleva un proveedor de la lista de confianza.",
    "source_hint": "Cada pista de origen (web-dl, hdtv, bluray, dvdrip...) suma esto.",
    "sample": "Muestra, trailer o demo: no es el capítulo completo.",
    "scr": "Captura de cine o screener: mala calidad.",
    "proper": "Versión corregida (proper/repack) de un release anterior.",
    "complete": "El archivo está completo en la red.",
    "sources_base": "Puntos por cada una de las 10 primeras fuentes.",
    "sources_mid": "Puntos por cada fuente de la 11 a la 30.",
    "sources_high": "Puntos por cada fuente de la 31 a la 100.",
    "sources_bonus_100": "Extra si supera las 100 fuentes.",
    "sources_bonus_150": "Extra si supera las 150 fuentes.",
    "size_ok": "Pesa como los demás capítulos de ESTA serie en tu servidor (0.7×–1.4× del tamaño típico). Solo vale cuando la app ya conoce la serie.",
    "size_bad": "Pesa distinto a los demás capítulos de ESTA serie en tu servidor: resta en proporción al desvío (|size/típico − 1| × este peso). Solo vale cuando la app ya conoce la serie; si no, manda el rango por resolución.",
    "size_in_range": "Pesa lo normal para su resolución (p. ej. un capítulo 1080p entre 400 MB y 2 GB). Solo vale cuando la app aún NO conoce la serie.",
    "size_out_range": "Pesa algo imposible para su resolución (diminuto o pasado). Solo vale cuando la app aún NO conoce la serie.",
    "min_best_score": "Por debajo de esta nota no se destaca ni descarga NADA.",
}
# Explicación de cada grupo de pesos (cabeceras de la GUI).
SCORE_GROUP_TIPS = {
    "Episodio": "Tiene que ser EL capítulo pedido, no otro con mejor calidad.",
    "Año peli": "La película tiene que ser del año pedido, no un remake.",
    "Título serie": "El resultado tiene que ser de la MISMA serie buscada.",
    "Idioma": "El español manda; otros idiomas sin español se excluyen o restan.",
    "Calidad": "Resolución de imagen: solo puntúa la primera que coincida.",
    "Contenedor": "Formato del archivo. Lo no-vídeo no se puede descargar.",
    "Señales": "Pistas de fiabilidad: proveedor, origen y versiones corregidas.",
    "Penalizaciones": "Resta cuando no es el capítulo completo o es mala calidad.",
    "Fuentes": "Más fuentes y completo = más fiable.",
    "Tamaño": "Si la app conoce la serie, compara con sus capítulos (Proporcionado/Desviado); si no, con la resolución (En rango/Fuera de rango).",
    "Umbral": "Nota mínima para recomendar y descargar algo.",
}

_WEIGHTS: dict = dict(SCORE_WEIGHT_DEFAULTS)


def set_score_weights(overrides=None) -> None:
    """Fija los pesos EFECTIVOS del scoring: fábrica + *overrides* (solo
    se guardan los cambiados, ver config p2p_score_weights). None o {}
    = volver a fábrica. Claves desconocidas o valores no numéricos se
    ignoran. Nunca lanza excepción."""
    global _WEIGHTS
    try:
        eff = dict(SCORE_WEIGHT_DEFAULTS)
        if overrides:
            try:
                items = overrides.items() if hasattr(overrides, "items") else []
            except Exception:
                items = []
            import math
            for k, v in items:
                if k not in SCORE_WEIGHT_DEFAULTS:
                    continue
                try:
                    if isinstance(v, bool):
                        continue
                    f = float(v)
                    if not math.isfinite(f):
                        continue
                except (TypeError, ValueError):
                    continue
                eff[k] = f
        _WEIGHTS = eff
    except Exception:
        pass


def _W(key: str) -> float:
    """Peso efectivo de *key* (fábrica si algo falla)."""
    try:
        return float(_WEIGHTS.get(key, SCORE_WEIGHT_DEFAULTS[key]))
    except Exception:
        try:
            return float(SCORE_WEIGHT_DEFAULTS[key])
        except Exception:
            return 0.0

_RES_WEIGHTS = [
    # (regex, clave de peso). El PRIMERO que coincita gana (ver _W).
    (re.compile(r"\b4k\b|\b2160p\b", re.IGNORECASE), "res_4k"),
    (re.compile(r"\b1080p\b|\b1080\b", re.IGNORECASE), "res_1080"),
    (re.compile(r"\b720p\b|\b720\b", re.IGNORECASE), "res_720"),
    (re.compile(r"\b576p\b|\b480p\b|\bsd\b|\bdivx\b", re.IGNORECASE), "res_sd"),
]

# Contenedores mas frecuentes en orden de preferencia (clave de peso por ext).
_EXT_WEIGHT_KEYS = {"mkv": "ext_mkv", "mp4": "ext_mp4", "avi": "ext_avi",
                    "mov": "ext_mov", "divx": "ext_divx"}

# Subcadenas que delatan fuente WEB / retransmisión / encode (dan puntos).
_SOURCE_HINTS = {"web", "web-dl", "webdl", "hdtv", "dvdrip", "bluray", "bdrip", "h264", "hevc", "x264", "x265"}

# Extrae "temporada, episodio" de un nombre de archivo o de una consulta de
# búsqueda, con el mismo esqueleto que EPISODE_PATTERNS de core/api_client.py
# pero en miniatura (solo lo que hace falta para comparar resultado vs
# búsqueda). Cubre "S01E02", "1x02" y variantes. Devuelve (season, episode) o
# None si no reconoce numeración de episodio.
_EPISODE_SCAN = re.compile(
    r"(?:[Ss](\d{1,2})[Ee](\d{1,3})|(\d{1,2})[xX](\d{2,3}))")


def _parse_season_episode(name: str):
    m = _EPISODE_SCAN.search(name or "")
    if not m:
        return None
    season = m.group(1) or m.group(3)
    episode = m.group(2) or m.group(4)
    if season is None or episode is None:
        return None
    return int(season), int(episode)


# Años de película (4 dígitos, 1800-2099) para comprobar que el release que
# se elige es del año correcto (ver _years_in_name / score_download). No
# entra "1080"/"2160" (empiezan por 10/21... y en "1080p"/"2160p" no hay
# límite de palabra tras el número).
_MOVIE_YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}\b")


def _years_in_name(name: str) -> set:
    """Años (1800-2099) que aparecen en un nombre de resultado de aMule, para
    comprobar que el release declara el año pedido (ver score_download). Un
    número de 4 cifras plausible cuenta aunque venga en el título ("Blade
    Runner 2049") -- al comprobar basta con que UNO de los años coincida con
    el esperado."""
    return {int(m) for m in _MOVIE_YEAR_RE.findall(name or "")}


def is_adult_content(name: str) -> bool:
    """True si el nombre delata contenido adulto/porno (motor _PORN_RE más
    la lista editable p2p_blocked_adult, ver set_filter_lists).
    Se usan solo marcadores inequívocos para no descartar títulos
    legítimos como "Sex Education" o "Adult Swim"."""
    if not name:
        return False
    return bool(_PORN_RE.search(name) or adult_match(name))


def is_italian_only(name: str) -> bool:
    """True si el resultado es italiano SIN rastro de español: se excluye
    SIEMPRE de best_result (ni siquiera cuando no hay ningún release en
    español). Motor (_ITA_RE, títulos en italiano) más la lista editable
    p2p_lang_it. Los releases que además traen español (dual "ENG-SPA",
    "Spanish subs"...): esos solo se penalizan y siguen siendo un
    candidato válido de emergencia."""
    if not name:
        return False
    # Hay rastro de español (audio o subs): no es "solo italiano".
    if _LANG_RE.search(name):
        return False
    # Marcador explícito ITA/Italiano (caza SUB_ITA, AAC_ITA con (?<![A-Za-z0-9]))
    # o de la lista editable del usuario.
    if _ITA_RE.search(name) or _lang_hit("it", name):
        return True
    # Título del episodio en italiano (ej. "Tensei 2x16 - La Riunione Che Danza 1080P")
    # Con solo nombre+numero+título, 1 palabra distintiva en el título ya indica italiano
    try:
        tp = _episode_title_part(name)
        if tp and len(_ITA_TITLE_DISTINCTIVE_RE.findall(tp)) >= 1:
            return True
    except Exception:
        pass
    # ...o título traducido al italiano (>=2 palabras-función inequívocas):
    # cubre nombres que no llevan el token ITA pero sí el título en italiano.
    return len(_ITA_TITLE_RE.findall(name)) >= 2


def is_vos_content(name: str) -> bool:
    """True si el nombre lleva etiqueta V.O.S. explícita (VOS/VOSE/VOSI,
    "versión original subtitulada") sin doblaje real: se excluye siempre
    (ver score_download/best_result), igual que el porno. Cubre
    VOS/VOSE/VOSI y 'versión original subtitulada'.

    "Spanish"/"castellano"/"spa"/"subs" NO cancelan el VOS: en aMule
    describen muy a menudo solo los subtítulos (real: "... (V.O.S.
    Spa-Eng) - Spanish subs integrados by JuAnItO" se descargó como
    "castellano"). Solo "dual" (doble audio real) lo mantiene como
    emergencia, igual que is_italian_only / is_french_content."""
    if not name:
        return False
    if not (_VOS_RE.search(name) or _lang_hit("vos", name)):
        return False
    if _DUAL_RE.search(name):
        return False
    return True


def is_french_content(name: str) -> bool:
    """True si el nombre delata contenido en francés / VOSTFR sin español:
    se excluye. Motor (_FRA_RE: VOSTFR/VOSTA/VF/French...) más la lista
    editable p2p_lang_fr. Caso real: 'Tensei Shitara Slime Datta Ken 4xXX
    VOSTFR Web (by OtakuNashi).ts'. Un dual 'Spanish French Subs' no se
    excluye (tiene castellano)."""
    if not name or _LANG_RE.search(name):
        return False
    return bool(_FRA_RE.search(name) or _lang_hit("fr", name))


# Aliases para compatibilidad con best_result (is_*_only)
def is_vos_only(name: str) -> bool:
    return is_vos_content(name)


def is_french_only(name: str) -> bool:
    return is_french_content(name)


def is_german_content(name: str) -> bool:
    """True si el nombre delata contenido en alemán sin español: se
    excluye, igual que el italiano solo (ver is_italian_only). Motor
    (_GER_RE) más la lista editable p2p_lang_de. Un dual con castellano
    no se excluye (tiene español), solo se penaliza."""
    if not name or _LANG_RE.search(name):
        return False
    return bool(_GER_RE.search(name) or _lang_hit("de", name))


def is_german_only(name: str) -> bool:
    return is_german_content(name)


def is_portuguese_content(name: str) -> bool:
    """True si el nombre delata contenido en portugués/brasileño sin
    español: se excluye, igual que el italiano solo. Motor (_POR_RE)
    más la lista editable p2p_lang_pt. Un dual con castellano no se
    excluye, solo se penaliza."""
    if not name or _LANG_RE.search(name):
        return False
    return bool(_POR_RE.search(name) or _lang_hit("pt", name))


def is_portuguese_only(name: str) -> bool:
    return is_portuguese_content(name)


def _title_words(text: str) -> set:
    """Palabras significativas (>=3 chars, sin números) de un título o
    consulta, para comparar que la SERIE coincide (no solo la numeración)."""
    words = set(re.findall(r"[a-zA-Z\u00C0-\u024F]{3,}", (text or "").lower()))
    return words


def _title_overlap(query: str, name: str) -> int:
    """Palabras del título compartidas entre la consulta y el nombre. Cuenta
    solo el solapamiento real de la serie: si la consulta es "Los Simpsons
    2x04" y el nombre es "Los Simpsons 2x04 720p", devuelve 2 (los, simpsons).
    Palabras genéricas del episodio (episode, capitulo, x264...) no cuentan
    porque no están en la consulta."""
    q = _title_words(query)
    if not q:
        return 0
    n = _title_words(name)
    if not n:
        return 0
    return len(q & n)


def _series_title_before_episode(name: str) -> str:
    """El título de la serie dentro de un nombre de archivo o consulta: todo
    lo que va ANTES del primer marcador de episodio (SxxExx/NxNN). Los
    nombres de aMule ponen la serie delante y el capítulo/título/basura
    técnica detrás ("Lucky Luke 1x01 El solitario..."), así que recortando
    ahí se aísla la serie para compararla con la de la consulta. Si no hay
    numeración, se devuelve el nombre completo."""
    m = _EPISODE_SCAN.search(name or "")
    if not m:
        return name or ""
    return (name or "")[:m.start()]


def _same_series_title(query: str, name: str) -> bool:
    """True si el título de la serie del resultado coincide con el de la
    consulta. Se compara solo la parte de SERIE (lo anterior a la numeración,
    ver _series_title_before_episode) con series_similarity en modo estricto
    (strict + allow_annotation, el mismo que usa la elección de carpeta de
    destino) y un umbral alto: "Lucky" NO es "Lucky Luke" ni "Star Wars" es
    "Star Wars Las aventuras de los jóvenes Jedi", aunque compartan las
    primeras palabras -- pero "Desencanto (Disenchantment)" sí es
    "Desencanto" y "Ranma ½" sí es "Ranma (1989)". Con numeración de episodio
    en la consulta basta con que la parte de la serie casen: el episodio se
    comprueba aparte. Se quitan los tags de grupo en corchetes ("[BRrip]
    Resident Alien" → "Resident Alien") que los releases reales anteponen al
    título, para no romper la coincidencia."""
    q = _series_title_before_episode(query).strip()
    n = _series_title_before_episode(name).strip()
    if not q or not n:
        # No se puede comparar (consulta sin serie, o serie detrás de la
        # numeración): se admite por defecto, que la numeración decida.
        return True
    q = re.sub(r"\[[^\]]*\]", " ", q)
    n = re.sub(r"\[[^\]]*\]", " ", n)
    # Alias corto del usuario (ej. "Slime" para "That Time I Got Reincarnated as a Slime"):
    # series_similarity estricto 0.90 falla porque "Slime" solo es sufijo, no prefijo.
    # Si la versión normalizada corta está contenida en la larga, aceptar.
    qn = normalize_series_name(q)
    nn = normalize_series_name(n)
    if len(qn) >= 3 and len(nn) >= 3 and (qn in nn or nn in qn):
        return True
    return series_similarity(q, n, strict=True, allow_annotation=True) >= 0.90


def _ext(name: str) -> str:
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    return ext


def _size_bytes(result) -> float:
    """Bytes apax. del AmuleSearchResult (size_human tipo "450,5 MB")."""
    s = (result.size_human or "").strip()
    parts = s.split()
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


def _size_range_for_resolution(name: str, is_movie: bool = False) -> tuple[float, float]:
    """Rango de bytes razonable para el contenido según la resolución de la
    codificación: (min_bytes, max_bytes). Distingue CAPÍTULOS de serie (el
    nombre trae numeración SxxExx/NxNN y no es una película) de películas:
    un episodio de 5 GB en 1080p es desproporcionado y debe penalizarse,
    mientras que una película del mismo tamaño es normal. El rango se queda
    laxo de todas formas para no penalizar encodes de alta calidad."""
    is_episode = not is_movie and _parse_season_episode(name) is not None
    if re.search(r"\b4k\b|\b2160\b", name, re.IGNORECASE):
        # 4K: capítulos grandes pero no absurdos
        return (800 * 1024**2, 3 * 1024**3) if is_episode else (800 * 1024**2, 15 * 1024**3)
    if re.search(r"\b1080p\b|1080\b", name, re.IGNORECASE):
        # 1080p: un capítulo normal ronda 400 MB-2 GB; 5 GB es un remux
        # desproporcionado para un episodio (caso real reportado).
        return (400 * 1024**2, 2 * 1024**3) if is_episode else (500 * 1024**2, 6 * 1024**3)
    if re.search(r"\b720p\b|720\b", name, re.IGNORECASE):
        return (200 * 1024**2, 1024**3) if is_episode else (200 * 1024**2, 3 * 1024**3)
    return (80 * 1024**2, 700 * 1024**2) if is_episode else (80 * 1024**2, 2 * 1024**3)


def resolution_label(name: str) -> str:
    """Etiqueta de resolución de *name* con las mismas regex que
    _size_range_for_resolution (mismo orden: 4K, 1080, 720, resto=SD).
    Sirve para agrupar películas por resolución en la vista plana de
    Liberar espacio (ver core/slim_candidates.find_oversize_flat): una
    mediana mezclando 4K y 720p no significa nada."""
    n = name or ""
    if re.search(r"\b4k\b|\b2160\b", n, re.IGNORECASE):
        return "4K"
    if re.search(r"\b1080p\b|1080\b", n, re.IGNORECASE):
        return "1080p"
    if re.search(r"\b720p\b|720\b", n, re.IGNORECASE):
        return "720p"
    return "SD"


def _size_typical_penalty(ratio: float, w_bad: float) -> float:
    """Resta proporcional al desvío del peso ideal.

    *ratio* = size / typical_size (>=0). Devuelve ``w_bad * |ratio - 1|``:
    un 10% de desvío resta el 10% de ``w_bad``, el doble del ideal
    (ratio 2.0) resta ``w_bad`` entero, y más allá sigue creciendo en
    proporción. ``w_bad`` es negativo de fábrica (-30), así que cuanto
    más lejos del ideal, más puntos resta. Por debajo del ideal el
    máximo es ``w_bad`` (size → 0, desvío del 100%); por encima no hay
    tope porque el exceso puede ser arbitrario (17 GB frente a 1 GB).
    Solo se llama fuera de la banda proporcionada (0.7×–1.4×); dentro
    suma ``size_ok`` y esto no se usa. Nunca lanza excepción."""
    try:
        dev = abs(float(ratio) - 1.0)
        return float(w_bad) * dev
    except Exception:
        try:
            return float(w_bad)
        except Exception:
            return 0.0


def _median_typical_size(sizes: list[int]) -> int | None:
    if not sizes:
        return None
    s = sorted(sizes)
    n = len(s)
    return int(s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) // 2)


def _typical_from_sizes(sizes: list[int]) -> int | None:
    """Tamaño típico de una lista de tamaños en bytes (misma serie).

    Agrupa en buckets de 200MB: si un bucket tiene mayoría estricta (>50%),
    ese es el típico (mediana del bucket). Sin mayoría (reparto disperso,
    p.ej. Stuart T01 con {2:2, 3:1, 5:2, 6:1, 9:1}), la mediana global es
    más fiel que el bucket más pequeño: con la regla anterior del mínimo,
    452+531MB ganaban a 5 ficheros de ~1GB y el típico salía 470MB.
    """
    if not sizes:
        return None
    buckets = Counter(int(sz // (200 * 1024 * 1024)) for sz in sizes)
    total = len(sizes)
    top_bucket, top_count = max(buckets.items(), key=lambda kv: (kv[1], -kv[0]))
    if top_count > total / 2:
        bucket_sizes = [sz for sz in sizes if int(sz // (200 * 1024 * 1024)) == top_bucket]
        return _median_typical_size(bucket_sizes)
    return _median_typical_size(sizes)


def score_download(result: AmuleSearchResult,
                   query: str = "",
                   expected_year: int = None,
                   is_movie: bool = False,
                   typical_size: int | None = None) -> float:
    """Da una puntuación (mejor = más grande) para un resultado.

    *query* es la consulta con la que se buscó (p.ej. "Los Simpsons 2x04").
    Si trae una numeración temporada×episodio reconocible, se exige que el
    resultado coincida con ella: un capítulo de OTRA temporada o de otra
    emisión, por muy buena resolución que tenga, NO debe quedar elegido por
    delante del capítulo pedido (real: buscando "2x04" el mejor candidato
    salía "Los Simpsons 22x04" porque solo se puntuaba por calidad). El
    "aura" de coincidencia es tan grande que domina el resto del score, que
    sigue decidiendo entre varios resultados de la MISMA temporada/emisión.

    *expected_year* es el año de una película (el botón ⬇ de Películas busca
    solo por título, sin el año entre paréntesis que aMule rechaza, y lo
    pasa aparte). Si se da y el nombre del resultado declara un año, se
    exige que coincida (ver _years_in_name); si el nombre no trae ningún
    año, no se puede comprobar y no se puntúa.
    """
    if result is None:
        return 0.0
    name = result.name or ""
    # Porno nunca puntúa: no pasa el umbral y jamás se destaca/descarga.
    if is_adult_content(name):
        return 0.0
    # Italiano sin español tampoco (ver is_italian_only).
    if is_italian_only(name):
        return 0.0
    # Alemán y portugués/brasileño sin español: mismo trato (ver
    # is_german_content/is_portuguese_content).
    if is_german_only(name) or is_portuguese_only(name):
        return 0.0
    # Proveedor/marca bloqueada en Ajustes → Preferencias descargas
    # (ver set_provider_lists/is_user_blocked).
    if is_user_blocked(name):
        return 0.0
    # V.O.S. y francés (VOSTFR) excluidos del todo por petición del usuario:
    # nunca se destacan/descargan, aunque sea lo único disponible.
    if is_vos_content(name) or is_french_content(name):
        return 0.0
    # El botón ⬇ de Películas pide UNA PELÍCULA (is_movie, query = solo el
    # título, sin numeración). Un resultado con numeración de capítulo
    # (SxxExx/NxNN) es un capítulo de una serie, NO la película: se excluye
    # como el porno/italiano. Real: el ⬇ de una película ("Leo") bajó
    # "Leo Talks 2x07", una serie que el usuario no tiene, porque al no
    # llevar la query numeración el bloque de episodios no se activaba y el
    # capítulo puntuaba solo por idioma/calidad/fuentes.
    if is_movie and _parse_season_episode(name) is not None:
        return 0.0
    score = 0.0

    # Coincidencia temporada/episodio con la consulta (ver _parse_season_episode).
    expected = _parse_season_episode(query)
    if expected is not None:
        actual = _parse_season_episode(name)
        if actual is not None:
            if actual == expected:
                # El episodio coincide, pero ¿es la MISMA serie? Un "Lucky
                # Luke 1x01 El solitario..." tiene la numeración correcta y
                # comparte la palabra "lucky", pero NO es el "Lucky 1x01"
                # pedido (real: el autocompletado de "Lucky" eligió "Lucky
                # Luke"). Con el +50 de episodio bastaba para ganar pese a
                # tener un título de serie distinto. Se exige que la parte de
                # la SERIE (lo anterior a la numeración) coincida de verdad
                # (mismo criterio estricto que la elección de carpeta de
                # destino); si no, es de OTRA serie y se excluye por completo
                # (0.0, como el porno/italiano), para que no se descargue la
                # serie equivocada aunque sea lo único que devuelva aMule.
                if not _same_series_title(query, name):
                    return 0.0
                score += _W("episode_match")
            else:
                # Misma serie pero temporada/episodio distinto: no se quiere.
                # Se resta agresivo (no solo se evita sumar) para que un
                # capítulo de otra temporada no gane aunque mejore en calidad
                # (real: buscando "2x04" se elegía "22x04 1080p" por encima
                # del "2x04 720p" pedido, porque solo se puntuaba por calidad).
                score += _W("episode_mismatch")

    # Año de la película (ver _years_in_name): si se pide un año concreto
    # (botón ⬇ de Películas, que busca solo por título) y el nombre del
    # resultado lo declara, se exige que coincida. Un remake o un
    # relanzamiento del año equivocado no debe ganar a la película pedida
    # por tener mejor calidad: se premia la coincidencia y se castiga fuerte
    # la discrepancia (mismo criterio que la numeración de episodio). Si el
    # nombre no trae ningún año, no se puede comprobar y no se puntúa.
    if expected_year:
        years = _years_in_name(name)
        if years:
            if expected_year in years:
                score += _W("year_match")
            else:
                score += _W("year_mismatch")

    # Coincidencia del TÍTULO de la serie con la consulta. Además de la
    # numeración (arriba) se exige que el resultado sea de la MISMA serie:
    # buscando "Los Simpsons 2x04", un "Padre de Familia 2x04 1080p" tiene la
    # numeración correcta pero NO se quiere. Solo suma si la consulta trae
    # palabras del título (con "2x04" a secas no hay nada que comparar).
    overlap = _title_overlap(query, name)
    if overlap >= 2:
        score += _W("title_overlap_2")
    elif overlap == 1:
        score += _W("title_overlap_1")
    elif _title_words(query) and name and overlap == 0:
        # Consulta con título pero resultado de OTRA serie (o sin relación):
        # es tan inútil como un capítulo de otra temporada, así que se resta
        # igual de agresivo (-40). Sin esto, un "Padre de Familia 2x04 1080p"
        # ganaba al "Los Simpsons 2x04 720p" pedido porque su numeración
        # coincidía y el resto del score solo premiaba calidad. Riesgo real:
        # un resultado que prescinde del nombre de la serie ("2x04 título")
        # también se resta, pero en aMule los nombres suelen llevar la serie
        # y ese -40 es mejor que elegir el capítulo equivocado.
        score += _W("title_overlap_0")

    # Idioma/audio español
    if _LANG_RE.search(name):
        score += _W("spanish")
    # Otro idioma: si es SOLO extranjero ya se excluyó arriba (0.0); si
    # llega aquí trae español además (o es catalán) y resta una sola vez.
    if has_other_language(name):
        score += _W("non_spanish")

    # Resolución (pesos personalizables, ver _W)
    for rx, wkey in _RES_WEIGHTS:
        if rx.search(name):
            score += _W(wkey)
            break

    # Extensión / contenedor
    ext = _ext(name)
    if ext in _EXT_WEIGHT_KEYS:
        score += _W(_EXT_WEIGHT_KEYS[ext])
    # Una extensión no-video NO es un capítulo descargable.
    if is_non_video_ext(ext):
        score += _W("ext_nonvideo")

    # Indicadores técnicos genéricos (codecs)
    if _GRUPOS_RE.search(name):
        score += _W("tech_generic")
    # Proveedores de confianza de la lista efectiva (ver set_provider_lists,
    # Ajustes → Servidor → Preferencias descargas, apartado Proveedores):
    # cuando el candidato lleva uno, es un indicador fuerte de que es la
    # versión buena. El tamaño atípico ya penaliza por su cuenta, así que
    # aquí el bonus es siempre el mismo.
    # Grupos de confianza (lista efectiva: ver trusted_provider_match).
    _trust_hit = trusted_provider_match(name)
    if _trust_hit:
        score += _W("trusted")

    # Hints de fuente/codificación
    lower = name.lower()
    for hint in _SOURCE_HINTS:
        if hint in lower:
            score += _W("source_hint")

    # Muestras/trailers/capturas de cine NO son el capítulo completo
    # (motor más listas editables p2p_blocked_sample / p2p_blocked_scr).
    if _SAMPLE_RE.search(name) or sample_match(name):
        score += _W("sample")
    if _SCR_RE.search(name) or scr_match(name):
        score += _W("scr")
    if _PROPER_RE.search(name):
        score += _W("proper")

    # Fuentes (más = más fiable) – tope alto para que 205 fuentes pese
    # claramente más que 2, sin que un mediocre gane solo por fuentes.
    s = int(result.sources or 0)
    score += (min(s, 10) * _W("sources_base")
              + min(max(s - 10, 0), 20) * _W("sources_mid")
              + min(max(s - 30, 0), 70) * _W("sources_high"))
    if s > 100:
        score += _W("sources_bonus_100")
    if s > 150:
        score += _W("sources_bonus_150")
    if result.complete:
        score += _W("complete")

    # Tamaño: si hay tamaño típico de la serie/temporada en el servidor,
    # proporcionado suma y desviado resta EN PROPORCIÓN al desvío
    # (|size/típico − 1| × peso Desviado). Si no hay típico, manda el
    # rango fijo por resolución: en rango suma, fuera resta (incluye
    # diminutos y pasados).
    size = _size_bytes(result)
    if size and typical_size and typical_size > 0 and not is_movie:
        ratio = size / typical_size
        if 0.7 <= ratio <= 1.4:
            score += _W("size_ok")
        else:
            score += _size_typical_penalty(ratio, _W("size_bad"))
    elif size:
        lo, hi = _size_range_for_resolution(name, is_movie)
        if lo <= size <= hi:
            score += _W("size_in_range")
        elif size < lo and size >= lo * 0.5:
            pass  # ligeramente por debajo: ni suma ni resta
        else:
            score += _W("size_out_range")

    return score


def explain_score(result: AmuleSearchResult, query: str = "", expected_year: int = None,
                  is_movie: bool = False, typical_size: int | None = None) -> str:
    """Desglose legible de por qué score_download dio esa puntuación. Para tooltip."""
    if result is None:
        return "Sin resultado"
    name = result.name or ""
    lines = [f"Archivo: {name[:60]}", f"Query: '{query}'" + (f" año {expected_year}" if expected_year else "")]
    # Coincidencia episodio/año/serie
    exp = _parse_season_episode(query)
    act = _parse_season_episode(name)
    if exp:
        lines.append(f"Episodio query {exp} vs result {act} {'✓' if exp==act else '✗'}")
        if exp and act and exp != act:
            lines.append(f"  → episodio distinto {_W('episode_mismatch'):+.0f}")
        elif exp and act and exp == act:
            if not _same_series_title(query, name):
                lines.append("  → serie distinta → excluido (0)")
            else:
                lines.append(f"  → episodio coincide {_W('episode_match'):+.0f}")
    if expected_year:
        years = _years_in_name(name)
        if years:
            lines.append(f"Año en nombre {years} vs esperado {expected_year} {'✓' if expected_year in years else '✗'}")
    # Idioma (pesos efectivos, ver _W)
    if _LANG_RE.search(name):
        lines.append(f"Idioma castellano/spa → {_W('spanish'):+.0f}")
    if _CAT_RE.search(name) or _lang_hit("ca", name):
        lines.append(f"Catalán → {_W('non_spanish'):+.0f}")
    if _ITA_RE.search(name) or _lang_hit("it", name):
        lines.append("Italiano ITA → excluido" if is_italian_only(name) else f"Italiano ITA → {_W('non_spanish'):+.0f}")
    if _GER_RE.search(name) or _lang_hit("de", name):
        lines.append("Alemán GER → excluido" if is_german_only(name) else f"Alemán GER → {_W('non_spanish'):+.0f}")
    if _POR_RE.search(name) or _lang_hit("pt", name):
        lines.append("Portugués → excluido" if is_portuguese_only(name) else f"Portugués → {_W('non_spanish'):+.0f}")
    if is_vos_content(name) or is_french_content(name):
        lines.append("V.O.S./VOSTFR explícito (sin dual) → excluido")
    if is_adult_content(name):
        lines.append("Contenido adulto → excluido")
    if is_user_blocked(name):
        lines.append("Marca bloqueada (lista propia en Ajustes) → excluido")
    # Resolución
    for rx, wkey in _RES_WEIGHTS:
        if rx.search(name):
            lines.append(f"Resolución {rx.pattern[:15]} → {_W(wkey):+.0f}")
            break
    ext = _ext(name)
    if ext in _EXT_WEIGHT_KEYS:
        lines.append(f"Contenedor .{ext} → {_W(_EXT_WEIGHT_KEYS[ext]):+.0f}")
    if is_non_video_ext(ext):
        lines.append(f"Extensión no-vídeo → {_W('ext_nonvideo'):+.0f}")
    if _GRUPOS_RE.search(name):
        lines.append(f"Indicador técnico → {_W('tech_generic'):+.0f}")
    if _SAMPLE_RE.search(name) or sample_match(name):
        lines.append(f"Muestra/trailer → {_W('sample'):+.0f}")
    if _SCR_RE.search(name) or scr_match(name):
        lines.append(f"Cam/screener → {_W('scr'):+.0f}")
    if _PROPER_RE.search(name):
        lines.append(f"Proper/repack → {_W('proper'):+.0f}")
    _trust_hit = trusted_provider_match(name)
    if _trust_hit:
        _glabel = f"Proveedor de confianza «{_trust_hit}»"
        lines.append(f"{_glabel} → {_W('trusted'):+.0f}")
    # Fuentes
    s = int(result.sources or 0)
    lines.append(f"Fuentes {s} → +{min(s,10)*_W('sources_base') + min(max(s-10,0),20)*_W('sources_mid') + min(max(s-30,0),70)*_W('sources_high') + (_W('sources_bonus_100') if s>100 else 0) + (_W('sources_bonus_150') if s>150 else 0):.1f} (capped)")
    if result.complete:
        lines.append(f"Completo → {_W('complete'):+.0f}")
    # Tamaño
    size = _size_bytes(result)
    if size and typical_size and not is_movie:
        ratio = size / typical_size
        lines.append(f"Tamaño {size/1024/1024:.0f} MB vs típico {typical_size/1024/1024:.0f} MB ratio {ratio:.2f}")
        if 0.7 <= ratio <= 1.4:
            lines.append(f"  → tamaño proporcionado {_W('size_ok'):+.0f}")
        else:
            _pen = _size_typical_penalty(ratio, _W("size_bad"))
            lines.append(f"  → tamaño desviado {_pen:+.1f} (desvío {abs(ratio - 1.0) * 100:.0f}% × {_W('size_bad'):+.0f})")
    elif size:
        lo, hi = _size_range_for_resolution(name, is_movie)
        lines.append(f"Tamaño {size/1024/1024:.0f} MB rango {lo/1024/1024:.0f}-{hi/1024/1024:.0f} MB")
        if lo <= size <= hi:
            lines.append(f"  → en rango {_W('size_in_range'):+.0f}")
        else:
            lines.append(f"  → fuera de rango {_W('size_out_range'):+.0f}")
    # Total
    lines.append(f"Total score: {score_download(result, query, expected_year, is_movie, typical_size):.1f} (umbral {_W('min_best_score'):.0f})")
    return "\n".join(lines)


# Umbral mínimo para considerar un resultado "candidato recomendable" --
# por debajo de él no se destaca NINGÚN resultado (best_result devuelve
# None), por si la lista no trae nada que de verdad coincida con la
# búsqueda. El valor de fábrica (ver SCORE_WEIGHT_DEFAULTS min_best_score)
# se calibró contra ejemplos reales: un capítulo pobre pero correcto
# (SD + mkv + fuentes) ronda ~30, mientras que basura sin resolver (nada
# de idioma/resolución/calidad, 0-1 fuentes, tamaño absurdo) se queda muy
# por debajo. Personalizable en Ajustes → Servidor → Preferencias
# descargas, sección Puntuación.
MIN_BEST_SCORE = 15.0


def best_result(results: list, query: str = "", expected_year: int = None,
                is_movie: bool = False, typical_size: int | None = None,
                max_size: int | None = None) -> AmuleSearchResult | None:
    """Devuelve el resultado (elemento de la lista) con mayor score, o None
    si la lista está vacía O si ninguno alcanza el mínimo (peso
    min_best_score, personalizable) -- así, cuando no hay ningún resultado
    que coincida de verdad con la búsqueda, no se destaca ningún capítulo (la fila sale del
    color normal). Empates: se queda con el primero (el orden de la búsqueda
    suele ser antigüedad/prioridad). *query* se reenvía a score_download
    para exigir que el mejor candidato coincida con la temporada/episodio
    pedido (ver score_download), y *expected_year* hace lo propio con el año
    de una película (botón ⬇ de Películas: busca solo por título y comprueba
    aquí que el release elegido declara el año pedido). *is_movie* marca una
    petición de película (botón ⬇ de Películas): los resultados con
    numeración de capítulo se excluyen (ver score_download). *max_size* (uso
    de "Adelgazar" en Liberar espacio): techo en bytes -- los resultados que
    lo igualen o superen se descartan antes de puntuar, para no "adelgazar"
    descargando algo igual o más pesado que lo que ya hay."""
    if not results:
        return None
    # Los resultados porno se descartan SIEMPRE: ni como mejor candidato ni
    # como "segundo mejor" (un XXX 4K no debe ganar jamás). El italiano SIN
    # español también (is_italian_only): si lo único que hay es italiano, no
    # se destaca/descarga nada. Igual el alemán y el portugués/brasileño SIN
    # español (is_german_only/is_portuguese_only). Y en una petición de
    # película (is_movie), los capítulos de serie (numeración SxxExx/NxNN)
    # tampoco son candidatos (ver score_download).
    candidates = [r for r in results
                  if not is_adult_content(r.name)
                  and not is_italian_only(r.name)
                  and not is_vos_content(r.name)
                  and not is_french_content(r.name)
                  and not is_german_only(r.name)
                  and not is_portuguese_only(r.name)
                  and not is_user_blocked(r.name)
                  and not (is_movie and _parse_season_episode(r.name) is not None)]
    if max_size and max_size > 0:
        # "Adelgazar": solo vale algo ESTRICTAMENTE más ligero que lo que
        # ya hay (con un 1% de margen por redondeos de "450,5 MB").
        candidates = [r for r in candidates
                      if not _size_bytes(r) or _size_bytes(r) < max_size * 0.99]
    if not candidates:
        return None
    best = candidates[0]
    best_score = score_download(best, query, expected_year, is_movie, typical_size)
    for r in candidates[1:]:
        s = score_download(r, query, expected_year, is_movie, typical_size)
        if s > best_score:
            best, best_score = r, s
    if best_score < _W("min_best_score"):
        return None
    return best