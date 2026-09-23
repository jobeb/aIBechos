import tarfile
import zipfile
from pathlib import Path

import pytest

import core.archive_extract as archive_extract_mod
from core.archive_extract import extract_archive, _configure_windows_rar_fallback_tool


@pytest.fixture(autouse=True)
def _reset_rar_fallback_probe():
    """_configure_windows_rar_fallback_tool solo busca en el sistema la
    primera vez (ver _rar_fallback_probed) -- aislar cada test entre sí,
    o el orden de ejecución cambiaría el resultado."""
    archive_extract_mod._rar_fallback_probed = False
    yield
    archive_extract_mod._rar_fallback_probed = False


def test_extract_zip_success(tmp_path):
    archive = tmp_path / "MiComic.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("MiComic 01.cbz", b"contenido falso")

    ok, dest = extract_archive(archive)
    assert ok is True
    dest_path = Path(dest)
    assert dest_path == tmp_path / "MiComic"
    assert (dest_path / "MiComic 01.cbz").read_bytes() == b"contenido falso"


def test_extract_zip_rejects_zip_slip(tmp_path):
    archive = tmp_path / "Malicioso.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zi = zipfile.ZipInfo("../evil.txt")
        zf.writestr(zi, b"payload")

    ok, msg = extract_archive(archive)
    assert ok is False
    assert "zip-slip" in msg.lower() or "inseguras" in msg.lower()
    assert not (tmp_path / "evil.txt").exists()


def test_extract_zip_corrupt_file_returns_error(tmp_path):
    archive = tmp_path / "Corrupto.zip"
    archive.write_bytes(b"esto no es un zip de verdad")

    ok, msg = extract_archive(archive)
    assert ok is False
    assert isinstance(msg, str) and msg


def test_extract_unsupported_extension(tmp_path):
    archive = tmp_path / "Algo.xyz"
    archive.write_bytes(b"x")
    ok, msg = extract_archive(archive)
    assert ok is False
    assert "no soportado" in msg.lower()


def test_extract_7z_success(tmp_path):
    py7zr = pytest.importorskip("py7zr")

    src_file = tmp_path / "Libro.pdf"
    src_file.write_bytes(b"contenido pdf falso")

    archive = tmp_path / "MiLibro.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.write(src_file, arcname="Libro.pdf")

    ok, dest = extract_archive(archive)
    assert ok is True
    dest_path = Path(dest)
    assert dest_path == tmp_path / "MiLibro"
    assert (dest_path / "Libro.pdf").read_bytes() == b"contenido pdf falso"


def test_extract_7z_rejects_zip_slip(tmp_path, monkeypatch):
    class _FakeSevenZip:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getnames(self):
            return ["../evil.txt"]

        def extractall(self, path=None):
            raise AssertionError("no debería llegar a extraer un archivo inseguro")

    fake_py7zr = type("fake_py7zr", (), {"SevenZipFile": _FakeSevenZip})
    monkeypatch.setitem(__import__("sys").modules, "py7zr", fake_py7zr)

    archive = tmp_path / "Malicioso.7z"
    archive.write_bytes(b"x")
    ok, msg = extract_archive(archive)
    assert ok is False
    assert "zip-slip" in msg.lower() or "inseguras" in msg.lower()


def test_extract_rar_without_unrar_installed(tmp_path, monkeypatch):
    # is_windows=False: sin esto, en una máquina Windows de verdad
    # _configure_windows_rar_fallback_tool intentaría tocar atributos
    # (UNRAR_TOOL, tool_setup...) que este rarfile de mentira no tiene.
    monkeypatch.setattr("core.archive_extract.is_windows", lambda: False)

    class _FakeRarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def namelist(self):
            return ["Comic 01.cbz"]

        def extractall(self, dest):
            raise fake_rarfile.RarCannotExec("no unrar tool found")

    class _RarCannotExec(Exception):
        pass

    fake_rarfile = type("fake_rarfile", (), {"RarFile": _FakeRarFile, "RarCannotExec": _RarCannotExec})
    monkeypatch.setitem(__import__("sys").modules, "rarfile", fake_rarfile)

    archive = tmp_path / "Comic.rar"
    archive.write_bytes(b"x")
    ok, msg = extract_archive(archive)
    assert ok is False
    assert "unrar" in msg.lower()


