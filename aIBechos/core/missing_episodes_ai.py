"""
Segunda opinión de IA (Groq) sobre los huecos que ya detectó
core/missing_episodes.py -- decide si una discrepancia entre TMDB y el
servidor es probablemente un hueco real o un artefacto de numeración
(series publicadas por partes que TMDB cuenta como una sola temporada, o
un ID de TMDB emparejado sin todas las temporadas reales de la serie).

Una sola llamada por escaneo (no una por serie) -- se manda de una vez la
lista de series con huecos, con solo el recuento de episodios por
temporada (no las listas de episodios enteras), para mantener el coste
bajo. Solo se activa si el usuario configuró su propia API key de Groq
(ai_fallback_enabled en Ajustes) -- nunca se manda nada sin que lo active.
"""

import json

import requests

from core.ai_title_fallback import GROQ_URL, DEFAULT_MODEL
from core.applog import get_logger

_log = get_logger("aIBechos.missing_episodes_ai", "ai_fallback.log")

# Modelo usado SOLO para la pregunta de doblaje castellano
# (check_spanish_dub=True), en vez de DEFAULT_MODEL -- probado en vivo
# contra la API real de Groq con un caso de respuesta conocida (Bleach,
# doblaje real cortado en el 109) y un truco para distinguir "sabe el
# dato de verdad" de "copia el límite de present_episodes": se le dio a
# propósito un present_episodes FALSO (hasta el 50, nada que ver con el
# 109 real) para ver si el veredicto lo seguía. DEFAULT_MODEL
# (llama-3.3-70b-versatile) y el más pequeño llama-3.1-8b-instant
# cambiaron su respuesta a "50" -- confirma que ninguno de los dos tiene
# conocimiento real, solo copian el límite que se les manda (causa raíz
# confirmada de los falsos positivos de Arcadia/Kung Fu Panda/Futurama).
# openai/gpt-oss-120b se mantuvo en blanco (sin inventar un corte) tanto
# con el límite real como con el falso -- el único que no siguió el
# truco. Los modelos "Compound" con búsqueda web real (groq/compound,
# groq/compound-mini) se probaron también: compound-mini dio CUATRO
# números distintos en cuatro llamadas idénticas (103, 366, 100, 52,
# ninguno correcto) -- la búsqueda parece dar munición para inventar con
# más confianza, no datos fiables, así que se descartó esa vía.
DUB_CHECK_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "Eres un analista que decide, para cada serie de una lista, si la "
    "diferencia entre lo que TMDB dice que debería tener y lo que hay de "
    "verdad en un servidor multimedia es (a) un hueco real -- episodios "
    "que de verdad faltan por conseguir -- o (b) un artefacto de "
    "numeración: TMDB cuenta las temporadas de forma distinta a como está "
    "organizado el servidor (series publicadas en \"partes\" que TMDB "
    "agrupa en una sola temporada, o el ID de TMDB emparejado no tiene "
    "registradas todas las temporadas reales de la serie). En el caso (b) "
    "probablemente NO faltan episodios de verdad.\n"
    "IMPORTANTE: \"numeracion_distinta\" es SOLO para ese desajuste de "
    "conteo -- si los episodios simplemente no están en el servidor por "
    "cualquier OTRA razón (nunca se consiguieron, no están disponibles en "
    "ningún idioma/edición, etc.), eso sigue siendo \"hueco_real\" aunque "
    "el motivo por el que el usuario no los tenga sea comprensible.\n"
    "Te doy una lista de series en JSON, cada una con cuántos episodios "
    "tiene cada temporada según TMDB (tmdb_seasons) y cuántos hay en el "
    "servidor (server_seasons), ambos como un objeto {numero_temporada: "
    "num_episodios}. Algunas series traen también campos opcionales, "
    "úsalos como contexto si están presentes -- no siempre vienen: "
    "original_name (título original), first_air_date (fecha de estreno, "
    "para ubicar la época), origin_country (país de origen), genres "
    "(géneros), missing_episodes y present_episodes (los números CONCRETOS "
    "de los episodios que faltan y que SÍ hay por temporada, no solo el "
    "recuento -- compáralos: si present_episodes no empieza en 1, o tiene "
    "una numeración que no encaja con tmdb_seasons, es una pista fuerte de "
    "artefacto de numeración, no de hueco real). Si viene missing_episodes, "
    "es la lista EXACTA (por número de temporada) de episodios que de "
    "verdad faltan -- tu \"motivo\" tiene que ser coherente con esas "
    "temporadas concretas, nunca generalizar a \"todas las temporadas\" ni "
    "a temporadas que no aparezcan como clave en missing_episodes, aunque "
    "tmdb_seasons/server_seasons de otras temporadas no coincidan exacto "
    "(esa diferencia puede deberse a episodios dobles fusionados en un solo "
    "archivo u otras razones que ya están resueltas si esa temporada no "
    "aparece en missing_episodes). Si viene ftp_filenames, son los nombres "
    "de archivo REALES del servidor para esta serie -- úsalos como fuente "
    "de verdad si detectas algo que missing_episodes/present_episodes no "
    "reflejen bien (p.ej. un episodio doble empaquetado en un mismo "
    "archivo, tipo \"7x21-7x22\" o \"S07E21E22\": ambos números cuentan "
    "como presentes aunque solo haya un archivo). Responde SOLO con un JSON "
    "de una línea, sin texto adicional, con este formato exacto:\n"
    '{"veredictos": [{"tmdb_id": <id>, '
    '"veredicto": "hueco_real"|"numeracion_distinta", '
    '"motivo": "<breve explicacion, maximo 20 palabras>"'
)
_SYSTEM_PROMPT_TAIL = (
    '}]}\n'
    "Un veredicto por cada serie de la lista recibida, sin omitir ninguna."
)

