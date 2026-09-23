import ftplib

from core.ftp_client import (
    FTPClient, _ftp_safe, _parse_unix_list_dirs, _parse_unix_list_files,
    sizes_by_top_level_folder, files_by_top_level_folder, format_ftp_tree,
)


class _FakeFtp:
    """Doble mínimo de ftplib.FTP para probar upload_file() sin red real."""
    def __init__(self):
        self.stor_calls = []
        self.sock = None

    def voidcmd(self, cmd):
        return "200 OK"

    def cwd(self, path):
        return None   # la carpeta "ya existe" siempre -> ensure_remote_dir no crea nada

    def mkd(self, path):
        raise ftplib.error_perm("550 no debería llamarse")

    def size(self, remote_file):
        raise ftplib.error_perm("550 no existe")   # sin subida previa que reanudar

    def storbinary(self, cmd, fp, blocksize, callback, rest=None):
        self.stor_calls.append(cmd)
        # Simular el envío completo del archivo en un único bloque
        data = fp.read()
        if data:
            callback(data)


def _client_with_fake_ftp():
    client = FTPClient()
    client.ftp = _FakeFtp()
    return client


def test_build_remote_path_tv_template():
    client = FTPClient()
    path = client.build_remote_path(
        "/datos2/series/{serie}/Temporada {temporada:02d}/",
        serie="Breaking Bad", season=3, year="2008", media_type="tv",
    )
    assert path == "/datos2/series/Breaking Bad/Temporada 03/"


def test_build_remote_path_defaults_season_to_one_when_none():
    client = FTPClient()
    path = client.build_remote_path(
        "/series/{serie}/Temporada {temporada:02d}/",
        serie="One Piece", season=None, media_type="tv",
    )
    assert "Temporada 01" in path


def test_build_remote_path_normalizes_backslashes_and_leading_slash():
    client = FTPClient()
    path = client.build_remote_path(
        "peliculas\\{serie}\\{año}\\", serie="Inception", year="2010", media_type="movie",
    )
    assert path == "/peliculas/Inception/2010/"


def test_build_remote_path_tipo_variable():
    client = FTPClient()
    tv_path = client.build_remote_path("/{tipo}/{serie}/", serie="X", media_type="tv")
    movie_path = client.build_remote_path("/{tipo}/{serie}/", serie="X", media_type="movie")
    assert "Series" in tv_path
    assert "Películas" in movie_path


def test_build_remote_path_falls_back_to_raw_template_on_bad_key():
    client = FTPClient()
    path = client.build_remote_path("/{campo_inexistente}/", serie="X")
    assert path == "/{campo_inexistente}/"


def test_ftp_safe_strips_problematic_chars():
    assert _ftp_safe('Serie: "Especial"? |Extra*') == "Serie Especial Extra"


class _FakeFtpForSpace:
    """Doble de ftplib.FTP para probar get_free_space(path) -- registra a
    qué directorios se hizo cwd, para comprobar que se entra al de destino
    y se vuelve al de antes al terminar."""
    def __init__(self, avbl_response=None, banner="220 (vsFTPd 3.0.3)"):
        self.cwd_calls = []
        self._avbl_response = avbl_response
        self._banner = banner
        self._cwd = "/"

    def pwd(self):
        return self._cwd

    def cwd(self, path):
        self.cwd_calls.append(path)
        self._cwd = path

    def sendcmd(self, cmd):
        import ftplib
        if cmd == "STAT":
            return self._banner
        if cmd == "AVBL" and self._avbl_response is not None:
            return self._avbl_response
        raise ftplib.error_perm("502 comando no soportado")


def test_get_free_space_returns_none_without_connection():
    client = FTPClient()
    assert client.get_free_space("/datos2/series/") is None


def test_get_free_space_cwds_into_target_path_and_back():
    client = FTPClient()
    fake = _FakeFtpForSpace(avbl_response="213 123456789", banner="220 ProFTPD")
    fake._cwd = "/inicio"
    client.ftp = fake

    free = client.get_free_space("/datos2/series/")

    assert free == 123456789
    assert fake.cwd_calls == ["/datos2/series/", "/inicio"], fake.cwd_calls