def test_extract_rar_falls_back_to_winrar_path_on_windows(tmp_path, monkeypatch):
    """Bug real: un usuario con WinRAR instalado (UnRAR.exe presente de
    verdad en C:\\Program Files\\WinRAR\\) seguía viendo "unrar no está
    instalado", porque rarfile solo busca el comando suelto "unrar" en el
    PATH -- WinRAR nunca se añade solo al PATH. Confirmado contra un
    archivo .rar real del usuario: rarfile.testrar() fallaba con
    RarCannotExec hasta fijar rarfile.UNRAR_TOOL a la ruta completa."""
    monkeypatch.setattr("core.archive_extract.is_windows", lambda: True)

    winrar_path = r"C:\Program Files\WinRAR\UnRAR.exe"
    monkeypatch.setattr("os.path.isfile", lambda p: p == winrar_path)

    calls = {"extractall": 0}

    class _FakeRarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def namelist(self):
            return ["Comic 01.cbz"]

        def extractall(self, dest):
            calls["extractall"] += 1
            if calls["extractall"] == 1:
                # Primer intento (sin configurar): falla, como en la
                # máquina real del usuario antes del arreglo.
                raise fake_rarfile.RarCannotExec("no tool found")
            # Segundo intento, ya con UNRAR_TOOL apuntando a WinRAR: éxito.
            (Path(dest) / "Comic 01.cbz").write_bytes(b"contenido")

    class _RarCannotExec(Exception):
        pass

    tool_setup_calls = []

    def _fake_tool_setup(force=False):
        tool_setup_calls.append(fake_rarfile.UNRAR_TOOL)
        if fake_rarfile.UNRAR_TOOL == winrar_path:
            return object()
        raise _RarCannotExec("still not found")

    fake_rarfile = type("fake_rarfile", (), {
        "RarFile": _FakeRarFile, "RarCannotExec": _RarCannotExec,
        "UNRAR_TOOL": "unrar", "SEVENZIP_TOOL": "7z",
        "tool_setup": staticmethod(_fake_tool_setup),
    })
    monkeypatch.setitem(__import__("sys").modules, "rarfile", fake_rarfile)

    archive = tmp_path / "Comic.rar"
    archive.write_bytes(b"x")
    ok, dest = extract_archive(archive)

    assert ok is True, "debería haber encontrado UnRAR.exe de WinRAR y reintentado con éxito"
    assert (Path(dest) / "Comic 01.cbz").read_bytes() == b"contenido"
    assert fake_rarfile.UNRAR_TOOL == winrar_path
    assert calls["extractall"] == 2, "debe reintentar extractall() tras configurar el tool"


def test_extract_rar_falls_back_when_namelist_itself_fails(tmp_path, monkeypatch):
    """Bug real (2026-07-28): con un .rar real ("Los Simpson...") en una
    máquina con WinRAR instalado pero sin el fallback todavía configurado,
    namelist() -- no solo extractall() -- ya lanzaba RarCannotExec
    ("Cannot find working tool"), contra lo que decía el comentario de
    _extract_rar ("namelist() es Python puro, sin herramienta externa").
    Como el try/except original solo envolvía extractall(), ese fallo de
    namelist() escapaba sin reintentar nada, con el mensaje genérico "No
    se pudo descomprimir: Cannot find working tool" en vez de encontrar
    WinRAR y reintentar. Ahora toda la operación (namelist + extractall)
    vive dentro del mismo try/except, así que el fallback se prueba sea
    cual sea la llamada que falle primero."""
    monkeypatch.setattr("core.archive_extract.is_windows", lambda: True)
    winrar_path = r"C:\Program Files\WinRAR\UnRAR.exe"
    monkeypatch.setattr("os.path.isfile", lambda p: p == winrar_path)

    calls = {"namelist": 0, "extractall": 0}

    class _FakeRarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def namelist(self):
            calls["namelist"] += 1
            if fake_rarfile.UNRAR_TOOL == "unrar":
                # Sin el fallback configurado todavía -- falla aquí, no
                # en extractall() (el caso real que motivó este test).
                raise fake_rarfile.RarCannotExec("Cannot find working tool")
            return ["Los Simpson - 1x01.avi"]

        def extractall(self, dest):
            calls["extractall"] += 1
            (Path(dest) / "Los Simpson - 1x01.avi").write_bytes(b"contenido")

    class _RarCannotExec(Exception):
        pass

    def _fake_tool_setup(force=False):
        if fake_rarfile.UNRAR_TOOL != winrar_path:
            raise _RarCannotExec("still not found")

    fake_rarfile = type("fake_rarfile", (), {
        "RarFile": _FakeRarFile, "RarCannotExec": _RarCannotExec,
        "UNRAR_TOOL": "unrar", "SEVENZIP_TOOL": "7z",
        "tool_setup": staticmethod(_fake_tool_setup),
    })
    monkeypatch.setitem(__import__("sys").modules, "rarfile", fake_rarfile)

    archive = tmp_path / "Los Simpson.rar"
    archive.write_bytes(b"x")
    ok, dest = extract_archive(archive)

    assert ok is True, "debería haber encontrado UnRAR.exe de WinRAR tras el fallo de namelist()"
    assert (Path(dest) / "Los Simpson - 1x01.avi").read_bytes() == b"contenido"
    assert calls["namelist"] == 2, "namelist() se repite tras configurar el tool, no se salta"
    assert calls["extractall"] == 1


