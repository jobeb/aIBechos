"""
Fallback de IA (Groq) para limpiar el título de un archivo cuando la
detección local (core/api_client.py) no encuentra resultados en TMDB. Solo
se activa si el usuario lo configura explícitamente en Ajustes con su propia
API key — no se envía nada a terceros sin que el usuario lo active.

Cada consulta (con o sin éxito) queda registrada en ai_fallback.log para que
quede constancia de cuándo y cuántas veces se usa la IA.
"""

import json
from typing import Optional

import requests

from core.applog import get_logger

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "Eres un extractor de titulos de peliculas/series a partir de nombres de "
    "archivo de version 'escena'/P2P. Te doy un nombre de archivo (sin extension) "
    "con ruido tecnico (calidad, codec, idioma, grupo de subida, etc). Responde "
    "SOLO con un JSON de una linea, sin texto adicional, con este formato exacto:\n"
    '{"title": "<titulo limpio, tal cual se buscaria en TMDB>", '
    '"junk_tokens": ["<token1>", "<token2>", ...]}\n'
    "junk_tokens debe listar las palabras/fragmentos EXACTOS (tal cual aparecen "
    "en el nombre original, respetando mayusculas/caracteres) que consideraste "
    "ruido y NO forman parte del titulo real. No incluyas el titulo en junk_tokens."
)


# Log rotativo (2 MB × 2 archivos) — registra TODAS las consultas a Groq, con
# éxito o sin él, para poder auditar cuándo y cuánto se usa la IA. Vía
# core/applog.py y no con un RotatingFileHandler propio como antes: este mismo
# fichero lo usan también eldoblaje.py y missing_episodes_ai.py, y un handler
# por módulo impide que el fichero rote en Windows (os.rename sobre un fichero
# que otro handler tiene abierto = WinError 32, ver core/applog.py).
_log = get_logger("aIBechos.ai_fallback", "ai_fallback.log")


def guess_title_via_ai(stem: str, api_key: str, model: str = DEFAULT_MODEL,
                        timeout: int = 15) -> Optional[dict]:
    """Consulta la IA para limpiar *stem* (nombre de archivo sin extensión).
    Devuelve {"title": str, "junk_tokens": list[str]} o None si falla
    cualquier cosa (sin key, red, formato inesperado...). Nunca lanza —
    quien la llama debe seguir funcionando igual que si la IA no existiera."""
    if not api_key:
        return None

    _log.info("Consulta a Groq (modelo=%s) para: %r", model, stem)
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": stem},
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
        _log.warning("Fallo la consulta a Groq para %r: %s", stem, e)
        return None

    try:
        parsed = json.loads(content)
        title = (parsed.get("title") or "").strip()
        junk_tokens = [t for t in parsed.get("junk_tokens", []) if isinstance(t, str)]
    except (ValueError, AttributeError) as e:
        _log.warning("Respuesta de Groq no parseable para %r: %s — %r", stem, e, content)
        return None

    if not title:
        _log.warning("Groq no devolvio titulo para %r — respuesta: %r", stem, content)
        return None

    _log.info("Groq devolvio title=%r junk_tokens=%r (tokens usados: %s) para %r",
               title, junk_tokens, usage.get("total_tokens"), stem)
    return {"title": title, "junk_tokens": junk_tokens}


COMIC_SYSTEM_PROMPT = (
    "Eres un experto en comics y manga. Te doy el titulo de una coleccion/serie, "
    "posiblemente traducido al castellano o mal formateado a partir de un nombre "
    "de archivo. Responde con el titulo ORIGINAL tal cual se buscaria en "
    "ComicVine (normalmente en ingles, o su romanizacion si es manga japones). "
    "Responde SOLO con un JSON de una linea, sin texto adicional, con este "
    "formato exacto:\n"
    '{"original_title": "<titulo original>"}\n'
    "Si no reconoces la serie, devuelve el mismo titulo que te di."
)


def guess_original_comic_title_via_ai(local_title: str, api_key: str, model: str = DEFAULT_MODEL,
                                       timeout: int = 15) -> Optional[str]:
    """Traduce *local_title* (título de cómic/manga detectado localmente,
    a menudo en castellano) al título original con el que se buscaría en
    ComicVine — su catálogo es mayoritariamente en inglés, así que buscar
    con la traducción castellana falla casi siempre. Devuelve el título
    original o None si falla cualquier cosa (sin key, red, formato
    inesperado...). Nunca lanza — mismo contrato que guess_title_via_ai."""
    if not api_key:
        return None

    _log.info("Consulta a Groq (modelo=%s) para traducir titulo de comic: %r", model, local_title)
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": COMIC_SYSTEM_PROMPT},
                    {"role": "user", "content": local_title},
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
        _log.warning("Fallo la consulta a Groq (traduccion de comic) para %r: %s", local_title, e)
        return None

    try:
        parsed = json.loads(content)
        original_title = (parsed.get("original_title") or "").strip()
    except (ValueError, AttributeError) as e:
        _log.warning("Respuesta de Groq (traduccion de comic) no parseable para %r: %s — %r",
                     local_title, e, content)
        return None

    if not original_title:
        _log.warning("Groq no devolvio original_title para %r — respuesta: %r", local_title, content)
        return None

    _log.info("Groq tradujo comic %r -> %r (tokens usados: %s)",
              local_title, original_title, usage.get("total_tokens"))
    return original_title