def test_get_free_space_none_when_server_is_vsftpd():
    """El servidor real de este proyecto (vsftpd) no soporta ningún comando
    de espacio libre -- debe degradar a None, no lanzar ni inventar un número."""
    client = FTPClient()
    client.ftp = _FakeFtpForSpace(avbl_response="213 999999999", banner="220 (vsFTPd 3.0.3)")
    assert client.get_free_space("/datos2/series/") is None


def test_get_free_space_without_path_does_not_cwd():
    client = FTPClient()
    fake = _FakeFtpForSpace(avbl_response="213 555555555", banner="220 ProFTPD")
    client.ftp = fake
    free = client.get_free_space()
    assert free == 555555555
    assert fake.cwd_calls == []


def test_ftp_safe_keeps_normal_text():
    assert _ftp_safe("One Piece") == "One Piece"


def test_parse_unix_list_dirs_extracts_only_directories():
    lines = [
        "drwxr-xr-x 2 ftp ftp 4096 Jan 01 12:00 Breaking Bad",
        "-rw-r--r-- 1 ftp ftp  123 Jan 01 12:00 readme.txt",
        "drwxr-xr-x 2 ftp ftp 4096 Jan 01 12:00 One Piece",
    ]
    assert _parse_unix_list_dirs(lines) == ["Breaking Bad", "One Piece"]


def test_parse_unix_list_dirs_ignores_dot_entries():
    lines = [
        "drwxr-xr-x 2 ftp ftp 4096 Jan 01 12:00 .",
        "drwxr-xr-x 2 ftp ftp 4096 Jan 01 12:00 ..",
        "drwxr-xr-x 2 ftp ftp 4096 Jan 01 12:00 Serie",
    ]
    assert _parse_unix_list_dirs(lines) == ["Serie"]


def test_parse_unix_list_dirs_empty_input():
    assert _parse_unix_list_dirs([]) == []


def test_parse_unix_list_files_extracts_only_files():
    lines = [
        "drwxr-xr-x 2 ftp ftp 4096 Jan 01 12:00 Temporada 01",
        "-rw-r--r-- 1 ftp ftp  123 Jan 01 12:00 Serie 1x01.mkv",
        "-rw-r--r-- 1 ftp ftp  456 Jan 01 12:00 Serie 1x02.mkv",
    ]
    assert _parse_unix_list_files(lines) == ["Serie 1x01.mkv", "Serie 1x02.mkv"]


def test_parse_unix_list_files_empty_input():
    assert _parse_unix_list_files([]) == []


class _FakeFtpMlsd:
    def __init__(self, entries):
        self._entries = entries   # [(name, {"type": "file"|"dir"}), ...]

    def voidcmd(self, cmd):
        return "200 OK"

    def mlsd(self, path):
        return iter(self._entries)


def test_list_files_uses_mlsd_when_supported():
    client = FTPClient()
    client.ftp = _FakeFtpMlsd([
        (".", {"type": "cdir"}),
        ("..", {"type": "pdir"}),
        ("Temporada 01", {"type": "dir"}),
        ("Serie 1x01.mkv", {"type": "file"}),
        ("Serie 1x02.mkv", {"type": "file"}),
    ])
    assert client.list_files("/series/Serie/") == ["Serie 1x01.mkv", "Serie 1x02.mkv"]


def test_list_files_returns_empty_when_not_connected():
    client = FTPClient()
    assert client.list_files("/series/Serie/") == []


class _FakeTransferConn:
    """Simula la conexión de datos de un LIST -- expone el mismo
    contrato (context manager + .makefile()) que usa _retrlines_lenient."""
    def __init__(self, raw_bytes: bytes):
        self._raw = raw_bytes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def makefile(self, mode, encoding=None, errors="strict"):
        import io
        return io.TextIOWrapper(io.BytesIO(self._raw), encoding=encoding, errors=errors)


