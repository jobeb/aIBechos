"""Tests de helpers puros de core/amule_client.py (sin aMule real).

Cubre hash_in_shared_output: detectar si un hash MD4 ya está en la salida
de `amulecmd show shared` (ya descargado en completados) para avisar en
vez de cantar éxito en silencio.
"""

from core.amule_client import hash_in_shared_output

HASH = "fdbcb8a4029728bbc29357d95d18adef"


def test_detecta_hash_en_shared():
    out = (
        "This is amulecmd 3.0.1\n"
        " > FDBCB8A4029728BBC29357D95D18ADEF Cientos de castores (2024).mkv\n"
    )
    assert hash_in_shared_output(out, HASH) is True


def test_no_detecta_hash_ausente():
    out = (
        "This is amulecmd 3.0.1\n"
        " > 1D450481FB0A697EF93E170835ABBC74 Otra pelicula (2026).mkv\n"
    )
    assert hash_in_shared_output(out, HASH) is False


def test_insensible_a_mayusculas():
    out = " > fdbcb8a4029728bbc29357d95d18adef algo.mkv\n"
    assert hash_in_shared_output(out, HASH.upper()) is True


def test_salida_vacia_no_afirma():
    assert hash_in_shared_output("", HASH) is False
    assert hash_in_shared_output(None, HASH) is False


def test_hash_invalido_no_afirma():
    assert hash_in_shared_output(" > abc algo.mkv\n", "") is False
    assert hash_in_shared_output(" > abc algo.mkv\n", None) is False
    assert hash_in_shared_output(" > abc algo.mkv\n", "corto") is False
    # 32 chars pero ausente: tampoco
    assert hash_in_shared_output(" > abc algo.mkv\n", "b" * 32) is False
