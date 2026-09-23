"""Cliente SFTP (SSH), con la MISMA interfaz pública que FTPClient.

SFTP no es "FTP con cifrado": es un protocolo distinto que viaja dentro de una
sesión SSH, sin comandos FTP (STOR/LIST/MLSD...) ni conexión de datos aparte.
Aun así, para la aplicación tiene que ser intercambiable con el cliente FTP,
así que esta clase HEREDA de FTPClient y solo sustituye lo que de verdad
depende del protocolo:

  * conectar/desconectar y comprobar la conexión
  * listar (una carpeta, con o sin tamaños) y crear/borrar
  * mandar y traer bytes

Todo lo demás -- construir rutas desde la plantilla, recorrer el árbol de
carpetas, sumar tamaños, borrar una carpeta entera de dentro afuera, y sobre
todo el límite de velocidad, el progreso y la verificación del tamaño final de
las subidas -- es lógica de la aplicación y se hereda tal cual, así que las
subidas se comportan igual por SFTP que por FTP.

paramiko se importa dentro de connect() y no arriba: es una dependencia
pesada (arrastra `cryptography`, con binarios) y quien siga usando FTP no
tiene por qué pagar su carga al arrancar la aplicación.
"""

import io
import os
import stat as stat_mod
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Callable

from core.applog import get_logger
from core.ftp_client import FTPClient

_log = get_logger("aIBechos.sftp", "app.log")


#: Puerto estándar de SSH/SFTP (el de FTP es el 21).
DEFAULT_SFTP_PORT = 22


