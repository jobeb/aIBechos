"""Puntuación de resultados de búsqueda de aMule para destacar el capítulo
que más conviene bajar (ver pestaña Descargas de la app).

El primer resultado "ideal" no obliga a ordenar la tabla: solo se usa para
pintar de un color distinto la fila del MEJOR candidato. Criterios que
cuenta el score (de mayor a menor peso):

  * Idioma/audio: español/castellano/spanish/spa (patrón "spa"); se
    penaliza fuerte V.O.S. (original + subtítulos, sin doblaje), italiano y
    catalán para que el castellano gane siempre que exista. El italiano SIN
    español se excluye por completo (is_italian_only), igual que el porno:
    si lo único disponible es italiano, no se descarga nada.
  * Calidad de imagen: 4K/2160p > 1080p > 720p > SD
  * Contenedor/flags de grupo notable: MKV > MP4 > AVI; 1xH26x, WEB-DL, HDTV
  * Fiabilidad: más fuentes (sources) y completo (broadcast completo)
  * Tamaño: razonable para la resolución (ni un capítulo de 50 KB ni un
    episodio de 10 GB suelen ser el candidato ideal; se premia el rango
    coherente).
"""

import os
import re
from dataclasses import dataclass

from core.amule_client import AmuleSearchResult
from core.series_match import normalize_series_name, series_similarity


_LANG_RE = re.compile(r"\b(?:spa|spanish|español|espanol|castellano|latino|dual)\b",
                      re.IGNORECASE)
# Catalán: el usuario NO quiere nada en catalán, así que un release que lo
# delate se penaliza fuerte para que jamás gane a uno en español. "cat" suelto
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
_VOS_RE = re.compile(
    r"(?:v\.?o\.?s(?:e|i)?(?:[_-]?es)?\b|vers[ií]on\s+original(?:\s+subtitulad[oa])?\b)",
    re.IGNORECASE)
# Francés: VOSTFR/VOSTA/VF y marcadores de idioma francés. Se excluye igual
# que VOS (ver is_french_content). Cubre "Tensei Shitara Slime Datta Ken
# 4xXX VOSTFR Web (by OtakuNashi).ts".
_FRA_RE = re.compile(r"\b(?:vostfr|vosta|vosta-fr|vf\b|french|français|francais)\b", re.IGNORECASE)
# Italiano (ITA/Italiano/Italiana): ver is_italian_only() -- si el release es
# italiano y no trae NINGÚN rastro de español, se EXCLUYE (como el porno); si
# además lleva español (dual ENG-SPA, "Spanish subs"...), solo se penaliza.
# Usa look-behind/ahead para cazar SUB_ITA / AAC_ITA donde '_' rompe \b.
_ITA_RE = re.compile(r"(?<![A-Za-z0-9])(?:ita|italian|italiano|italiana)(?![A-Za-z0-9])", re.IGNORECASE)
# Marcadores de TÍTULO en italiano (palabras-función y lexemas inequívocos,
# ausentes del castellano/inglés) por si el nombre no lleva el token ITA pero
# el título está traducido al italiano (p.ej. "Un Caso Di Chiaroscuro" o
# "La Mossa Della Bella" 2x06, "Visitatori" 2x13 By Kagome, "La Riunione Che Danza" 2x16). Se exigen >=2
# para no falsear con palabras sueltas compartidas ("la", "un"...), salvo que
# venga con el grupo Kagome (italiano) donde basta 1 o directamente Kagome sin castellano.
_ITA_TITLE_RE = re.compile(
    r"\b(?:il|lo|gli|le|di|del|della|dello|dei|delle|degli|nel|nella|nello|"
    r"nei|negli|sul|sulla|sullo|sui|sulle|dal|dalla|dai|dalle|dagli|"
    r"sempre|dopo|perche|perché|senza|dove|quando|tutto|tutta|tutti|tutte|"
    r"niente|nulla|troppo|ancora|adesso|davvero|amore|morte|notte|giorno|"
    r"storia|famiglia|fratelli|uomini|donne|ragazzi|bambini|signore|grazie|"
    r"ecco|avanti|basta|ciao|bella|bello|belle|belli|mossa|mosse|visitatori|visitatore|speranza|riconciliazione|preparativi|inviti|udienze|avventori|vigilia|apertura|decisione|folla|riunione|danza|che)\b", re.IGNORECASE)
