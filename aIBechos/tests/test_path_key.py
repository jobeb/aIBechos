"""Clave canónica de identidad de archivos (ver core/path_key.py)."""

import os
import sys

import pytest

from core.path_key import canon_path


def test_misma_ruta_distinta_caja():
    a = canon_path(r"C:\Users\Jose\Incoming\1x01 foo.avi")
    b = canon_path(r"c:\users\jose\incoming\1X01 FOO.AVI")
    if sys.platform.startswith("win"):
        assert a == b
    else:
        assert a != b  # en POSIX la caja sí distingue


def test_separadores_unificados():
    assert canon_path("C:/Users/Jose/a.avi") == canon_path(r"C:\Users\Jose\a.avi")


def test_quita_prefijo_long_path_y_puntos():
    a = canon_path(r"\\?\C:\Users\Jose\.\Incoming\1x01 foo.avi")
    b = canon_path(r"C:\Users\Jose\Incoming\1x01 foo.avi")
    assert a == b or not sys.platform.startswith("win")


def test_vacia_da_vacia():
    assert canon_path("") == ""
    assert canon_path(None) == ""


def test_no_toca_disco_inexistente():
    rara = os.path.join("no", "existe", "x.avi")
    assert canon_path(rara) == os.path.normcase(os.path.normpath(rara))


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="solo Windows")
def test_drive_con_distinta_caja():
    assert canon_path("C:\\x.avi") == canon_path("c:\\x.avi")
