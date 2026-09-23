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