class _FakeFtpMixedEncoding:
    """Simula un servidor cuyo LIST devuelve una carpeta con archivos
    antiguos en latin-1 (antes de activar utf8_filesystem) mezclados con
    otros ya en UTF-8 -- el caso real reportado: activar UTF8 en el
    servidor expone bytes inválidos de nombres viejos al decodificar
    estricto en utf-8."""
    encoding = "utf-8"
    maxline = 8192

    def __init__(self, raw_bytes: bytes):
        self._raw = raw_bytes

    def voidcmd(self, cmd):
        return "200"

    def mlsd(self, path):
        raise ftplib.error_perm("550 MLSD no soportado")

    def sendcmd(self, cmd):
        return "200 OK"

    def transfercmd(self, cmd):
        return _FakeTransferConn(self._raw)

    def voidresp(self):
        return "226 OK"


def test_list_files_survives_invalid_utf8_bytes_from_old_filenames():
    # "La nena 1x02 Sin perd\xf3n.mkv" -- la "ó" grabada en latin-1 (0xF3),
    # que no es una secuencia UTF-8 válida -- junto con un archivo normal.
    raw = (
        "-rw-r--r-- 1 user user 100 Jan 01 00:00 La nena 1x01 Agua y sangre.mkv\r\n"
        "-rw-r--r-- 1 user user 100 Jan 01 00:00 La nena 1x02 Sin perd\xf3n.mkv\r\n"
    ).encode("latin-1")
    client = FTPClient()
    client.ftp = _FakeFtpMixedEncoding(raw)
    files = client.list_files("/series/La nena/")
    assert len(files) == 2
    assert "La nena 1x01 Agua y sangre.mkv" in files
    # La línea con el byte inválido no debe tirar todo el listado abajo --
    # sigue reconociéndose el "1x02" aunque el título salga con el
    # carácter de reemplazo en vez de la "ó" real.
    assert any(f.startswith("La nena 1x02") for f in files)


def test_upload_file_uses_local_filename_by_default(tmp_path):
    local = tmp_path / "Serie.1x01.Original.WEB-DL.mkv"
    local.write_bytes(b"contenido")
    client = _client_with_fake_ftp()

    ok, msg = client.upload_file(str(local), "/series/Serie/Temporada 01")

    assert ok is True
    assert client.ftp.stor_calls == ["STOR /series/Serie/Temporada 01/Serie.1x01.Original.WEB-DL.mkv"]


def test_upload_file_remote_filename_overrides_local_name(tmp_path):
    """'Renombrar en destino' sin renombrar en origen: sube con el nombre
    limpio aunque el archivo local siga con el nombre original."""
    local = tmp_path / "Serie.1x01.Original.WEB-DL.mkv"
    local.write_bytes(b"contenido")
    client = _client_with_fake_ftp()

    ok, msg = client.upload_file(
        str(local), "/series/Serie/Temporada 01",
        remote_filename="Serie 1x01 Titulo.mkv",
    )

    assert ok is True
    assert client.ftp.stor_calls == ["STOR /series/Serie/Temporada 01/Serie 1x01 Titulo.mkv"]


class _FakeFtpWithFinalSize(_FakeFtp):
    """Variante que, tras el STOR, informa un tamaño final concreto -- para
    probar la verificación de integridad post-subida."""
    def __init__(self, final_size):
        super().__init__()
        self._final_size = final_size
        self._stor_done = False

    def size(self, remote_file):
        if not self._stor_done:
            raise ftplib.error_perm("550 no existe")   # sin subida previa que reanudar
        return self._final_size

    def storbinary(self, cmd, fp, blocksize, callback, rest=None):
        super().storbinary(cmd, fp, blocksize, callback, rest)
        self._stor_done = True


def test_upload_file_succeeds_when_remote_size_matches(tmp_path):
    local = tmp_path / "Pelicula.mkv"
    local.write_bytes(b"0123456789")   # 10 bytes
    client = FTPClient()
    client.ftp = _FakeFtpWithFinalSize(final_size=10)

    ok, msg = client.upload_file(str(local), "/peliculas/Pelicula")

    assert ok is True


def test_upload_file_fails_when_remote_size_does_not_match():
    import tempfile
    from pathlib import Path as _Path
    tmp_path = _Path(tempfile.mkdtemp())
    local = tmp_path / "Pelicula.mkv"
    local.write_bytes(b"0123456789")   # 10 bytes locales
    client = FTPClient()
    client.ftp = _FakeFtpWithFinalSize(final_size=7)   # el servidor solo recibio 7

    ok, msg = client.upload_file(str(local), "/peliculas/Pelicula")

    assert ok is False
    assert "incompleta" in msg.lower()
    assert "7" in msg and "10" in msg


