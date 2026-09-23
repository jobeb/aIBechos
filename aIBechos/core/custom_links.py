"""
Enlaces personalizables desde el detector de episodios que faltan --
puramente informativos: construyen una URL sustituyendo variables (como
las plantillas de nombrado) y se abren en el navegador. No se conectan a
nada por su cuenta ni descargan nada -- el usuario decide qué URL
configurar (ficha de TMDB, compartir por WhatsApp, o cualquier otra cosa).
"""

import string
from urllib.parse import quote


class _URLEncodingFormatter(string.Formatter):
    """Aplica el format spec (p.ej. ':02d') sobre el valor original antes
    de URL-encodearlo, en vez de encodear primero y formatear después --
    un str ya encodeado no admite specs numéricos como 'd'."""

    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs.get(key, "")
        return super().get_value(key, args, kwargs)

    def format_field(self, value, format_spec):
        if value == "":
            return ""
        formatted = format(value, format_spec)
        return quote(formatted)


def build_link_url(template: str, variables: dict) -> str:
    """Sustituye {variable} en *template*, con cada valor URL-encodeado
    automáticamente (para que espacios y caracteres especiales no rompan
    la URL resultante). Las variables que no vengan en *variables* (p.ej.
    {temporada} en un enlace a nivel de serie, sin episodio concreto) se
    sustituyen por cadena vacía en vez de fallar con KeyError."""
    safe_vars = {k: v for k, v in variables.items() if v is not None}
    try:
        result = _URLEncodingFormatter().vformat(template, (), safe_vars)
    except (ValueError, IndexError, TypeError):
        return ""
    # Los espacios DENTRO de una variable ya salen como %20 (por quote()
    # arriba) -- esto cubre los espacios que el propio usuario escribe en
    # la plantilla, fuera de las variables (p.ej. "{serie} Temporada
    # {temporada}"), para que la URL final quede bien formada del todo.
    return result.replace(" ", "%20")
