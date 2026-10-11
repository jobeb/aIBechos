"""upload_bytes escribe a un temporal y renombra encima: quien lee el JSON
compartido mientras otro lo escribe ve el viejo entero, nunca uno a medias,
y un corte a mitad deja el original intacto. Real: 8 avisos de
"aIBechos_episodios_que_faltan.json corrupto en el servidor" (9-10/10)."""

import ftplib
import io

from core.ftp_client import FTPClient
from core.sftp_client import SFTPClient


class _FtpEnMemoria:
    def __init__(self, files=None, rename_overwrites=True):
        self.files = dict(files or {})
        self.rename_overwrites = rename_overwrites
        self.sock = None
        self.during_stor = None   # lo que vería un lector a mitad de subida

    def voidcmd(self, cmd):
        return "200 OK"

    def cwd(self, path):
        return None

    def storbinary(self, cmd, fp, *a, **k):
        path = cmd[len("STOR "):]
        self.files[path] = b""                 # STOR trunca al empezar
        self.during_stor = dict(self.files)
        data = fp.read()
        if getattr(self, "cut", False):
            raise ftplib.error_temp("426 conexión cortada")
        self.files[path] = data

    def rename(self, src, dst):
        if dst in self.files and not self.rename_overwrites:
            raise ftplib.error_perm("550 destino existe")
        self.files[dst] = self.files.pop(src)

    def delete(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 no existe")
        del self.files[path]


def _ftp(fake):
    c = FTPClient()
    c.ftp = fake
    return c


DEST = "/datos2/aIBechos/aIBechos_episodios_que_faltan.json"


def test_readers_never_see_a_half_written_file():
    fake = _FtpEnMemoria({DEST: b'{"viejo": 1}'})
    ok, _ = _ftp(fake).upload_bytes(b'{"nuevo": 2}', DEST)
    assert ok
    assert fake.during_stor[DEST] == b'{"viejo": 1}'
    assert fake.files == {DEST: b'{"nuevo": 2}'}, "no deben quedar temporales"


def test_a_cut_mid_upload_leaves_the_original_intact():
    fake = _FtpEnMemoria({DEST: b'{"viejo": 1}'})
    fake.cut = True
    ok, _ = _ftp(fake).upload_bytes(b'{"nuevo": 2}', DEST)
    assert not ok
    assert fake.files == {DEST: b'{"viejo": 1}'}


def test_servers_that_do_not_rename_over_existing_files():
    fake = _FtpEnMemoria({DEST: b"viejo"}, rename_overwrites=False)
    ok, _ = _ftp(fake).upload_bytes(b"nuevo", DEST)
    assert ok
    assert fake.files == {DEST: b"nuevo"}


def test_first_write_when_the_file_does_not_exist():
    fake = _FtpEnMemoria()
    ok, _ = _ftp(fake).upload_bytes(b"nuevo", DEST)
    assert ok
    assert fake.files == {DEST: b"nuevo"}


class _SftpEnMemoria:
    def __init__(self, files, posix=True):
        self.files = dict(files)
        self.posix = posix

    def stat(self, path):
        return object()   # ensure_remote_dir: la carpeta existe

    def open(self, path, mode="rb"):
        files = self.files

        class _W(io.BytesIO):
            def __exit__(s, *a):
                files[path] = s.getvalue()
                return False
        return _W()

    def posix_rename(self, src, dst):
        if not self.posix:
            raise IOError("extensión no soportada")
        self.files[dst] = self.files.pop(src)

    def rename(self, src, dst):
        if dst in self.files:
            raise IOError("destino existe")   # SFTP estándar
        self.files[dst] = self.files.pop(src)

    def remove(self, path):
        self.files.pop(path)

    def get_channel(self):
        return None


def _sftp(fake):
    c = SFTPClient()
    c.sftp = fake
    c.is_connected = lambda: True
    c.ensure_remote_dir = lambda d: (True, d)
    return c


def test_sftp_uses_posix_rename():
    fake = _SftpEnMemoria({DEST: b"viejo"})
    ok, _ = _sftp(fake).upload_bytes(b"nuevo", DEST)
    assert ok
    assert fake.files == {DEST: b"nuevo"}


def test_sftp_without_posix_rename_falls_back_to_remove_and_rename():
    fake = _SftpEnMemoria({DEST: b"viejo"}, posix=False)
    ok, _ = _sftp(fake).upload_bytes(b"nuevo", DEST)
    assert ok
    assert fake.files == {DEST: b"nuevo"}