def test_upload_file_skips_integrity_check_when_size_unsupported(tmp_path):
    """Si el servidor no soporta SIZE (o el archivo no se puede consultar
    tras subir), se omite el chequeo en vez de fallar -- igual que
    get_free_space() se degrada a None sin romper nada."""
    local = tmp_path / "Pelicula.mkv"
    local.write_bytes(b"0123456789")
    client = _client_with_fake_ftp()   # _FakeFtp.size() siempre lanza error_perm

    ok, msg = client.upload_file(str(local), "/peliculas/Pelicula")

    assert ok is True


class _FakeFtpTree:
    """Simula un árbol de carpetas para list_files_recursive: {ruta: (dirs, files)}."""
    def __init__(self, tree):
        self.tree = tree

    def voidcmd(self, cmd):
        return "200 OK"

    def mlsd(self, path):
        dirs, files = self.tree.get(path, ([], []))
        entries = [(d, {"type": "dir"}) for d in dirs] + [(f, {"type": "file"}) for f in files]
        return iter(entries)


def test_list_files_recursive_walks_season_subfolders():
    client = FTPClient()
    client.ftp = _FakeFtpTree({
        "/series/Serie": (["Temporada 01", "Temporada 02"], []),
        "/series/Serie/Temporada 01": ([], ["Serie 1x01.mkv", "Serie 1x02.mkv"]),
        "/series/Serie/Temporada 02": ([], ["Serie 2x01.mkv"]),
    })
    files = client.list_files_recursive("/series/Serie")
    assert sorted(files) == ["Serie 1x01.mkv", "Serie 1x02.mkv", "Serie 2x01.mkv"]


def test_list_files_recursive_includes_files_at_top_level_too():
    client = FTPClient()
    client.ftp = _FakeFtpTree({
        "/series/Serie": ([], ["Serie 1x01.mkv"]),   # sin subcarpetas de temporada
    })
    assert client.list_files_recursive("/series/Serie") == ["Serie 1x01.mkv"]


def test_list_files_recursive_respects_max_depth():
    client = FTPClient()
    client.ftp = _FakeFtpTree({
        "/a": (["b"], ["top.mkv"]),
        "/a/b": (["c"], ["mid.mkv"]),
        "/a/b/c": ([], ["deep.mkv"]),
    })
    files = client.list_files_recursive("/a", max_depth=2)
    assert sorted(files) == ["mid.mkv", "top.mkv"]   # "deep.mkv" queda fuera (3er nivel)


def test_list_files_recursive_returns_empty_when_not_connected():
    client = FTPClient()
    assert client.list_files_recursive("/series/Serie") == []


class _FakeFtpTreeWithSizes:
    """Como _FakeFtpTree, pero con tamaños -- {ruta: (dirs, {archivo: tamaño})}."""
    def __init__(self, tree):
        self.tree = tree

    def voidcmd(self, cmd):
        return "200 OK"

    def mlsd(self, path):
        dirs, files = self.tree.get(path, ([], {}))
        entries = [(d, {"type": "dir"}) for d in dirs]
        entries += [(f, {"type": "file", "size": str(sz)}) for f, sz in files.items()]
        return iter(entries)


def test_list_files_with_sizes_returns_name_and_size_pairs():
    client = FTPClient()
    client.ftp = _FakeFtpTreeWithSizes({
        "/series/Serie/Temporada 01": ([], {"Serie 1x01.mkv": 1000, "Serie 1x02.mkv": 2000}),
    })
    result = client.list_files_with_sizes("/series/Serie/Temporada 01")
    assert sorted(result) == [("Serie 1x01.mkv", 1000), ("Serie 1x02.mkv", 2000)]


def test_get_folder_size_sums_all_files_recursively():
    client = FTPClient()
    client.ftp = _FakeFtpTreeWithSizes({
        "/series/Serie": (["Temporada 01", "Temporada 02"], {}),
        "/series/Serie/Temporada 01": ([], {"Serie 1x01.mkv": 1000, "Serie 1x02.mkv": 2000}),
        "/series/Serie/Temporada 02": ([], {"Serie 2x01.mkv": 500}),
    })
    assert client.get_folder_size("/series/Serie") == 3500