_KAGOME_RE = re.compile(r"kagome", re.IGNORECASE)
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
# Grupos/collectores conocidos que tienden a dar capítulos fiables (se
# distingue "grupots" a propósito porque se ve en nombres reales de aMule).
_GRUPOS_RE = re.compile(r"\b(?:grupots|lnd|dts|cifra|avs|x264|hevvc|x265)\b",
                        re.IGNORECASE)
# Grupos españoles de P2P de MUCHA confianza para el usuario: un release
# subido por ellos suele ser la versión correcta y completa (el nombre del
# grupo va en el nombre del archivo, p.ej. "[exploradoresp2p] ...",
# "...-grupots", "Hispashare.org", "nocturniap2p"). Puntúan muy por encima
# de _GRUPOS_RE: cuando un candidato los lleva y otro no, el de grupo fiable
# debe ganar aunque pierda en resolución/fuentes.
_P2P_TRUSTED_RE = re.compile(
    r"(?:exploradoresp2p|grupots|hispashare(?:\.org)?|nocturniap2p)\b",
    re.IGNORECASE)

_RES_WEIGHTS = [
    # (regex, puntos). El PRIMERO que coincita gana.
    (re.compile(r"\b4k\b|\b2160p\b", re.IGNORECASE), 30),
    (re.compile(r"\b1080p\b|\b1080\b", re.IGNORECASE), 25),
    (re.compile(r"\b720p\b|\b720\b", re.IGNORECASE), 20),
    (re.compile(r"\b576p\b|\b480p\b|\bsd\b|\bdivx\b", re.IGNORECASE), 8),
]

# Contenedores mas frecuentes en orden de preferencia.
_EXT_WEIGHTS = {"mkv": 8, "mp4": 6, "avi": 4, "mov": 3, "divx": 2}

# Subcadenas que delatan fuente WEB / retransmisión / encode (dan puntos).
_SOURCE_HINTS = {"web", "web-dl", "webdl", "hdtv", "dvdrip", "bluray", "bdrip", "h264", "hevc", "x264", "x265"}

_QUALITY_WORDS = {"prover"}

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
    """True si el nombre delata contenido adulto/porno (XXX, porn, hentai...).
    Ver _PORN_RE: se usan solo marcadores inequívocos para no descartar
    títulos legítimos como "Sex Education" o "Adult Swim"."""
    return bool(name and _PORN_RE.search(name))


def is_italian_only(name: str) -> bool:
    """True si el resultado es italiano SIN rastro de español: se excluye
    SIEMPRE de best_result (el usuario no quiere descargas en italiano, ni
    siquiera cuando no hay ningún release en español). NO excluye los releases
    que además traen español (dual "ENG-SPA", "Spanish subs"...): esos solo se
    penalizan (-60) y siguen siendo un candidato válido de emergencia."""
    if not name:
        return False
    # Hay rastro de español (audio o subs): no es "solo italiano".
    if _LANG_RE.search(name):
        return False
    # Marcador explícito ITA/Italiano (caza SUB_ITA, AAC_ITA con (?<![A-Za-z0-9]))
    if _ITA_RE.search(name):
        return True
    # Grupo Kagome es italiano: cualquier Kagome sin castellano es italiano (La Riunione, Visitatori)
    if _KAGOME_RE.search(name):
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
    """True si el nombre delata V.O.S. (versión original subtitulada) sin
    doblaje: se excluye siempre (ver score_download/best_result), igual que
    el porno. Cubre VOS/VOSE/VOSI y 'versión original subtitulada'. Solo se
    considera VOS si no hay rastro de español (dual con castellano se
    mantiene como emergencia, igual que is_italian_only / is_french_only)."""
    if not name or _LANG_RE.search(name):
        return False
    return bool(_VOS_RE.search(name))


def is_french_content(name: str) -> bool:
    """True si el nombre delata contenido en francés / VOSTFR sin español:
    se excluye. Cubre VOSTFR/VOSTA/VF/French. Caso real: 'Tensei Shitara
    Slime Datta Ken 4xXX VOSTFR Web (by OtakuNashi).ts'. Un dual
    'Spanish French Subs' no se excluye (tiene castellano)."""
    if not name or _LANG_RE.search(name):
        return False
    return bool(_FRA_RE.search(name))