class SFTPClient(FTPClient):
    def __init__(self):
        super().__init__()
        self.port = DEFAULT_SFTP_PORT
        self._ssh = None       # paramiko.SSHClient
        self.sftp = None       # paramiko.SFTPClient

    # ------------------------------------------------------------ conexión --

    def connect(self, host: str, port: int, user: str, password: str,
                use_tls: bool = False) -> tuple[bool, str]:
        """*use_tls* se ignora: SFTP va cifrado siempre, por definición.

        Se acepta en la firma para que esta clase sea intercambiable con
        FTPClient sin tocar ninguno de los sitios que la instancian."""
        try:
            import paramiko
        except ImportError:
            return False, ("Falta la biblioteca paramiko, necesaria para SFTP. "
                           "Instálala con: pip install paramiko")
        _permitir_nombres_no_utf8()
        try:
            self.host = host
            self.port = int(port) or DEFAULT_SFTP_PORT
            self.user = user
            self.password = password
            self.use_tls = True          # SFTP siempre va cifrado

            self._ssh = paramiko.SSHClient()
            # Aceptar la clave del servidor la primera vez. Un servidor de
            # medios doméstico no tiene su clave en ningún known_hosts, y con
            # la política estricta de paramiko la conexión fallaría siempre.
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._ssh.connect(hostname=host, port=self.port, username=user,
                              password=password, timeout=15,
                              allow_agent=False, look_for_keys=False)
            self.sftp = self._ssh.open_sftp()
            self.sftp.get_channel().settimeout(30)
            return True, f"Conectado a {host} por SFTP"
        except Exception as e:
            self.disconnect()
            return False, f"Error SFTP: {self._explain(e)}"

    @staticmethod
    def _explain(e: Exception) -> str:
        """Mensaje en cristiano para los fallos más habituales.

        paramiko lanza excepciones con textos muy escuetos ("Authentication
        failed.") o directamente vacíos, y "error sin explicación" es
        exactamente lo que ya costó un disgusto con las subidas."""
        try:
            import paramiko
            if isinstance(e, paramiko.AuthenticationException):
                return "usuario o contraseña incorrectos"
            if isinstance(e, paramiko.SSHException) and not str(e):
                return "el servidor rechazó la conexión SSH"
        except ImportError:
            pass
        return str(e) or e.__class__.__name__

    def disconnect(self):
        for obj in (self.sftp, self._ssh):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self.sftp = None
        self._ssh = None

    def is_connected(self) -> bool:
        """Se mira el estado de la sesión SSH, sin preguntarle al servidor.

        En FTP hay que mandar un NOOP porque no hay otra forma de saberlo,
        pero aquí el transporte de paramiko ya sabe si el canal sigue vivo. Y
        conviene que sea gratis: is_connected() se comprueba al principio de
        CADA operación, así que un viaje de ida y vuelta al servidor por cada
        listado se acabaría notando en las carpetas grandes."""
        if self.sftp is None:
            return False
        transporte = self._ssh.get_transport() if self._ssh else None
        if transporte is not None and not transporte.is_active():
            self.sftp = None
            return False
        return True

    @contextmanager
    def widened_timeout(self, seconds: int = 120):
        """Igual que en FTP, pero sobre el canal SSH (no hay socket de control
        separado: aquí todo viaja por el mismo canal cifrado)."""
        canal = None
        orig = None
        try:
            if self.sftp is not None:
                canal = self.sftp.get_channel()
                orig = canal.gettimeout()
                canal.settimeout(seconds)
        except Exception:
            canal = None
        try:
            yield
        finally:
            try:
                if canal is not None:
                    canal.settimeout(orig if orig is not None else 30)
            except Exception:
                pass

    # ------------------------------------------------------------- listados --

    def _attrs(self, path: str) -> list:
        """listdir_attr con la ruta normalizada, o [] si no se puede leer."""
        try:
            return self.sftp.listdir_attr(path or "/")
        except Exception:
            return []

    def list_dirs(self, path: str) -> list[str]:
        if not self.is_connected():
            return []
        return [a.filename for a in self._attrs(path)
                if a.st_mode is not None and stat_mod.S_ISDIR(a.st_mode)]

    def list_files(self, path: str) -> list[str]:
        if not self.is_connected():
            return []
        return [a.filename for a in self._attrs(path)
                if a.st_mode is not None and stat_mod.S_ISREG(a.st_mode)]

    def list_files_with_sizes(self, path: str) -> list[tuple[str, int]]:
        if not self.is_connected():
            return []
        return [(a.filename, int(a.st_size or 0)) for a in self._attrs(path)
                if a.st_mode is not None and stat_mod.S_ISREG(a.st_mode)]

    def list_dir(self, path: str = "/") -> list[str]:
        if not self.is_connected():
            return []
        try:
            return self.sftp.listdir(path or "/")
        except Exception:
            return []

    def list_tree_recursive(self, path: str) -> "dict | None":
        """SFTP no tiene equivalente al "LIST -R" de FTP: no existe forma de
        pedir el árbol entero de una vez. Devolver None hace que la clase base
        caiga en el recorrido carpeta por carpeta, que aquí además es bastante
        más barato que en FTP (no hay que abrir una conexión de datos nueva
        por cada carpeta)."""
        return None

    # ------------------------------------------------------- consultas ------

    def file_exists(self, remote_file: str) -> bool:
        if self.sftp is None:
            return False
        try:
            self.sftp.stat(remote_file)
            return True
        except Exception:
            return False

    def get_remote_size(self, remote_file: str) -> Optional[int]:
        if self.sftp is None:
            return None
        try:
            return int(self.sftp.stat(remote_file).st_size)
        except Exception:
            return None

    def get_free_space(self, path: Optional[str] = None) -> Optional[int]:
        """Espacio libre en bytes, o None si el servidor no lo dice.

        Por SSH esto es MUCHO más fiable que por FTP (donde hay que ir
        probando comandos no estándar, ver FTPClient._get_free_space_here):
        el protocolo SFTP tiene una extensión estándar para esto, y si el
        servidor no la trae, siempre queda ejecutar `df` por SSH."""
        if self.sftp is None:
            return None
        destino = path or "/"
        libre = self._statvfs_free(destino)
        if libre is not None:
            return libre
        return self._df_free(destino)

    def _statvfs_free(self, path: str) -> Optional[int]:
        """Espacio libre vía la extensión statvfs@openssh.com.

        Se pide a mano porque paramiko NO expone ningún método statvfs (pese a
        que el servidor la soporte): llamar a self.sftp.statvfs() solo da un
        AttributeError. La respuesta son once enteros de 64 bits, en el orden
        que fija la extensión de OpenSSH; interesan el tamaño de bloque
        (f_frsize, el segundo) y los bloques libres PARA ESTE USUARIO
        (f_bavail, el quinto) -- no f_bfree, que incluye lo que el sistema
        reserva para root y daría una cifra mayor de la que se puede usar."""
        try:
            from paramiko.sftp import CMD_EXTENDED, CMD_EXTENDED_REPLY
            # _adjust_cwd es lo que usa paramiko para toda ruta: deja los bytes
            # como el servidor los espera, acentos incluidos.
            ruta = self.sftp._adjust_cwd(path)
            tipo, respuesta = self.sftp._request(CMD_EXTENDED,
                                                 "statvfs@openssh.com", ruta)
            if tipo != CMD_EXTENDED_REPLY:
                return None
            campos = [respuesta.get_int64() for _ in range(11)]
            f_frsize, f_bavail = campos[1], campos[4]
            return int(f_frsize) * int(f_bavail)
        except Exception:
            return None

    def _df_free(self, path: str) -> Optional[int]:
        """Respaldo con `df` para servidores sin esa extensión.

        Muchos servidores de SFTP no dan shell (el de referencia contesta
        "This service allows sftp connections only"), así que esto suele no
        estar disponible -- de ahí que se compruebe que la salida es de
        verdad la de un df antes de creérsela."""
        if self._ssh is None:
            return None
        try:
            # -P para el formato POSIX (una línea por sistema de archivos) y
            # LC_ALL para que las cabeceras no dependan del idioma del servidor.
            _in, out, _err = self._ssh.exec_command(
                f"LC_ALL=POSIX df -Pk {_shell_quote(path)}", timeout=15)
            lineas = out.read().decode("utf-8", "replace").splitlines()
            if len(lineas) < 2 or "blocks" not in lineas[0].lower():
                return None            # no es un df: no hay shell de verdad
            return int(lineas[1].split()[3]) * 1024
        except Exception:
            return None

    # ------------------------------------------------- crear / borrar -------

    def ensure_remote_dir(self, remote_path: str) -> tuple[bool, str]:
        if not self.is_connected():
            return False, "No conectado al servidor SFTP."
        actual = ""
        try:
            for parte in [p for p in remote_path.split("/") if p]:
                actual = f"{actual}/{parte}"
                try:
                    self.sftp.stat(actual)
                except Exception:
                    try:
                        self.sftp.mkdir(actual)
                    except Exception as e:
                        # Otra subida en paralelo puede haberla creado entre el
                        # stat y el mkdir: eso no es un error.
                        if not self.file_exists(actual):
                            return False, f"No se pudo crear {actual}: {self._explain(e)}"
            return True, remote_path
        except Exception as e:
            return False, f"Error SFTP: {self._explain(e)}"

    def _delete_remote_file(self, remote_file: str):
        self.sftp.remove(remote_file)

    def _remove_remote_dir(self, remote_dir: str):
        self.sftp.rmdir(remote_dir)

    def delete_file(self, remote_file: str,
                    progress_cb: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        if self.sftp is None:
            return False, "No conectado"
        try:
            if progress_cb:
                progress_cb(remote_file)
            self.sftp.remove(remote_file)
            return True, remote_file
        except Exception as e:
            return False, self._explain(e)

    def delete_folder_recursive(self, path: str, max_depth: int = 8,
                                progress_cb: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        if not self.is_connected():
            return False, "No conectado al servidor SFTP."
        return super().delete_folder_recursive(path, max_depth, progress_cb=progress_cb)

    def rename_file(self, remote_from: str, remote_to: str) -> tuple[bool, str]:
        if not self.is_connected():
            return False, "No conectado al servidor SFTP."
        ok, msg = self.ensure_remote_dir(remote_to.rsplit("/", 1)[0] or "/")
        if not ok:
            return False, msg
        try:
            self.sftp.rename(remote_from, remote_to)
            return True, remote_to
        except Exception as e:
            return False, f"Error SFTP: {self._explain(e)}"

    # ------------------------------------------------ enviar / recibir ------

    #: A partir de este tamaño compensa repartir un archivo entre conexiones.
    #: Por debajo, abrirlas cuesta más de lo que se gana.
    MULTIFLOW_MIN_BYTES = 16 * 1024 * 1024

    #: Tamaño de cada trozo de la cola de trabajo. Pequeño para que todas las
    #: conexiones acaben a la vez, pero no tanto que ir a por trabajo se note.
    CHUNK_BYTES = 8 * 1024 * 1024

    #: Reintentos de un trozo cuya conexión se cayó, antes de darlo por perdido.
    CHUNK_MAX_ATTEMPTS = 3

    def _store_stream(self, fileobj, remote_file: str, blocksize: int,
                      callback, resume_offset: int = 0, streams: int = 1):
        """Manda el archivo, por una conexión o repartido entre varias.

        Repartirlo es lo único que sube de verdad la velocidad con un archivo
        solo: medido contra el servidor, CADA conexión se queda clavada en ~2
        MB/s (da igual el tamaño de bloque, y le pasa igual al putfo() del
        propio paramiko), pero varias suman casi linealmente: 2 -> 3,9 MB/s,
        4 -> 6,5, 6 -> 9,2. El cuello no es la línea ni la ventana SSH (con 98
        ms de latencia darían para 20 MB/s): es un límite por conexión."""
        ruta_local = getattr(fileobj, "name", None)
        total = None
        if ruta_local:
            try:
                total = os.path.getsize(ruta_local)
            except OSError:
                total = None
        if (streams > 1 and ruta_local and total is not None
                and total - resume_offset >= self.MULTIFLOW_MIN_BYTES):
            self._store_stream_multi(ruta_local, remote_file, blocksize, callback,
                                     resume_offset, total, streams)
            return
        self._store_stream_single(fileobj, remote_file, blocksize, callback,
                                  resume_offset)

    def _store_stream_multi(self, ruta_local: str, remote_file: str, blocksize: int,
                            callback, desde: int, total: int, streams: int):
        """Reparte el archivo entre varias conexiones, por trozos.

        Todas escriben en el MISMO archivo remoto, cada una colocada en su
        trozo (seek): es lo que hacen los gestores de descargas, y en SFTP vale
        porque se puede escribir en cualquier posición.

        Los trozos van en una cola común y no en tramos fijos por conexión: con
        tramos fijos, la conexión más rápida terminaba antes y se quedaba
        parada, así que el archivo acababa subiendo por una sola y la velocidad
        se desplomaba al final (medido: empezaba a 8 MB/s y acababa a 3,7)."""
        # Dejar el archivo creado antes de repartir: los trozos lo abren para
        # escribir en su sitio, y ninguno debe truncarlo.
        with self.sftp.open(remote_file, "r+b" if desde > 0 else "wb"):
            pass

        trozos = deque()
        posicion = desde
        while posicion < total:
            siguiente = min(posicion + self.CHUNK_BYTES, total)
            trozos.append((posicion, siguiente))
            posicion = siguiente

        reparto = threading.Lock()
        parar = threading.Event()
        fallos = []
        intentos = {}
        ultimo_error = []

        def siguiente_trozo():
            with reparto:
                return trozos.popleft() if trozos else None

        def reencolar(trozo) -> bool:
            """Devuelve el trozo a la cola para que lo coja otra conexión.

            Sin esto, que se cayera UNA conexión tiraba el archivo entero y
            había que resubirlo desde cero -- bastante más probable usando
            varias. Los trozos son independientes, así que reintentar uno no
            molesta a los demás."""
            with reparto:
                intentos[trozo] = intentos.get(trozo, 1) + 1
                if intentos[trozo] > self.CHUNK_MAX_ATTEMPTS:
                    return False
                trozos.appendleft(trozo)
                return True

        def quedan_trozos() -> bool:
            with reparto:
                return bool(trozos)

        def trabajar():
            """Un hilo: abre su conexión y va cogiendo trozos. Si se cae,
            devuelve lo que tuviera a medias y lo reintenta con otra."""
            for _ in range(self.CHUNK_MAX_ATTEMPTS):
                if parar.is_set() or not quedan_trozos():
                    return
                if _con_una_conexion():
                    return

        def _con_una_conexion() -> bool:
            """True si terminó; False si se cayó y conviene reintentar."""
            cliente = None
            # Solo el trozo de ESTE hilo: reencolar los de los demás (que los
            # están subiendo ahora mismo) duplicaría el trabajo.
            en_curso = [None]
            try:
                cliente = SFTPClient()
                ok, msg = cliente.connect(self.host, self.port, self.user,
                                          self.password)
                if not ok:
                    raise IOError(msg)
                with open(ruta_local, "rb") as local, \
                        cliente.sftp.open(remote_file, "r+b") as remoto:
                    remoto.set_pipelined(True)
                    while not parar.is_set():
                        trozo = siguiente_trozo()
                        if trozo is None:
                            break
                        en_curso[0] = trozo
                        inicio, fin = trozo
                        local.seek(inicio)
                        remoto.seek(inicio)
                        queda = fin - inicio
                        while queda > 0 and not parar.is_set():
                            datos = local.read(min(blocksize, queda))
                            if not datos:
                                break
                            remoto.write(datos)
                            queda -= len(datos)
                            callback(datos)
                        if queda <= 0:
                            en_curso[0] = None
                return True
            except (OSError, EOFError) as e:
                if en_curso[0] is None or reencolar(en_curso[0]):
                    _log.warning("Subida repartida: una conexión se cayó (%s); "
                                 "su trabajo vuelve a la cola", e)
                    ultimo_error.append(e)
                    return False
                parar.set()
                fallos.append(e)
            except BaseException as e:
                # Cancelar/saltar, que el callback lanza a propósito: se para
                # todo y se relanza en el hilo principal, que sabe
                # distinguirlas (ver FTPClient.upload_file).
                parar.set()
                fallos.append(e)
            finally:
                if cliente is not None:
                    cliente.disconnect()
            return True

        hilos = [threading.Thread(target=trabajar, daemon=True)
                 for _ in range(streams)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        if fallos:
            raise fallos[0]
        # Si se agotaron los reintentos sin que nadie pudiera conectar, queda
        # trabajo sin hacer: hay que decirlo con el error de verdad, o la
        # subida se daría por buena sin haber subido nada.
        if trozos:
            raise (ultimo_error[-1] if ultimo_error
                   else IOError("no se pudo subir el archivo entero"))

    def _store_stream_single(self, fileobj, remote_file: str, blocksize: int,
                             callback, resume_offset: int = 0):
        """Vuelca *fileobj* en el archivo remoto llamando a *callback* por
        bloque -- el mismo callback que usa el cliente FTP, que es quien
        aplica el límite de velocidad y va informando del progreso.

        Se escribe a mano en vez de usar sftp.putfo() porque putfo() informa
        del progreso pero NO deja frenar la subida ni cancelarla a mitad, y
        con reanudación hay que abrir en modo añadir en vez de truncar."""
        modo = "ab" if resume_offset > 0 else "wb"
        with self.sftp.open(remote_file, modo) as remoto:
            # Sin esto paramiko manda un bloque y espera la confirmación antes
            # de mandar el siguiente, y la subida se arrastra en cuanto hay
            # algo de latencia.
            remoto.set_pipelined(True)
            if resume_offset > 0:
                remoto.seek(resume_offset)
            while True:
                datos = fileobj.read(blocksize)
                if not datos:
                    break
                remoto.write(datos)
                callback(datos)

    def upload_bytes(self, data: bytes, remote_path: str) -> tuple[bool, str]:
        if not self.is_connected():
            return False, "No conectado al servidor SFTP."
        ok, msg = self.ensure_remote_dir(remote_path.rsplit("/", 1)[0] or "/")
        if not ok:
            return False, msg
        try:
            with self.sftp.open(remote_path, "wb") as remoto:
                remoto.write(data)
            return True, remote_path
        except Exception as e:
            return False, f"Error SFTP: {self._explain(e)}"

    def download_bytes(self, remote_path: str) -> Optional[bytes]:
        if not self.is_connected():
            return None
        try:
            buf = io.BytesIO()
            with self.sftp.open(remote_path, "rb") as remoto:
                buf.write(remoto.read())
            return buf.getvalue()
        except Exception:
            return None

    def download_file(self, remote_file: str, local_path: str,
                      progress_cb: Optional[Callable[[int], None]] = None) -> tuple[bool, str]:
        if not self.is_connected():
            return False, "No conectado al servidor SFTP."
        try:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            recibidos = 0
            with self.sftp.open(remote_file, "rb") as remoto, \
                    open(local_path, "wb") as local:
                remoto.prefetch()
                while True:
                    trozo = remoto.read(4 * 1024 * 1024)
                    if not trozo:
                        break
                    local.write(trozo)
                    recibidos += len(trozo)
                    if progress_cb:
                        progress_cb(recibidos)
            return True, local_path
        except OSError as e:
            return False, f"Error de archivo: {e}"
        except Exception as e:
            return False, f"Error SFTP: {self._explain(e)}"


def _permitir_nombres_no_utf8():
    """Que un nombre de archivo raro no tire abajo la carpeta entera.

    paramiko decodifica en UTF-8 ESTRICTO todo el texto que llega del
    servidor, nombres de archivo incluidos. Un servidor con archivos
    antiguos en latin-1 -- una "ñ" es ahí el byte 0xf1, que no es UTF-8
    válido -- hace saltar UnicodeDecodeError, y eso no se lleva por delante
    solo ese nombre: se lleva el LISTADO ENTERO de la carpeta. La aplicación
    la ve vacía y actúa como si no hubiera nada, así que no encuentra la
    carpeta de la serie, no detecta duplicados y no ve los episodios que ya
    están subidos. Pasa de verdad, y sin ningún error a la vista.

    Es el mismo compromiso que ya hacía el cliente FTP en
    FTPClient._retrlines_lenient (ver ahí): el nombre problemático sale con
    un "?" en el carácter que no se pudo decodificar, pero el resto de la
    carpeta se lee entero, y el patrón NxNN de temporada/episodio -- que no
    lleva acentos -- se mantiene intacto para poder seguir analizándolo.

    Se parchea paramiko.message y no paramiko.util porque el primero importa
    `u` directamente, así que cambiar el segundo no le afectaría."""
    import paramiko.message as _message
    if getattr(_message, "_aibechos_utf8_indulgente", False):
        return
    original = _message.u

    def _u_indulgente(s, encoding="utf8"):
        if isinstance(s, bytes):
            return s.decode(encoding, "replace")
        return original(s, encoding)

    _message.u = _u_indulgente
    _message._aibechos_utf8_indulgente = True


def _shell_quote(path: str) -> str:
    """Entrecomilla una ruta para pasarla por la línea de órdenes de SSH.

    Las rutas del servidor llevan espacios y acentos ("/Películas/...") y sin
    comillas `df` recibiría cada palabra como un argumento distinto."""
    return "'" + str(path).replace("'", "'\\''") + "'"