def test_get_folder_size_respects_max_depth():
    client = FTPClient()
    client.ftp = _FakeFtpTreeWithSizes({
        "/a": (["b"], {"top.mkv": 100}),
        "/a/b": (["c"], {"mid.mkv": 200}),
        "/a/b/c": ([], {"deep.mkv": 400}),
    })
    assert client.get_folder_size("/a", max_depth=2) == 300   # top + mid, sin deep


def test_get_folder_size_returns_zero_when_not_connected():
    client = FTPClient()
    assert client.get_folder_size("/series/Serie") == 0


# ── get_folder_tree / format_ftp_tree (árbol de archivos, Episodios que faltan) ──

class _FakeFtpTreeWithSizesNoDashR(_FakeFtpTreeWithSizes):
    """Como _FakeFtpTreeWithSizes, pero rechaza "LIST -R" (servidor sin
    soporte) -- para forzar la caída de get_folder_tree() a la
    reconstrucción manual carpeta por carpeta (_walk_folder_tree)."""
    def sendcmd(self, cmd):
        return "200 OK"

    def transfercmd(self, cmd):
        raise ftplib.error_perm("500 LIST -R no soportado")


def test_get_folder_tree_uses_list_tree_recursive_when_supported():
    raw = (
        "/datos2/series/Serie A:\r\n"
        "-rw-r--r-- 1 user user 1000 Jan 01 00:00 ep1.mkv\r\n"
        "drwxr-xr-x 2 user user 4096 Jan 01 00:00 Extras\r\n"
        "\r\n"
        "/datos2/series/Serie A/Extras:\r\n"
        "-rw-r--r-- 1 user user 500 Jan 01 00:00 trailer.mkv\r\n"
    ).encode("utf-8")
    client = FTPClient()
    client.ftp = _FakeFtpMixedEncoding(raw)
    tree = client.get_folder_tree("/datos2/series/Serie A")
    assert tree == {
        "/datos2/series/Serie A": [("ep1.mkv", 1000)],
        "/datos2/series/Serie A/Extras": [("trailer.mkv", 500)],
    }


def test_get_folder_tree_falls_back_to_manual_walk_without_dash_r():
    client = FTPClient()
    client.ftp = _FakeFtpTreeWithSizesNoDashR({
        "/series/Serie": (["Temporada 01"], {}),
        "/series/Serie/Temporada 01": ([], {"Serie 1x01.mkv": 1000, "Serie 1x02.mkv": 2000}),
    })
    tree = client.get_folder_tree("/series/Serie")
    assert tree == {
        "/series/Serie": [],
        "/series/Serie/Temporada 01": [("Serie 1x01.mkv", 1000), ("Serie 1x02.mkv", 2000)],
    }


def test_get_folder_tree_returns_none_when_not_connected():
    client = FTPClient()
    assert client.get_folder_tree("/series/Serie") is None


def test_format_ftp_tree_indents_subfolders_and_shows_sizes():
    tree = {
        "/series/Serie": [],
        "/series/Serie/Temporada 01": [("Serie 1x01.mkv", 1024 * 1024 * 200)],
        "/series/Serie/Temporada 01/Extras": [("opening.mkv", 1024 * 100)],
    }
    text = format_ftp_tree("/series/Serie", tree)
    lines = text.split("\n")
    assert lines == [
        "📁 Temporada 01/",
        "  📄 Serie 1x01.mkv  (200.0 MB)",
        "  📁 Extras/",
        "    📄 opening.mkv  (100.0 KB)",
    ]


def test_format_ftp_tree_shows_loose_files_directly_at_root_unindented():
    tree = {
        "/series/Serie": [("suelto.mkv", 100)],
    }
    text = format_ftp_tree("/series/Serie", tree)
    assert text == "📄 suelto.mkv  (100 B)"


def test_format_ftp_tree_empty_tree_is_empty_string():
    assert format_ftp_tree("/series/Serie", {}) == ""