# Se añade SOLO cuando el interruptor "Ocultar sin doblaje ES" está activo
# (ver gui/app.py::_ask_ai_for_missing_ep_verdicts) -- preguntar esto en
# cada consulta gastaría tokens sin que nadie use el dato la mayoría de las
# veces. Insiste explícitamente en CASTELLANO (España), no latino: TMDB no
# distingue entre ambos doblajes y muchas series (p.ej. Bleach, doblada al
# castellano solo hasta el episodio 109 de 366 pese a que el doblaje LATINO
# sí llegó a completarse años después) tienen historiales de doblaje muy
# distintos entre las dos variantes -- confundirlas daría el mismo tipo de
# falso positivo que ya se vio con el intento anterior basado en TMDB.
_DUB_FIELD_INSTRUCTION = (
    ', "doblaje_castellano": {"<numero_temporada>": <ultimo_episodio_doblado>, ...}'
)
_DUB_SYSTEM_SUFFIX = (
    "\nAdemás, para cada temporada con hueco de cada serie, indica en "
    '"doblaje_castellano" el número del ÚLTIMO episodio de esa temporada '
    "que tiene doblaje oficial al CASTELLANO (español de España, NO "
    "español latino/de Latinoamérica -- son doblajes distintos, con "
    "historiales de emisión distintos). Usa first_air_date/original_name "
    "para ubicar la época y edición de la serie (el doblaje castellano de "
    "muchas series antiguas o de nicho se interrumpió a mitad, mientras "
    "que el latino sí se completó años después, o viceversa -- no asumas "
    "que coinciden). Omite la temporada del objeto si el doblaje "
    "castellano cubre TODA la temporada, o si no tienes información "
    "fiable -- no adivines ni asumas que coincide con el doblaje latino.\n"
    "IMPORTANTE: missing_episodes/present_episodes reflejan SOLO lo que "
    "el usuario ya tiene descargado en su servidor -- NUNCA son un dato "
    "real sobre hasta dónde llega el doblaje castellano, aunque coincidan "
    "por casualidad. Si no tienes conocimiento verificable de verdad "
    "sobre el doblaje castellano de ESTA serie en concreto, omite la "
    "temporada -- no uses el último episodio presente en el servidor "
    "como aproximación de por dónde corta el doblaje, es una suposición "
    "sin fundamento y da falsos positivos graves (caso real: dijiste que "
    "el doblaje castellano de una serie cortaba justo donde el usuario "
    "tenía descargado, cuando en realidad toda la serie está doblada).\n"
    "Si viene el campo \"info_doblaje_eldoblaje\", es texto real extraído "
    "de eldoblaje.com (base de datos de doblaje al castellano mantenida "
    "por la comunidad, no una suposición tuya) -- ÚSALO como fuente "
    "principal para el corte, por encima de tu propio conocimiento: suele "
    "indicar directamente cuántos episodios se doblaron (p.ej. \"Consta "
    "de 366 episodios, de los que solo fueron doblados los 109 "
    "primeros.\"). Si ese texto no menciona ningún corte o dice que está "
    "doblada por completo, no inventes uno -- sigue la misma regla de "
    "omitir la temporada si no queda claro.\n"
    "IMPORTANTE: si los episodios que faltan simplemente no tienen "
    "doblaje castellano, eso NUNCA es motivo para elegir "
    '"numeracion_distinta" -- ese veredicto es solo para desajustes de '
    "conteo de temporadas entre TMDB y el servidor, algo totalmente "
    "distinto. Un hueco por falta de doblaje sigue siendo \"hueco_real\" "
    "(los episodios de verdad no están en el servidor); la falta de "
    "doblaje se indica SOLO en \"doblaje_castellano\", nunca cambiando el "
    "veredicto."
)


