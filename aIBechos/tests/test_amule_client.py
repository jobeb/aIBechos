"""Tests de helpers puros de core/amule_client.py (sin aMule real).

Cubre hash_in_shared_output: detectar si un hash MD4 ya está en la salida
de `amulecmd show shared` (ya descargado en completados) para avisar en
vez de cantar éxito en silencio; y decode_console_output: los acentos de
amulecmd (UTF-8) llegaban como "acciÃ³n" al decodificar con cp1252.
"""

from core.amule_client import decode_console_output, hash_in_shared_output

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


def test_consola_utf8_con_acentos():
    # amulecmd escupe UTF-8: decodificar con la locale (cp1252) daba "acciÃ³n".
    raw = " > 0123456789abcdef0123456789abcdef Acción y aventura 1x05.mkv\n".encode("utf-8")
    assert decode_console_output(raw).splitlines()[0].endswith("Acción y aventura 1x05.mkv")
    assert decode_console_output(b"ascii puro") == "ascii puro"
    assert decode_console_output(b"") == ""


def test_consola_cp1252_de_respaldo():
    # Por si un amulecmd viejo hablara en locale: 0xE9 suelto no es UTF-8
    # válido y cae a windows-1252 en vez de romper o dar �.
    assert decode_console_output(b"acci\xf3n.mkv") == "acción.mkv"