def test_list_files_with_sizes_falls_back_to_list_parsing():
    client = FTPClient()
    raw = (
        "-rw-r--r-- 1 user user 12345 Jan 01 00:00 Serie 1x01.mkv\r\n"
        "drwxr-xr-x 2 user user 4096 Jan 01 00:00 Extras\r\n"
    ).encode("utf-8")
    client.ftp = _FakeFtpMixedEncoding(raw)
    result = client.list_files_with_sizes("/series/Serie")
    assert result == [("Serie 1x01.mkv", 12345)]


def test_list_tree_recursive_parses_multi_section_output():
    raw = (
        "/datos2/series:\r\n"
        "drwxr-xr-x 2 user user 4096 Jan 01 00:00 Serie A\r\n"
        "drwxr-xr-x 2 user user 4096 Jan 01 00:00 Serie B\r\n"
        "\r\n"
        "/datos2/series/Serie A:\r\n"
        "-rw-r--r-- 1 user user 1000 Jan 01 00:00 ep1.mkv\r\n"
        "drwxr-xr-x 2 user user 4096 Jan 01 00:00 Extras\r\n"
        "\r\n"
        "/datos2/series/Serie A/Extras:\r\n"
        "-rw-r--r-- 1 user user 500 Jan 01 00:00 trailer.mkv\r\n"
        "\r\n"
        "/datos2/series/Serie B:\r\n"
        "-rw-r--r-- 1 user user 2000 Jan 01 00:00 ep1.mkv\r\n"
    ).encode("utf-8")
    client = FTPClient()
    client.ftp = _FakeFtpMixedEncoding(raw)
    tree = client.list_tree_recursive("/datos2/series")
    assert tree is not None
    assert tree["/datos2/series/Serie A"] == [("ep1.mkv", 1000)]
    assert tree["/datos2/series/Serie A/Extras"] == [("trailer.mkv", 500)]
    assert tree["/datos2/series/Serie B"] == [("ep1.mkv", 2000)]


def test_list_tree_recursive_returns_none_when_server_ignores_dash_r():
    # El servidor no soporta "-R" y devuelve un listado plano normal, sin
    # ninguna línea de cabecera "ruta:" -- no hay forma de distinguir esto
    # de un árbol real, así que se trata como "no soportado".
    raw = "-rw-r--r-- 1 user user 1000 Jan 01 00:00 archivo.mkv\r\n".encode("utf-8")
    client = FTPClient()
    client.ftp = _FakeFtpMixedEncoding(raw)
    assert client.list_tree_recursive("/datos2/series") is None


def test_list_tree_recursive_returns_none_when_not_connected():
    client = FTPClient()
    assert client.list_tree_recursive("/datos2/series") is None


def test_sizes_by_top_level_folder_sums_all_subtree_files():
    tree = {
        "/datos2/series/Serie A": [("ep1.mkv", 1000)],
        "/datos2/series/Serie A/Extras": [("trailer.mkv", 500)],
        "/datos2/series/Serie B": [("ep1.mkv", 2000)],
    }
    totals = sizes_by_top_level_folder(tree, "/datos2/series")
    assert totals == {"Serie A": 1500, "Serie B": 2000}


def test_sizes_by_top_level_folder_ignores_files_directly_at_root():
    tree = {
        "/datos2/series": [("archivo_suelto.txt", 100)],
        "/datos2/series/Serie A": [("ep1.mkv", 1000)],
    }
    totals = sizes_by_top_level_folder(tree, "/datos2/series")
    assert totals == {"Serie A": 1000}


def test_files_by_top_level_folder_collects_names_across_subfolders():
    tree = {
        "/datos2/series/Serie A/Temporada 01": [("Serie A 1x01.mkv", 1000), ("Serie A 1x02.mkv", 1000)],
        "/datos2/series/Serie A/Temporada 02": [("Serie A 2x01.mkv", 1000)],
        "/datos2/series/Serie B": [("Serie B 1x01.mkv", 1000)],
    }
    result = files_by_top_level_folder(tree, "/datos2/series")
    assert sorted(result["Serie A"]) == ["Serie A 1x01.mkv", "Serie A 1x02.mkv", "Serie A 2x01.mkv"]
    assert result["Serie B"] == ["Serie B 1x01.mkv"]


