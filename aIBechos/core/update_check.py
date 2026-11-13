"""
Comprueba en GitHub Releases si hay una versión de aIBechos más nueva que
la que se está ejecutando. Solo consulta y compara -- nunca descarga ni
reemplaza nada, ver gui/app.py::_show_update_dialog.
"""

import requests

from core.applog import get_logger

_log = get_logger("aIBechos.update_check", "update_check.log")

GITHUB_REPO = "jobeb/aIBechos"
_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> tuple:
    """"v1.10.2" -> (1, 10, 2). Necesario para comparar numéricamente --
    comparar las cadenas directamente haría que "1.9.0" > "1.10.0"."""
    tag = tag.lstrip("vV")
    parts = []
    for piece in tag.split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def latest_release_status(current_version: str, timeout: int = 10) -> dict:
    """Estado de la última release de GitHub frente a *current_version*:
    {"status": "update", "tag", "url"} si hay una más nueva,
    {"status": "current", "tag"} si ya es la última (o una anterior), o
    {"status": "error", "error"} si la consulta falla o la respuesta no trae
    los campos esperados. No lanza nunca. La comprobación manual de Ajustes
    necesita distinguir "al día" de "no se pudo consultar"."""
    try:
        resp = requests.get(
            _RELEASES_URL, timeout=timeout,
            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Fallo al consultar la última release de GitHub: %s", e)
        return {"status": "error", "error": str(e)}

    tag = data.get("tag_name", "") if isinstance(data, dict) else ""
    html_url = data.get("html_url", "") if isinstance(data, dict) else ""
    if not tag or not html_url:
        return {"status": "error", "error": "respuesta sin tag_name/html_url"}

    try:
        if _parse_version(tag) <= _parse_version(current_version):
            return {"status": "current", "tag": tag}
    except Exception as e:
        _log.warning("Fallo al comparar versiones ('%s' vs '%s'): %s", tag, current_version, e)
        return {"status": "error", "error": str(e)}

    _log.info("Nueva versión disponible: %s (actual: %s)", tag, current_version)
    return {"status": "update", "tag": tag, "url": html_url}


def check_for_update(current_version: str, timeout: int = 10):
    """Devuelve (tag_name, html_url) de la última release de GitHub si es
    más nueva que *current_version*, o None si no lo es, si la consulta
    falla, o si la respuesta no trae los campos esperados. No lanza nunca --
    ver latest_release_status."""
    st = latest_release_status(current_version, timeout=timeout)
    if st["status"] != "update":
        return None
    return st["tag"], st["url"]