JUDGE_SYSTEM_PROMPT = (
    "Eres el control de calidad de un descargador de series y películas. Te "
    "digo QUÉ se quiere conseguir y el nombre/tamaño de UN candidato "
    "encontrado en la red eDonkey. Decides si ese archivo es lo pedido.\n"
    "Criterios (todos deben cumplirse para 'ok'):\n"
    "1. Título: el MISMO título/serie y, si se pide capítulo, la MISMA "
    "temporada y episodio (o la película del MISMO año). Un título parecido "
    "de otra obra es 'malo'.\n"
    "2. Idioma: CASTELLANO de España (doblado: espanol, castellano, spanish, "
    "dual con castellano). LATINO solo, VOS (versión original subtitulada), "
    "VOSTFR, italiano, alemán, portugués o francés solo son 'malo'.\n"
    "3. Contenido: nada de porno/erótico aunque el nombre coincida, ni "
    "samples, trailers, extras o fakes evidentes.\n"
    "4. Tamaño: coherente con el esperado (un capítulo no pesa 15 GB ni una "
    "película 100 MB); si no te doy tamaño esperado, solo descarta lo "
    "absurdo.\n"
    "Responde SOLO con un JSON de una línea: "
    '{"verdict": "ok|dudoso|malo", "reason": "<motivo corto en español>"}. '
    "'dudoso' si podría valer pero algo no cuadra del todo."
)


def judge_download_candidate(wanted: dict, candidate: dict, api_key: str,
                             model: str = DEFAULT_MODEL, timeout: int = 20) -> Optional[dict]:
    """Juez IA de un candidato de aMule antes de descargarlo (ver
    JUDGE_SYSTEM_PROMPT): devuelve {"verdict": "ok|dudoso|malo", "reason"}
    o None si falla cualquier cosa (sin key, red, formato...) -- None es
    fail-open: quien llama sigue como si la IA no existiera. Solo un
    "malo" explícito debe bloquear. Cada llamada queda en ai_fallback.log."""
    if not api_key:
        return None
    title = str(wanted.get("title") or "")
    year = str(wanted.get("year") or "")
    kind = "película" if wanted.get("is_movie") else "capítulo de serie"
    expected_size = str(wanted.get("expected_size") or "")
    user_text = (f"Pedido: {kind} '{title}'" + (f" ({year})" if year else "") +
                 (f", tamaño esperado {expected_size}" if expected_size else "") +
                 f"\nCandidato: '{candidate.get('name', '')}' " +
                 f"({candidate.get('size_human', '?')}, {candidate.get('sources', '?')} fuentes)")
    _log.info("Juez Groq (modelo=%s): %s", model, user_text.replace("\n", " | "))
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        _log.warning("Juez Groq falló: %s", e)
        return None
    try:
        parsed = json.loads(content)
        verdict = str(parsed.get("verdict") or "").strip().lower()
        reason = str(parsed.get("reason") or "").strip()
    except (ValueError, AttributeError) as e:
        _log.warning("Juez Groq no parseable: %s — %r", e, content)
        return None
    if verdict not in ("ok", "dudoso", "malo"):
        _log.warning("Juez Groq veredicto desconocido %r — %r", verdict, content)
        return None
    _log.info("Juez Groq: %s (%s)", verdict, reason)
    return {"verdict": verdict, "reason": reason or verdict}


def validate_api_key(api_key: str, timeout: int = 10) -> bool:
    """Comprueba que la API key de Groq es válida, sin gastar una consulta
    de verdad (solo pide el listado de modelos). Igual que guess_title_via_ai,
    la llamada queda registrada en ai_fallback.log."""
    if not api_key:
        return False
    _log.info("Validando API Key de Groq")
    try:
        resp = requests.get(
            GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        ok = resp.status_code == 200
    except Exception as e:
        _log.warning("Fallo al validar la API Key de Groq: %s", e)
        return False
    _log.info("Resultado de validar la API Key de Groq: %s", "valida" if ok else "invalida")
    return ok