class _FakeFtpDeletable:
    """Simula un árbol de carpetas borrable -- registra delete()/rmd() y
    permite comprobar que se borra de más profundo a menos."""
    def __init__(self, tree):
        self.tree = {k: (list(dirs), list(files)) for k, (dirs, files) in tree.items()}
        self.deleted_files = []
        self.deleted_dirs = []
        self.fail_delete_for = None   # ruta exacta de archivo que debe fallar, o None

    def voidcmd(self, cmd):
        return "200 OK"

    def mlsd(self, path):
        dirs, files = self.tree.get(path, ([], []))
        entries = [(d, {"type": "dir"}) for d in dirs] + [(f, {"type": "file"}) for f in files]
        return iter(entries)

    def delete(self, path):
        if self.fail_delete_for and path == self.fail_delete_for:
            raise ftplib.error_perm("550 no se pudo borrar")
        self.deleted_files.append(path)

    def rmd(self, path):
        self.deleted_dirs.append(path)


def test_delete_folder_recursive_deletes_files_and_subfolders_then_itself():
    client = FTPClient()
    client.ftp = _FakeFtpDeletable({
        "/peliculas/Pelicula X": (["Extras"], ["Pelicula X.mkv"]),
        "/peliculas/Pelicula X/Extras": ([], ["trailer.mkv"]),
    })
    ok, msg = client.delete_folder_recursive("/peliculas/Pelicula X")
    assert ok is True
    assert "/peliculas/Pelicula X/Extras/trailer.mkv" in client.ftp.deleted_files
    assert "/peliculas/Pelicula X/Pelicula X.mkv" in client.ftp.deleted_files
    # La subcarpeta se borra ANTES que la carpeta padre (de mas profundo a menos)
    assert client.ftp.deleted_dirs == ["/peliculas/Pelicula X/Extras", "/peliculas/Pelicula X"]


def test_delete_folder_recursive_stops_on_first_failure():
    client = FTPClient()
    client.ftp = _FakeFtpDeletable({
        "/peliculas/Pelicula X": ([], ["a.mkv", "b.mkv"]),
    })
    client.ftp.fail_delete_for = "/peliculas/Pelicula X/a.mkv"
    ok, msg = client.delete_folder_recursive("/peliculas/Pelicula X")
    assert ok is False
    # No debe haber intentado borrar la carpeta si un archivo fallo
    assert client.ftp.deleted_dirs == []


def test_delete_folder_recursive_returns_false_when_not_connected():
    client = FTPClient()
    ok, msg = client.delete_folder_recursive("/peliculas/Pelicula X")
    assert ok is False


class _FakeFtpBlob:
    """Doble para upload_bytes/download_bytes/rename_file -- registra STOR
    (con los bytes recibidos) y RETR, y simula RNFR/RNTO vía rename()."""
    def __init__(self, files=None):
        self.files = dict(files or {})   # {remote_path: bytes}
        self.stor_calls = []
        self.rename_calls = []

    def voidcmd(self, cmd):
        return "200 OK"

    def cwd(self, path):
        return None   # la carpeta "ya existe" siempre

    def mkd(self, path):
        raise ftplib.error_perm("550 no debería llamarse")

    def storbinary(self, cmd, fp):
        self.stor_calls.append(cmd)
        remote_path = cmd.split(" ", 1)[1]
        self.files[remote_path] = fp.read()

    def retrbinary(self, cmd, write_cb):
        remote_path = cmd.split(" ", 1)[1]
        if remote_path not in self.files:
            raise ftplib.error_perm("550 no existe")
        write_cb(self.files[remote_path])

    def rename(self, remote_from, remote_to):
        if remote_from not in self.files:
            raise ftplib.error_perm("550 no existe")
        self.rename_calls.append((remote_from, remote_to))
        self.files[remote_to] = self.files.pop(remote_from)


def test_upload_bytes_stores_data_at_remote_path():
    client = FTPClient()
    client.ftp = _FakeFtpBlob()

    ok, msg = client.upload_bytes(b'{"favoritos": []}', "/datos/favoritos.json")

    assert ok is True
    assert client.ftp.files["/datos/favoritos.json"] == b'{"favoritos": []}'