# Aliases para compatibilidad con best_result (is_*_only)
def is_vos_only(name: str) -> bool:
    return is_vos_content(name)


def is_french_only(name: str) -> bool:
    return is_french_content(name)


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


def score_download(result: AmuleSearchResult,
                   query: str = "",
                   expected_year: int = None,
                   is_movie: bool = False) -> float:
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
                score += 50.0
            else:
                # Misma serie pero temporada/episodio distinto: no se quiere.
                # Se resta agresivo (no solo se evita sumar) para que un
                # capítulo de otra temporada no gane aunque mejore en calidad
                # (real: buscando "2x04" se elegía "22x04 1080p" por encima
                # del "2x04 720p" pedido, porque solo se puntuaba por calidad).
                score -= 40.0

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
                score += 15.0
            else:
                score -= 40.0

    # Coincidencia del TÍTULO de la serie con la consulta. Además de la
    # numeración (arriba) se exige que el resultado sea de la MISMA serie:
    # buscando "Los Simpsons 2x04", un "Padre de Familia 2x04 1080p" tiene la
    # numeración correcta pero NO se quiere. Solo suma si la consulta trae
    # palabras del título (con "2x04" a secas no hay nada que comparar).
    overlap = _title_overlap(query, name)
    if overlap >= 2:
        score += 12.0
    elif overlap == 1:
        score += 5.0
    elif _title_words(query) and name and overlap == 0:
        # Consulta con título pero resultado de OTRA serie (o sin relación):
        # es tan inútil como un capítulo de otra temporada, así que se resta
        # igual de agresivo (-40). Sin esto, un "Padre de Familia 2x04 1080p"
        # ganaba al "Los Simpsons 2x04 720p" pedido porque su numeración
        # coincidía y el resto del score solo premiaba calidad. Riesgo real:
        # un resultado que prescinde del nombre de la serie ("2x04 título")
        # también se resta, pero en aMule los nombres suelen llevar la serie
        # y ese -40 es mejor que elegir el capítulo equivocado.
        score -= 40.0

    # Idioma/audio español
    if _LANG_RE.search(name):
        score += 18.0
    # Catalán: penalización fuerte. Un release catalán no debe ganar ni
    # siquiera cuando suma el español ("castellano + català" en dual audio:
    # el doblaje sigue siendo catalán, el usuario no lo quiere). Se resta
    # más de lo que puede sumar calidad+fuentes+idioma para que nunca gane.
    if _CAT_RE.search(name):
        score -= 60.0
    # Italiano: penalización cuando trae español (dual) — si es solo italiano
    # ya se excluyó arriba. Mantiene -60 para que no gane a castellano.
    if _ITA_RE.search(name):
        score -= 60.0

    # Resolución
    for rx, w in _RES_WEIGHTS:
        if rx.search(name):
            score += w
            break

    # Extensión / contenedor
    ext = _ext(name)
    score += _EXT_WEIGHTS.get(ext, 0)
    # Una extensión no-video NO es un capítulo descargable.
    if ext in _NON_VIDEO_EXTS:
        score -= 25.0

    # Grupos/contenedores de confianza
    if _GRUPOS_RE.search(name):
        score += 3.0
    # Grupos españoles de P2P de mucha confianza (exploradoresp2p, grupots,
    # hispashare, nocturniap2p): cuando el candidato los lleva, es un
    # indicador fuerte de que es la versión buena -- puntúan muy por encima
    # de un grupo genérico y del idioma/resolución individuales, para que un
    # release fiable gane a otro del mismo título sin esa señal (real: el
    # usuario da prioridad a estos grupos al elegir qué bajar).
    if _P2P_TRUSTED_RE.search(name):
        score += 25.0

    # Hints de fuente/codificación
    lower = name.lower()
    for hint in _SOURCE_HINTS:
        if hint in lower:
            score += 2
    for word in _QUALITY_WORDS:
        if word in lower:
            score += 3

    # Muestras/trailers/capturas de cine NO son el capítulo completo.
    if _SAMPLE_RE.search(name):
        score -= 15.0
    if _SCR_RE.search(name):
        score -= 10.0
    if _PROPER_RE.search(name):
        score += 4.0

    # Fuentes (más = más fiable) – tope alto para que 205 fuentes pese
    # claramente más que 2, sin que un mediocre gane solo por fuentes.
    s = int(result.sources or 0)
    score += min(s, 10) * 1.5 + min(max(s - 10, 0), 20) * 0.7 + min(max(s - 30, 0), 70) * 0.3
    if s > 100:
        score += 5.0
    if s > 150:
        score += 5.0
    if result.complete:
        score += 4.0

    # Tamaño: penaliza tanto aire de nada como descargas absurdas. La
    # penalización por exceso crece con lo desproporcionado que sea el
    # tamaño: un release muy grande (17 GB de una película vieja, p.ej.)
    # cuesta más en disco/cuota de lo que aporta en calidad, y debe perder
    # contra un encode ligero del mismo título aunque tenga algo menos de
    # calidad o de fuentes (real: el botón ⬇ de "Scary Movie" elegía un
    # archivo de 17 GB porque el tamaño solo restaba -2).
    size = _size_bytes(result)
    if size:
        lo, hi = _size_range_for_resolution(name, is_movie)
        if lo <= size <= hi:
            score += 3.0
        elif size < lo * 0.2 and size > 0:  # demasiado pequeño
            score -= 5.0
        elif size > hi:
            over = size / hi
            if over >= 3.0:
                score -= 40.0
            elif over >= 2.0:
                score -= 30.0
            elif over >= 1.5:
                score -= 18.0
            elif over >= 1.1:
                score -= 10.0
            else:
                score -= 5.0

    return score