def analyze_missing_episodes(shows: list, api_key: str, model: str = DEFAULT_MODEL,
                              timeout: int = 30, check_spanish_dub: bool = False) -> dict:
    """*shows*: [{"tmdb_id", "name", "tmdb_seasons": {temporada: num_eps},
    "server_seasons": {temporada: num_eps}}, ...] -- solo series con alguna
    discrepancia (mandar la biblioteca entera no aporta nada, malgasta
    tokens). Campos opcionales por serie, incluidos si están presentes:
    "original_name", "first_air_date", "origin_country", "genres",
    "missing_episodes"/"present_episodes" ({temporada: [episodios]}, los
    números concretos en vez de solo el recuento), "ftp_filenames" (nombres
    de archivo reales del servidor para esta serie, sin ruta -- refuerzo
    para que la IA vea patrones, como episodios dobles, que un recuento no
    distingue) -- dan más contexto real a la IA que el nombre y los
    recuentos solos.

    check_spanish_dub=True añade la pregunta de doblaje castellano a la
    MISMA consulta (ver _DUB_SYSTEM_SUFFIX) -- solo cuando el usuario tiene
    activo "Ocultar sin doblaje ES", para no gastar tokens de más en la
    consulta normal. En ese caso, además, se ignora *model* y se usa
    siempre DUB_CHECK_MODEL -- ver su comentario para el porqué.

    Devuelve {tmdb_id: {"veredicto": "hueco_real"|"numeracion_distinta",
    "motivo": str, "doblaje_castellano": {temporada: ultimo_episodio}}},
    o {} si falla cualquier cosa (sin key, sin series, red, formato
    inesperado...) -- nunca lanza, quien llama debe poder seguir
    funcionando igual que si la IA no existiera. "doblaje_castellano" solo
    aparece si check_spanish_dub=True."""
    if not api_key or not shows:
        return {}

    # La pregunta de doblaje castellano usa siempre DUB_CHECK_MODEL, no el
    # *model* que pase el llamador -- ver su comentario: es el único
    # probado en vivo que no "adivina" el corte copiando present_episodes.
    effective_model = DUB_CHECK_MODEL if check_spanish_dub else model

    _log.info("Consulta a Groq (modelo=%s) para el veredicto de %d serie(s) con huecos%s",
               effective_model, len(shows), " (con doblaje castellano)" if check_spanish_dub else "")

    def _show_entry(s: dict) -> dict:
        # Campos opcionales -- solo se incluyen si el llamador los mandó
        # (el botón manual por serie los manda, ver
        # gui/app.py::_ask_ai_about_current_missing_ep_show; la consulta
        # por lotes tras un escaneo completo no, para mantener el coste
        # bajo con muchas series a la vez).
        entry = {"tmdb_id": s["tmdb_id"], "name": s["name"],
                 "tmdb_seasons": s["tmdb_seasons"], "server_seasons": s["server_seasons"]}
        for key in ("original_name", "first_air_date", "origin_country", "genres",
                    "missing_episodes", "present_episodes", "info_doblaje_eldoblaje",
                    "ftp_filenames"):
            value = s.get(key)
            if value:
                entry[key] = value
        return entry

    payload = json.dumps([_show_entry(s) for s in shows], ensure_ascii=False)

    system_prompt = SYSTEM_PROMPT + (_DUB_FIELD_INSTRUCTION if check_spanish_dub else "") + _SYSTEM_PROMPT_TAIL
    if check_spanish_dub:
        system_prompt += _DUB_SYSTEM_SUFFIX

    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": effective_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
    except Exception as e:
        _log.warning("Fallo la consulta a Groq para el veredicto de huecos: %s", e)
        return {}

    try:
        parsed = json.loads(content)
        veredictos = parsed.get("veredictos", [])
    except (ValueError, AttributeError, TypeError) as e:
        _log.warning("Respuesta de Groq no parseable para el veredicto de huecos: %s -- %r", e, content)
        return {}

    result = {}
    for v in veredictos:
        if not isinstance(v, dict):
            continue
        tmdb_id = v.get("tmdb_id")
        veredicto = v.get("veredicto")
        if tmdb_id is None or veredicto not in ("hueco_real", "numeracion_distinta"):
            continue
        try:
            entry = {"veredicto": veredicto, "motivo": v.get("motivo", "") or ""}
            if check_spanish_dub:
                dub_raw = v.get("doblaje_castellano")
                dub_cutoff = {}
                if isinstance(dub_raw, dict):
                    for season_str, last_ep in dub_raw.items():
                        try:
                            dub_cutoff[int(season_str)] = int(last_ep)
                        except (TypeError, ValueError):
                            continue
                entry["doblaje_castellano"] = dub_cutoff
            result[int(tmdb_id)] = entry
        except (TypeError, ValueError):
            continue

    _log.info("Groq devolvio %d veredicto(s) (tokens usados: %s)",
               len(result), usage.get("total_tokens"))
    return result