def test_extract_rar_still_fails_when_no_tool_found_anywhere_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("core.archive_extract.is_windows", lambda: True)
    monkeypatch.setattr("os.path.isfile", lambda p: False)   # nada instalado en ninguna ruta típica

    class _FakeRarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def namelist(self):
            return ["Comic 01.cbz"]

        def extractall(self, dest):
            raise fake_rarfile.RarCannotExec("no tool found")

    class _RarCannotExec(Exception):
        pass

    fake_rarfile = type("fake_rarfile", (), {
        "RarFile": _FakeRarFile, "RarCannotExec": _RarCannotExec,
        "UNRAR_TOOL": "unrar", "SEVENZIP_TOOL": "7z",
    })
    monkeypatch.setitem(__import__("sys").modules, "rarfile", fake_rarfile)

    archive = tmp_path / "Comic.rar"
    archive.write_bytes(b"x")
    ok, msg = extract_archive(archive)
    assert ok is False
    assert "unrar" in msg.lower()


def test_configure_windows_rar_fallback_tool_returns_false_off_windows(monkeypatch):
    monkeypatch.setattr("core.archive_extract.is_windows", lambda: False)
    fake_rarfile = type("fake_rarfile", (), {})
    assert _configure_windows_rar_fallback_tool(fake_rarfile) is False


def test_configure_windows_rar_fallback_tool_probes_only_once(monkeypatch):
    monkeypatch.setattr("core.archive_extract.is_windows", lambda: True)
    probe_count = {"n": 0}

    def fake_isfile(p):
        probe_count["n"] += 1
        return False

    monkeypatch.setattr("os.path.isfile", fake_isfile)
    fake_rarfile = type("fake_rarfile", (), {"UNRAR_TOOL": "unrar", "SEVENZIP_TOOL": "7z"})

    _configure_windows_rar_fallback_tool(fake_rarfile)
    n_after_first = probe_count["n"]
    assert n_after_first > 0

    _configure_windows_rar_fallback_tool(fake_rarfile)
    assert probe_count["n"] == n_after_first, "la segunda llamada no debería volver a tocar el disco"


def test_extract_rar_success(tmp_path, monkeypatch):
    class _FakeRarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def namelist(self):
            return ["Comic 01.cbz"]

        def extractall(self, dest):
            (Path(dest) / "Comic 01.cbz").write_bytes(b"contenido")

    fake_rarfile = type("fake_rarfile", (), {"RarFile": _FakeRarFile, "RarCannotExec": Exception})
    monkeypatch.setitem(__import__("sys").modules, "rarfile", fake_rarfile)

    archive = tmp_path / "Comic.rar"
    archive.write_bytes(b"x")
    ok, dest = extract_archive(archive)
    assert ok is True
    assert (Path(dest) / "Comic 01.cbz").read_bytes() == b"contenido"


def _make_tar(archive_path, arcname, content, mode="w"):
    src_file = archive_path.parent / f"_src_{archive_path.name}"
    src_file.write_bytes(content)
    with tarfile.open(archive_path, mode) as tf:
        tf.add(src_file, arcname=arcname)
    src_file.unlink()


def test_extract_tar_success(tmp_path):
    archive = tmp_path / "MiComic.tar"
    _make_tar(archive, "MiComic 01.cbz", b"contenido falso")

    ok, dest = extract_archive(archive)
    assert ok is True
    dest_path = Path(dest)
    assert dest_path == tmp_path / "MiComic"
    assert (dest_path / "MiComic 01.cbz").read_bytes() == b"contenido falso"


@pytest.mark.parametrize("suffix,mode", [
    (".tar.gz", "w:gz"), (".tgz", "w:gz"),
    (".tar.bz2", "w:bz2"), (".tbz2", "w:bz2"),
    (".tar.xz", "w:xz"), (".txz", "w:xz"),
])
def test_extract_compressed_tar_variants(tmp_path, suffix, mode):
    archive = tmp_path / f"MiComic{suffix}"
    _make_tar(archive, "MiComic 01.cbz", b"contenido falso", mode=mode)

    ok, dest = extract_archive(archive)
    assert ok is True
    dest_path = Path(dest)
    # El destino debe quitar el sufijo COMPUESTO entero (".tar.gz"), no solo
    # el último punto (".gz") -- si no, quedaría "MiComic.tar" en vez de
    # "MiComic".
    assert dest_path == tmp_path / "MiComic"
    assert (dest_path / "MiComic 01.cbz").read_bytes() == b"contenido falso"


def test_extract_tar_rejects_zip_slip(tmp_path):
    archive = tmp_path / "Malicioso.tar"
    src_file = tmp_path / "_src_evil"
    src_file.write_bytes(b"payload")
    with tarfile.open(archive, "w") as tf:
        tf.add(src_file, arcname="../evil.txt")
    src_file.unlink()

    ok, msg = extract_archive(archive)
    assert ok is False
    assert "zip-slip" in msg.lower() or "inseguras" in msg.lower()
    assert not (tmp_path / "evil.txt").exists()


def test_extract_tar_corrupt_file_returns_error(tmp_path):
    archive = tmp_path / "Corrupto.tar"
    archive.write_bytes(b"esto no es un tar de verdad")

    ok, msg = extract_archive(archive)
    assert ok is False
    assert isinstance(msg, str) and msg
