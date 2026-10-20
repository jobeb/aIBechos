"""FTPClient._local_is_prefix_of_remote: ¿el remoto (mayor) es este archivo
con una cola de más? Real: dos subidas simultáneas a la misma ruta dejaron
"The Seven Deadly Sins" con 864 KB sobrantes."""

from core.ftp_client import FTPClient


def _client(remote: bytes):
    c = FTPClient()
    c.RESUME_CHECK_BYTES = 4
    c._read_remote_range = lambda path, off, n: remote[off:off + n]
    return c


def test_remoto_con_cola_de_mas_es_este(tmp_path):
    local = tmp_path / "a.mkv"
    local.write_bytes(b"0123456789")
    c = _client(b"0123456789XYZ")
    assert c._local_is_prefix_of_remote("/r/a.mkv", local, 13)


def test_remoto_distinto_no(tmp_path):
    local = tmp_path / "a.mkv"
    local.write_bytes(b"0123456789")
    assert not _client(b"0123456abcXYZ")._local_is_prefix_of_remote("/r/a.mkv", local, 13)
    assert not _client(b"abcd456789XYZ")._local_is_prefix_of_remote("/r/a.mkv", local, 13)


def test_remoto_igual_o_menor_no(tmp_path):
    local = tmp_path / "a.mkv"
    local.write_bytes(b"0123456789")
    assert not _client(b"0123456789")._local_is_prefix_of_remote("/r/a.mkv", local, 10)


def test_sin_leer_remoto_no(tmp_path):
    local = tmp_path / "a.mkv"
    local.write_bytes(b"0123456789")
    c = FTPClient()
    c._read_remote_range = lambda *a: None
    assert not c._local_is_prefix_of_remote("/r/a.mkv", local, 13)
