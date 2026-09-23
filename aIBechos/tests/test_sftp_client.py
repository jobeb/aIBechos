"""Cliente SFTP (core/sftp_client.py) y la fábrica que elige protocolo.

No se abre ninguna conexión de verdad: se sustituye el objeto sftp de paramiko
por un doble que se comporta como un sistema de archivos en memoria. Lo que se
comprueba es justo lo que puede romperse al cambiar de protocolo -- que los
listados distinguen carpetas de archivos, que la subida respeta el progreso y
la reanudación, y que los métodos heredados de FTPClient (recorrer el árbol,
sumar tamaños, borrar una carpeta entera) siguen funcionando sobre SFTP.
"""

import stat as stat_mod

import pytest

from core.ftp_client import FTPClient
from core.sftp_client import SFTPClient
from core.transfer import default_port, make_client


class _Attr:
    """Lo que devuelve paramiko en listdir_attr()."""
    def __init__(self, filename, size=0, es_dir=False):
        self.filename = filename
        self.st_size = size
        self.st_mode = (stat_mod.S_IFDIR | 0o755) if es_dir else (stat_mod.S_IFREG | 0o644)


class _FicheroRemoto:
    def __init__(self, almacen, ruta, modo):
        self._almacen = almacen
        self._ruta = ruta
        self._pos = 0
        if "w" in modo:                 # solo escribir desde cero trunca
            almacen[ruta] = b""
        elif "r" in modo and ruta not in almacen:
            raise IOError(f"no existe: {ruta}")

    def set_pipelined(self, _v):
        pass

    def seek(self, pos):
        self._pos = pos

    def write(self, datos):
        self._almacen[self._ruta] = self._almacen.get(self._ruta, b"") + datos

    def read(self, n=None):
        datos = self._almacen.get(self._ruta, b"")[self._pos:]
        if n is not None:
            datos = datos[:n]
        self._pos += len(datos)
        return datos

    def prefetch(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _SftpFalso:
    """Sistema de archivos en memoria con la interfaz de paramiko.SFTPClient."""
    def __init__(self):
        self.ficheros = {}          # ruta -> bytes
        self.carpetas = {"/"}
        self.borrados = []

    # --- lo que usa el cliente ---
    def listdir_attr(self, path):
        path = path.rstrip("/") or "/"
        if path not in self.carpetas:
            raise IOError("no existe")
        prefijo = path if path.endswith("/") else path + "/"
        salida = []
        for c in self.carpetas:
            if c != path and c.startswith(prefijo) and "/" not in c[len(prefijo):]:
                salida.append(_Attr(c[len(prefijo):], es_dir=True))
        for f, datos in self.ficheros.items():
            if f.startswith(prefijo) and "/" not in f[len(prefijo):]:
                salida.append(_Attr(f[len(prefijo):], size=len(datos)))
        return salida

    def listdir(self, path):
        return [a.filename for a in self.listdir_attr(path)]

    def stat(self, path):
        path = path.rstrip("/") or "/"
        if path in self.carpetas:
            return _Attr(path, es_dir=True)
        if path in self.ficheros:
            return _Attr(path, size=len(self.ficheros[path]))
        raise IOError(f"no existe: {path}")

    def mkdir(self, path):
        self.carpetas.add(path.rstrip("/") or "/")

    def rmdir(self, path):
        self.carpetas.discard(path.rstrip("/"))
        self.borrados.append(path)

    def remove(self, path):
        self.ficheros.pop(path, None)
        self.borrados.append(path)

    def rename(self, origen, destino):
        self.ficheros[destino] = self.ficheros.pop(origen)

    def open(self, ruta, modo="rb"):
        return _FicheroRemoto(self.ficheros, ruta, modo)

    def get_channel(self):
        class _Canal:
            def settimeout(self, _s): pass
            def gettimeout(self): return 30
        return _Canal()

    def statvfs(self, _path):
        raise IOError("no soportado")


@pytest.fixture
def cliente():
    c = SFTPClient()
    c.sftp = _SftpFalso()
    c.sftp.carpetas.update({"/datos", "/datos/series", "/datos/series/Bleach"})
    c.sftp.ficheros.update({
        "/datos/series/Bleach/Bleach 1x01.mkv": b"a" * 100,
        "/datos/series/Bleach/Bleach 1x02.mkv": b"b" * 250,
        "/datos/series/leeme.txt": b"x" * 10,
    })
    return c


# --------------------------------------------------------------- fábrica --

def test_la_fabrica_devuelve_el_cliente_de_cada_protocolo():
    assert type(make_client("ftp")) is FTPClient
    assert type(make_client("sftp")) is SFTPClient
    assert type(make_client("SFTP")) is SFTPClient      # sin distinguir mayúsculas


def test_sin_protocolo_o_con_uno_desconocido_se_queda_en_ftp():
    assert type(make_client("")) is FTPClient
    assert type(make_client(None)) is FTPClient
    assert type(make_client("scp")) is FTPClient


def test_cada_protocolo_propone_su_puerto():
    assert default_port("ftp") == 21
    assert default_port("sftp") == 22


def test_el_cliente_sftp_es_intercambiable_con_el_de_ftp():
    """Todo lo público de FTPClient tiene que existir también en SFTPClient:
    hay medio centenar de sitios que piden una conexión sin saber cuál les
    tocará."""
    faltan = [n for n in dir(FTPClient)
              if not n.startswith("_") and not hasattr(SFTPClient, n)]
    assert faltan == []


# -------------------------------------------------------------- listados --

def test_separa_carpetas_de_archivos(cliente):
    assert cliente.list_dirs("/datos/series") == ["Bleach"]
    assert cliente.list_files("/datos/series") == ["leeme.txt"]


def test_lista_los_archivos_con_su_tamaño(cliente):
    assert sorted(cliente.list_files_with_sizes("/datos/series/Bleach")) == [
        ("Bleach 1x01.mkv", 100), ("Bleach 1x02.mkv", 250)]


def test_una_carpeta_que_no_existe_no_revienta(cliente):
    assert cliente.list_dirs("/no/existe") == []
    assert cliente.list_files_with_sizes("/no/existe") == []


def test_no_pide_el_arbol_de_una_vez(cliente):
    """SFTP no tiene equivalente a "LIST -R": debe devolver None para que la
    clase base recorra carpeta por carpeta."""
    assert cliente.list_tree_recursive("/datos") is None


# ------------------------------------- lógica heredada, ahora sobre SFTP --

def test_suma_el_tamaño_de_todo_el_arbol(cliente):
    # 100 + 250 de Bleach, + 10 del archivo suelto de /datos/series
    assert cliente.get_folder_size("/datos/series") == 360


def test_recorre_las_subcarpetas_buscando_archivos(cliente):
    assert sorted(cliente.list_files_recursive("/datos/series")) == [
        "Bleach 1x01.mkv", "Bleach 1x02.mkv", "leeme.txt"]


def test_borra_una_carpeta_entera_de_dentro_afuera(cliente):
    ok, _msg = cliente.delete_folder_recursive("/datos/series")
    assert ok
    # La carpeta de dentro se borra antes que la de fuera: al revés, el
    # servidor rechazaría borrar una carpeta que no está vacía.
    assert cliente.sftp.borrados.index("/datos/series/Bleach") < \
           cliente.sftp.borrados.index("/datos/series")
    assert cliente.sftp.ficheros == {}


def test_construye_la_ruta_remota_igual_que_por_ftp(cliente):
    ruta = cliente.build_remote_path("/datos/{tipo}/{serie}/Temporada {temporada:02d}/",
                                     "Bleach", season=2, media_type="tv")
    assert ruta == "/datos/Series/Bleach/Temporada 02/"


# ------------------------------------------------------- crear y subir ----

def test_crea_las_carpetas_que_falten(cliente):
    ok, _ = cliente.ensure_remote_dir("/datos/series/Nueva/Temporada 01")
    assert ok
    assert "/datos/series/Nueva/Temporada 01" in cliente.sftp.carpetas


def test_no_falla_si_otra_subida_creo_la_carpeta_a_la_vez(cliente):
    """Con varias subidas en paralelo, dos pueden intentar crear la misma
    carpeta: que exista ya no es un error."""
    def _mkdir_que_choca(path):
        cliente.sftp.carpetas.add(path.rstrip("/"))
        raise IOError("ya existe")
    cliente.sftp.mkdir = _mkdir_que_choca
    ok, _ = cliente.ensure_remote_dir("/datos/series/Otra")
    assert ok


def test_sube_un_archivo_entero(cliente, tmp_path):
    local = tmp_path / "Bleach 1x03.mkv"
    local.write_bytes(b"z" * 5000)
    ok, destino = cliente.upload_file(str(local), "/datos/series/Bleach")
    assert ok, destino
    assert cliente.sftp.ficheros["/datos/series/Bleach/Bleach 1x03.mkv"] == b"z" * 5000


def test_va_avisando_bloque_a_bloque_mientras_sube(cliente, tmp_path):
    """El aviso por bloque es lo que alimenta el límite de velocidad, la
    barra de progreso y la cancelación (todo eso vive en la clase base, y
    solo funciona si el transporte SFTP lo va llamando).

    Se comprueba sobre _store_stream y no sobre upload_file porque el
    progreso que llega a la interfaz está limitado a uno cada 0,4 s: un
    archivo pequeño sube de golpe y no daría ninguno, igual que por FTP."""
    local = tmp_path / "grande.mkv"
    local.write_bytes(b"z" * 2500)
    bloques = []
    with open(local, "rb") as f:
        cliente._store_stream(f, "/datos/series/Bleach/grande.mkv", 1000,
                              lambda datos: bloques.append(len(datos)))
    assert bloques == [1000, 1000, 500]
    assert cliente.sftp.ficheros["/datos/series/Bleach/grande.mkv"] == b"z" * 2500


def test_reanuda_una_subida_a_medias_en_vez_de_repetirla(cliente, tmp_path):
    local = tmp_path / "corte.mkv"
    local.write_bytes(b"0123456789")
    cliente.sftp.ficheros["/datos/series/Bleach/corte.mkv"] = b"01234"   # mitad subida
    ok, _ = cliente.upload_file(str(local), "/datos/series/Bleach", try_resume=True)
    assert ok
    assert cliente.sftp.ficheros["/datos/series/Bleach/corte.mkv"] == b"0123456789"


def test_no_resube_lo_que_ya_esta_completo(cliente, tmp_path):
    local = tmp_path / "ya.mkv"
    local.write_bytes(b"0123456789")
    cliente.sftp.ficheros["/datos/series/Bleach/ya.mkv"] = b"0123456789"
    ok, _ = cliente.upload_file(str(local), "/datos/series/Bleach", try_resume=True)
    assert ok


def test_avisa_si_el_archivo_llega_incompleto(cliente, tmp_path, monkeypatch):
    """La verificación del tamaño final es de la clase base, pero tiene que
    seguir cazando un corte silencioso también por SFTP."""
    local = tmp_path / "truncado.mkv"
    local.write_bytes(b"z" * 900)
    monkeypatch.setattr(cliente, "get_remote_size", lambda ruta: 400)
    ok, msg = cliente.upload_file(str(local), "/datos/series/Bleach")
    assert not ok
    assert "incompleta" in msg.lower()


def test_manda_y_trae_bytes_sueltos(cliente):
    ok, _ = cliente.upload_bytes(b"hola", "/datos/compartido/datos.json")
    assert ok
    assert cliente.download_bytes("/datos/compartido/datos.json") == b"hola"


def test_traer_un_archivo_que_no_existe_devuelve_nada(cliente):
    assert cliente.download_bytes("/datos/no_existe.json") is None


def test_descarga_a_disco(cliente, tmp_path):
    destino = tmp_path / "bajado.mkv"
    ok, _ = cliente.download_file("/datos/series/Bleach/Bleach 1x01.mkv", str(destino))
    assert ok
    assert destino.read_bytes() == b"a" * 100


def test_renombra_en_el_servidor(cliente):
    ok, _ = cliente.rename_file("/datos/series/Bleach/Bleach 1x01.mkv",
                                "/datos/series/Bleach/Bleach S01E01.mkv")
    assert ok
    assert "/datos/series/Bleach/Bleach S01E01.mkv" in cliente.sftp.ficheros


# ------------------------------------------------------------- conexión ---

def test_sin_conexion_no_revienta_nada():
    c = SFTPClient()          # nunca se llamó a connect()
    assert c.is_connected() is False
    assert c.list_dirs("/datos") == []
    assert c.get_remote_size("/x") is None
    assert c.file_exists("/x") is False
    ok, msg = c.ensure_remote_dir("/x")
    assert not ok and "No conectado" in msg


def test_el_puerto_por_defecto_es_el_de_ssh():
    assert SFTPClient().port == 22


def test_explica_los_fallos_en_vez_de_dejarlos_en_blanco():
    """"Error sin explicación" ya costó un disgusto con las subidas: los
    fallos de paramiko llegan con el texto vacío más de la cuenta."""
    paramiko = pytest.importorskip("paramiko")
    assert "contraseña" in SFTPClient._explain(paramiko.AuthenticationException())
    assert SFTPClient._explain(paramiko.SSHException("")) != ""
    assert SFTPClient._explain(Exception()) == "Exception"


# ------------------------------------------------------- espacio libre ----
# Por SFTP el espacio libre es un dato de verdad, no como por FTP (donde hay
# que ir probando comandos no estándar y vsftpd directamente no contesta).

class _RespuestaStatvfs:
    """Los once enteros de 64 bits que define statvfs@openssh.com."""
    def __init__(self, frsize, bavail, blocks=0):
        self._campos = [4096, frsize, blocks, 0, bavail] + [0] * 6
        self._i = 0

    def get_int64(self):
        valor = self._campos[self._i]
        self._i += 1
        return valor


def _con_statvfs(cliente, respuesta=None, revienta=False):
    from paramiko.sftp import CMD_EXTENDED_REPLY

    def _request(_cmd, nombre, ruta):
        assert nombre == "statvfs@openssh.com"
        assert ruta == b"/datos2"          # la ruta llega ya en bytes
        if revienta:
            raise IOError("extensión no soportada")
        return CMD_EXTENDED_REPLY, respuesta

    cliente.sftp._request = _request
    cliente.sftp._adjust_cwd = lambda p: p.encode() if isinstance(p, str) else p


def test_pide_el_espacio_libre_con_la_extension_de_openssh(cliente):
    pytest.importorskip("paramiko")
    _con_statvfs(cliente, _RespuestaStatvfs(frsize=4096, bavail=1000))
    assert cliente.get_free_space("/datos2") == 4096 * 1000


def test_cuenta_lo_libre_para_el_usuario_no_lo_reservado_para_root(cliente):
    """f_bavail (el quinto campo), no f_bfree (el cuarto): el sistema reserva
    parte del disco para root, y contarla daría una cifra que no se puede
    usar de verdad."""
    pytest.importorskip("paramiko")
    resp = _RespuestaStatvfs(frsize=1024, bavail=50)
    resp._campos[3] = 999999          # f_bfree, mucho mayor: no debe usarse
    _con_statvfs(cliente, resp)
    assert cliente.get_free_space("/datos2") == 1024 * 50


def test_si_no_hay_extension_lo_intenta_con_df(cliente):
    pytest.importorskip("paramiko")
    _con_statvfs(cliente, revienta=True)

    class _Salida:
        def __init__(self, texto): self._t = texto.encode()
        def read(self): return self._t

    class _Ssh:
        def exec_command(self, _cmd, timeout=None):
            return None, _Salida("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                                 "/dev/sda1 100000 40000 60000 40% /datos2\n"), _Salida("")

    cliente._ssh = _Ssh()
    assert cliente.get_free_space("/datos2") == 60000 * 1024


def test_un_servidor_solo_sftp_no_inventa_un_numero(cliente):
    """Caso real del servidor de referencia: no da shell, y a cualquier orden
    contesta "This service allows sftp connections only". Sin comprobar que la
    salida es de verdad un df, esa frase se colaba como si fuera un dato."""
    pytest.importorskip("paramiko")
    _con_statvfs(cliente, revienta=True)

    class _Salida:
        def __init__(self, texto): self._t = texto.encode()
        def read(self): return self._t

    class _Ssh:
        def exec_command(self, _cmd, timeout=None):
            return None, _Salida("This service allows sftp connections only.\n"), _Salida("")

    cliente._ssh = _Ssh()
    assert cliente.get_free_space("/datos2") is None


def test_sin_extension_y_sin_shell_devuelve_nada(cliente):
    pytest.importorskip("paramiko")
    _con_statvfs(cliente, revienta=True)
    cliente._ssh = None
    assert cliente.get_free_space("/datos2") is None


# ----------------------------------------------- nombres que no son UTF-8 --

def test_un_nombre_en_latin1_no_tira_abajo_la_carpeta_entera():
    """Caso real: una sola carpeta llamada "El Sueño Producciones" con la eñe
    en latin-1 (byte 0xf1) hacía que paramiko lanzara UnicodeDecodeError y se
    perdieran las 97 entradas de la carpeta. La aplicación la veía vacía y
    actuaba en consecuencia: no encontraba la carpeta de la serie, no
    detectaba duplicados y no veía los episodios ya subidos, todo ello sin un
    solo error a la vista."""
    pytest.importorskip("paramiko")
    import paramiko.message as msg
    from core.sftp_client import _permitir_nombres_no_utf8

    _permitir_nombres_no_utf8()
    nombre = msg.u(b"El Sue\xf1o Producciones")     # antes: UnicodeDecodeError
    assert nombre.startswith("El Sue") and nombre.endswith(" Producciones")


def test_los_nombres_normales_se_leen_igual_que_siempre():
    pytest.importorskip("paramiko")
    import paramiko.message as msg
    from core.sftp_client import _permitir_nombres_no_utf8

    _permitir_nombres_no_utf8()
    assert msg.u("Películas".encode("utf-8")) == "Películas"
    assert msg.u("ya es texto") == "ya es texto"


def test_el_parche_no_se_encadena_sobre_si_mismo():
    """Se llama en cada connect(), y hay medio centenar de conexiones."""
    pytest.importorskip("paramiko")
    import paramiko.message as msg
    from core.sftp_client import _permitir_nombres_no_utf8

    _permitir_nombres_no_utf8()
    primera = msg.u
    for _ in range(5):
        _permitir_nombres_no_utf8()
    assert msg.u is primera


# ----------------------------------- repartir un archivo entre conexiones --
# El servidor limita CADA conexión por separado (~2 MB/s medidos, y le pasa
# igual al putfo() de paramiko), así que un archivo solo nunca alcanza el
# límite configurado. Repartirlo sí: 2 -> 3,9 MB/s, 4 -> 6,5, 6 -> 9,2.

def _grande(tmp_path, cliente):
    local = tmp_path / "peli.mkv"
    local.write_bytes(b"z" * (cliente.MULTIFLOW_MIN_BYTES + 1024))
    return local


class _RemotoFalso:
    def set_pipelined(self, v): pass
    def seek(self, p): pass
    def write(self, d): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_un_archivo_pequeno_no_abre_conexiones_de_mas(cliente, tmp_path, monkeypatch):
    """Abrir una conexión son un saludo TCP y una negociación SSH enteros: para
    un archivo pequeño cuesta más de lo que se gana."""
    local = tmp_path / "pequeno.mkv"
    local.write_bytes(b"z" * 1000)
    llamado = []
    monkeypatch.setattr(cliente, "_store_stream_multi",
                        lambda *a, **k: llamado.append(True))
    ok, _ = cliente.upload_file(str(local), "/datos/series/Bleach", streams=4)
    assert ok
    assert llamado == []


def test_un_archivo_grande_se_reparte(cliente, tmp_path, monkeypatch):
    local = _grande(tmp_path, cliente)
    repartos = []
    monkeypatch.setattr(cliente, "_store_stream_multi",
                        lambda *a, **k: repartos.append(a[-1]))
    ok, _ = cliente.upload_file(str(local), "/datos/series/Bleach", streams=4)
    assert ok
    assert repartos == [4]


def test_sin_reparto_pedido_va_por_una_sola(cliente, tmp_path, monkeypatch):
    local = _grande(tmp_path, cliente)
    llamado = []
    monkeypatch.setattr(cliente, "_store_stream_multi",
                        lambda *a, **k: llamado.append(True))
    ok, _ = cliente.upload_file(str(local), "/datos/series/Bleach")   # streams=1
    assert ok
    assert llamado == []


def test_el_trabajo_se_reparte_entre_varias_conexiones(cliente, tmp_path, monkeypatch):
    """Los trozos van en una cola común, no en tramos fijos: con tramos fijos
    la conexión más rápida terminaba antes y se quedaba parada, y el archivo
    acababa subiendo por una sola (medido: de 8 MB/s a 3,7 al final)."""
    import threading
    import core.sftp_client as mod

    total = 40 * 1024 * 1024
    local = tmp_path / "peli.mkv"
    local.write_bytes(b"z" * total)
    por_hilo = {}

    class _Falso:
        def connect(self, *a, **k): return True, "ok"
        def disconnect(self): pass
        sftp = type("S", (), {"open": lambda s, r, m: _RemotoFalso()})()

    monkeypatch.setattr(mod, "SFTPClient", _Falso)

    def contar(datos):
        n = threading.current_thread().name
        por_hilo[n] = por_hilo.get(n, 0) + len(datos)

    cliente._store_stream_multi(str(local), "/x/peli.mkv", 4 * 1024 * 1024,
                                contar, 0, total, 4)
    assert sum(por_hilo.values()) == total      # se subió entero
    assert len(por_hilo) > 1                    # y entre varios


def test_si_se_cae_una_conexion_el_archivo_se_sube_igual(cliente, tmp_path, monkeypatch):
    """Antes, que se cayera UNA conexión tiraba el archivo entero y había que
    resubirlo desde cero -- bastante más probable usando varias."""
    import core.sftp_client as mod

    total = 40 * 1024 * 1024
    local = tmp_path / "peli.mkv"
    local.write_bytes(b"z" * total)
    escrito = []
    caidas = [2]        # las dos primeras conexiones se caen al conectar

    class _Remoto(_RemotoFalso):
        def write(self, d): escrito.append(len(d))

    class _Falso:
        def __init__(self):
            self._caer = caidas[0] > 0
            if self._caer:
                caidas[0] -= 1
        def connect(self, *a, **k):
            if self._caer:
                raise OSError("se cayó la conexión")
            return True, "ok"
        def disconnect(self): pass
        @property
        def sftp(self):
            return type("S", (), {"open": lambda s, r, m: _Remoto()})()

    monkeypatch.setattr(mod, "SFTPClient", _Falso)
    cliente._store_stream_multi(str(local), "/x/peli.mkv", 4 * 1024 * 1024,
                                lambda d: None, 0, total, 4)
    assert sum(escrito) == total


def test_si_no_hay_forma_de_conectar_la_subida_falla(cliente, tmp_path, monkeypatch):
    """Reintentar está bien, pero no eternamente: si el servidor no está, hay
    que decirlo en vez de dar por buena una subida que no ha ocurrido."""
    import core.sftp_client as mod

    total = 40 * 1024 * 1024
    local = tmp_path / "peli.mkv"
    local.write_bytes(b"z" * total)

    class _SiempreCae:
        def connect(self, *a, **k):
            raise OSError("servidor caído")
        def disconnect(self): pass

    monkeypatch.setattr(mod, "SFTPClient", _SiempreCae)
    with pytest.raises(OSError):
        cliente._store_stream_multi(str(local), "/x/peli.mkv", 4 * 1024 * 1024,
                                    lambda d: None, 0, total, 4)
