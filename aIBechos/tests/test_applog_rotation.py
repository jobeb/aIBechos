"""La rotación de un fichero de log compartido por varios loggers.

El fallo que esto evita (Windows): RotatingFileHandler rota con os.rename, y
renombrar un fichero que otro handler tiene abierto revienta con WinError 32.
Como "app.log" lo usan tres loggers distintos (gui.app, comicvine_client,
duplicate_detect), un handler por logger significaba que la rotación en
caliente fallaba SIEMPRE: logging se traga el error en handleError() -- en una
app gráfica no lo ve nadie -- y a partir de los 2 MB se perdía todo lo que se
registrase hasta el siguiente reinicio. Medido antes del arreglo: 2115 líneas
perdidas y ningún app.log.1. Eso es lo que hizo que las subidas de un usuario
"no aparecieran" en el log y pareciera un fallo del registro de subidas.
"""

import logging
import itertools

import pytest

from core import applog

_nombres = itertools.count()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Carpeta de datos aislada -- nunca el %APPDATA% real del usuario."""
    monkeypatch.setattr(applog, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(applog, "_handlers", {})
    yield tmp_path
    for h in applog._handlers.values():
        h.close()


def _loggers(n: int, filename: str = "app.log"):
    """n loggers con nombre nuevo (logging.getLogger es un registro global:
    reusar nombres entre tests arrastraría handlers de otro tmp_path)."""
    tanda = next(_nombres)
    return [applog.get_logger(f"prueba{tanda}.{i}", filename) for i in range(n)]


def test_varios_loggers_del_mismo_fichero_comparten_un_solo_handler(data_dir):
    a, b, c = _loggers(3)
    assert a.handlers[0] is b.handlers[0] is c.handlers[0]


def test_ficheros_distintos_no_comparten_handler(data_dir):
    (a,) = _loggers(1, "app.log")
    (b,) = _loggers(1, "otro.log")
    assert a.handlers[0] is not b.handlers[0]


def test_el_banner_de_version_sale_una_vez_por_fichero(data_dir):
    _loggers(3)
    banners = [l for l in (data_dir / "app.log").read_text(encoding="utf-8").splitlines()
               if "=== aIBechos v" in l]
    assert len(banners) == 1


def test_rota_sin_perder_registros_con_tres_loggers_abiertos(data_dir, monkeypatch):
    """La regresión: con tres loggers escribiendo, pasar de maxBytes tiene que
    rotar de verdad y no tirar ni una línea."""
    a, _b, _c = _loggers(3)

    perdidos = []
    monkeypatch.setattr(logging.Handler, "handleError",
                        lambda self, record: perdidos.append(record))
    relleno = "x" * 500
    for i in range(6000):
        a.info("linea %d %s", i, relleno)

    assert perdidos == []
    assert (data_dir / "app.log.1").exists()      # rotó de verdad
    assert (data_dir / "app.log").stat().st_size < 2 * 1024 * 1024