def test_upload_bytes_fails_without_connection():
    client = FTPClient()
    ok, msg = client.upload_bytes(b"datos", "/datos/favoritos.json")
    assert ok is False


def test_download_bytes_returns_stored_data():
    client = FTPClient()
    client.ftp = _FakeFtpBlob(files={"/datos/favoritos.json": b'{"a": 1}'})

    data = client.download_bytes("/datos/favoritos.json")

    assert data == b'{"a": 1}'


def test_download_bytes_returns_none_when_missing():
    client = FTPClient()
    client.ftp = _FakeFtpBlob()
    assert client.download_bytes("/datos/no_existe.json") is None


def test_download_bytes_returns_none_without_connection():
    client = FTPClient()
    assert client.download_bytes("/datos/favoritos.json") is None


def test_rename_file_moves_within_same_disk():
    client = FTPClient()
    client.ftp = _FakeFtpBlob(files={"/peliculas/A/Pelicula.mkv": b"contenido"})

    ok, msg = client.rename_file("/peliculas/A/Pelicula.mkv", "/peliculas/B/Pelicula.mkv")

    assert ok is True
    assert client.ftp.rename_calls == [("/peliculas/A/Pelicula.mkv", "/peliculas/B/Pelicula.mkv")]
    assert "/peliculas/B/Pelicula.mkv" in client.ftp.files


def test_rename_file_fails_without_connection():
    client = FTPClient()
    ok, msg = client.rename_file("/a/Pelicula.mkv", "/b/Pelicula.mkv")
    assert ok is False


def test_download_file_writes_local_file(tmp_path):
    client = FTPClient()
    client.ftp = _FakeFtpBlob(files={"/peliculas/Pelicula.mkv": b"0123456789"})
    local_path = tmp_path / "descargas" / "Pelicula.mkv"

    ok, msg = client.download_file("/peliculas/Pelicula.mkv", str(local_path))

    assert ok is True
    assert local_path.read_bytes() == b"0123456789"


def test_download_file_reports_progress():
    client = FTPClient()
    client.ftp = _FakeFtpBlob(files={"/peliculas/Pelicula.mkv": b"0123456789"})
    progress = []

    ok, msg = client.download_file(
        "/peliculas/Pelicula.mkv", str(_tmp_local_path()),
        progress_cb=lambda n: progress.append(n),
    )

    assert ok is True
    assert progress == [10]


def _tmp_local_path():
    import tempfile
    from pathlib import Path as _Path
    return _Path(tempfile.mkdtemp()) / "Pelicula.mkv"


def test_download_file_fails_without_connection(tmp_path):
    client = FTPClient()
    ok, msg = client.download_file("/peliculas/Pelicula.mkv", str(tmp_path / "Pelicula.mkv"))
    assert ok is False


class _FakeSocket:
    def __init__(self, timeout):
        self._timeout = timeout

    def gettimeout(self):
        return self._timeout

    def settimeout(self, value):
        self._timeout = value


class _FakeFtpWithSocket:
    def __init__(self, timeout=15):
        self.sock = _FakeSocket(timeout)


def test_widened_timeout_widens_and_restores():
    client = FTPClient()
    client.ftp = _FakeFtpWithSocket(timeout=15)

    with client.widened_timeout(300):
        assert client.ftp.sock.gettimeout() == 300

    assert client.ftp.sock.gettimeout() == 15


def test_widened_timeout_restores_even_if_body_raises():
    client = FTPClient()
    client.ftp = _FakeFtpWithSocket(timeout=15)

    try:
        with client.widened_timeout(300):
            assert client.ftp.sock.gettimeout() == 300
            raise ValueError("fallo dentro del bloque")
    except ValueError:
        pass

    assert client.ftp.sock.gettimeout() == 15


def test_widened_timeout_restores_to_30_when_original_was_none():
    client = FTPClient()
    client.ftp = _FakeFtpWithSocket(timeout=None)

    with client.widened_timeout(300):
        assert client.ftp.sock.gettimeout() == 300

    assert client.ftp.sock.gettimeout() == 30


def test_widened_timeout_is_a_no_op_without_a_connection():
    client = FTPClient()   # client.ftp es None -- nunca se conectó
    with client.widened_timeout(300):
        pass   # no debe lanzar