# Umbral mínimo para considerar un resultado "candidato recomendable" --
# por debajo de él no se destaca NINGÚN resultado (best_result devuelve
# None), por si la lista no trae nada que de verdad coincida con la
# búsqueda. El valor se calibró contra ejemplos reales: un capítulo pobre
# pero correcto (SD + mkv + fuentes) ronda ~30, mientras que basura sin
# resolver (nada de idioma/resolución/calidad, 0-1 fuentes, tamaño absurdo)
# se queda muy por debajo.
MIN_BEST_SCORE = 15.0


def best_result(results: list, query: str = "", expected_year: int = None,
                is_movie: bool = False) -> AmuleSearchResult | None:
    """Devuelve el resultado (elemento de la lista) con mayor score, o None
    si la lista está vacía O si ninguno alcanza el mínimo (ver
    MIN_BEST_SCORE) -- así, cuando no hay ningún resultado que coincida de
    verdad con la búsqueda, no se destaca ningún capítulo (la fila sale del
    color normal). Empates: se queda con el primero (el orden de la búsqueda
    suele ser antigüedad/prioridad). *query* se reenvía a score_download
    para exigir que el mejor candidato coincida con la temporada/episodio
    pedido (ver score_download), y *expected_year* hace lo propio con el año
    de una película (botón ⬇ de Películas: busca solo por título y comprueba
    aquí que el release elegido declara el año pedido). *is_movie* marca una
    petición de película (botón ⬇ de Películas): los resultados con
    numeración de capítulo se excluyen (ver score_download)."""
    if not results:
        return None
    # Los resultados porno se descartan SIEMPRE: ni como mejor candidato ni
    # como "segundo mejor" (un XXX 4K no debe ganar jamás). El italiano SIN
    # español también (is_italian_only): si lo único que hay es italiano, no
    # se destaca/descarga nada. Y en una petición de película (is_movie), los
    # capítulos de serie (numeración SxxExx/NxNN) tampoco son candidatos
    # (ver score_download).
    candidates = [r for r in results
                  if not is_adult_content(r.name)
                  and not is_italian_only(r.name)
                  and not is_vos_content(r.name)
                  and not is_french_content(r.name)
                  and not (is_movie and _parse_season_episode(r.name) is not None)]
    if not candidates:
        return None
    best = candidates[0]
    best_score = score_download(best, query, expected_year, is_movie)
    for r in candidates[1:]:
        s = score_download(r, query, expected_year, is_movie)
        if s > best_score:
            best, best_score = r, s
    if best_score < MIN_BEST_SCORE:
        return None
    return best