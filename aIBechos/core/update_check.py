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


def check_for_update(current_version: str, timeout: int = 10):
    """Devuelve (tag_name, html_url) de la última release de GitHub si es
    más nueva que *current_version*, o None si no lo es, si la consulta
    falla, o si la respuesta no trae los campos esperados. No lanza nunca --
    pensado para llamarse en un hilo de fondo al arrancar la app."""
    try:
        resp = requests.get(
            _RELEASES_URL, timeout=timeout,
            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log.warning("Fallo al consultar la última release de GitHub: %s", e)
        return None

    tag = data.get("tag_name", "")
    html_url = data.get("html_url", "")
    if not tag or not html_url:
        return None

    try:
        if _parse_version(tag) <= _parse_version(current_version):
            return None
    except Exception as e:
        _log.warning("Fallo al comparar versiones ('%s' vs '%s'): %s", tag, current_version, e)
        return None

    _log.info("Nueva versión disponible: %s (actual: %s)", tag, current_version)
    return tag, html_url
