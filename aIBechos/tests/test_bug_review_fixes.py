"""Fallos encontrados revisando el código: especiales (temporada 0),
sincronizaciones que borraban lo de los demás al fallar una lectura,
descarga FTP abriendo el archivo por bloque y errores locales tomados por
errores FTP."""
import json

import pytest

from core import shared_data
from core.api_client import MediaInfo
from core.app_auto_complete import AutoCompleteMixin
from core.app_history_core import HistoryCoreMixin
from core.app_movies_core import MoviesCoreMixin
from core.ftp_client import FTPClient
from core.renamer import build_new_name


# ── 1. Temporada 0 / episodio 0 ──

def test_especiales_conservan_la_temporada_0():
    m = MediaInfo(1, "tv", "Doctor Who", "Doctor Who", "2005", season=0, episode=5)
    assert build_new_name(m, "{serie} {temporada}x{episodio:02d}{ext}", ".mkv") == "Doctor Who 0x05.mkv"


def test_episodio_0_no_pasa_a_ser_el_1():
    m = MediaInfo(1, "anime", "One Piece", "One Piece", "1999", season=1, episode=0)
    assert build_new_name(m, "{serie} {temporada}x{episodio:03d}{ext}", ".mkv") == "One Piece 1x000.mkv"


def test_sin_numero_sigue_siendo_1():
    m = MediaInfo(1, "libro", "Hulk", "Hulk", "2007")
    assert build_new_name(m, "{serie} #{episodio:02d}{ext}", ".cbr") == "Hulk #01.cbr"


def test_ruta_remota_de_especiales():
    c = FTPClient()
    assert "Temporada 00" in c.build_remote_path("/Series/{serie}/Temporada {temporada:02d}/", "Doctor Who", 0)
    assert "Temporada 01" in c.build_remote_path("/Series/{serie}/Temporada {temporada:02d}/", "X", None)


# ── 2. Pushes que trataban "no se pudo leer" como "vacío" ──

class _FakeFTP:
    """El JSON remoto existe, pero leerlo falla (corte de red)."""
    def __init__(self):
        self.uploaded = []

    def connect(self, *a, **k): return True, ""
    def disconnect(self): pass
    def download_bytes(self, path): return None
    def file_exists(self, path): return True
    def upload_bytes(self, data, path):
        self.uploaded.append(json.loads(data))
        return True, path


class _Host(HistoryCoreMixin, AutoCompleteMixin, MoviesCoreMixin):
    def __init__(self, ftp):
        self.config_data = {"ftp_host": "h", "ftp_port": 21}
        self._ftp = ftp
        self.later = []

    def _new_ftp_client(self): return self._ftp
    def _reservations_remote_path(self): return "/x/reservas.json"
    def _auto_series_remote_path(self): return "/x/auto.json"
    def _missing_movies_remote_path(self): return "/x/movies.json"
    def after(self, ms, fn): self.later.append(fn)


@pytest.fixture
def inline_threads(monkeypatch):
    import threading

    class _T:
        def __init__(self, target, daemon=None): self.t = target
        def start(self): self.t()
    monkeypatch.setattr(threading, "Thread", _T)
    monkeypatch.setattr(shared_data, "PARTIAL_RETRY_DELAY", 0)


def test_reservas_no_se_suben_si_no_se_pudo_leer_el_remoto(inline_threads):
    ftp = _FakeFTP()
    _Host(ftp)._push_reservations_to_ftp(lambda d: dict(d, nueva={"x": 1}))
    assert ftp.uploaded == []


def test_autocompletado_no_se_sube_si_no_se_pudo_leer_el_remoto(inline_threads):
    ftp = _FakeFTP()
    _Host(ftp)._push_auto_series_to_ftp(lambda d: dict(d, nueva={"x": 1}))
    assert ftp.uploaded == []


def test_cache_de_peliculas_no_se_sube_si_no_se_pudo_leer_el_remoto(inline_threads, monkeypatch):
    import core.missing_movies_cache as mmc
    monkeypatch.setattr(mmc, "load_cache", lambda: {"603": {"title": "Matrix"}})
    ftp = _FakeFTP()
    _Host(ftp)._push_missing_movies_to_ftp()
    assert ftp.uploaded == []


# ── 5 y 6. download_file y errores locales ──

class _BlobFTP:
    def __init__(self, chunks, exc=None):
        self.chunks, self.exc = chunks, exc

    def voidcmd(self, *a): return "200"

    def retrbinary(self, cmd, cb, blocksize=8192):
        for c in self.chunks:
            cb(c)
        if self.exc:
            raise self.exc


def test_download_file_abre_el_archivo_una_sola_vez(tmp_path, monkeypatch):
    import builtins
    opens = []
    real_open = builtins.open

    def _open(path, *a, **k):
        if str(path).endswith("bajado.mkv"):
            opens.append(a)
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", _open)
    c = FTPClient()
    c.ftp = _BlobFTP([b"a" * 10] * 50)
    dest = tmp_path / "bajado.mkv"
    ok, _ = c.download_file("/r.mkv", str(dest))
    assert ok and dest.stat().st_size == 500 and len(opens) == 1


def test_download_file_disco_local_lleno_es_error_de_archivo(tmp_path):
    import errno
    c = FTPClient()
    c.ftp = _BlobFTP([b"x"], exc=OSError(errno.ENOSPC, "No space left on device"))
    ok, msg = c.download_file("/r.mkv", str(tmp_path / "b.mkv"))
    assert not ok and msg.startswith("Error de archivo")
    c.ftp = _BlobFTP([b"x"], exc=ConnectionResetError("reset"))
    ok, msg = c.download_file("/r.mkv", str(tmp_path / "b.mkv"))
    assert not ok and msg.startswith("Error FTP")


def test_local_readable(tmp_path):
    from core.ftp_client import _local_readable
    f = tmp_path / "a.mkv"
    f.write_bytes(b"1")
    assert _local_readable(str(f))
    assert not _local_readable(str(tmp_path / "no-existe.mkv"))
