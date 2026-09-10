"""
Cliente FTP con soporte para rutas configurables mediante plantillas.
Variables disponibles en la plantilla de ruta:
  {serie}, {temporada}, {temporada:02d}, {año}, {tipo}
"""

import ftplib
import io
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Callable


class FTPClient:
    def __init__(self):
        self.ftp: Optional[ftplib.FTP] = None
        self.host = ""
        self.port = 21
        self.user = ""
        self.password = ""
        self.use_tls = False

    def connect(self, host: str, port: int, user: str, password: str, use_tls: bool = False) -> tuple[bool, str]:
        try:
            self.host = host
            self.port = port
            self.user = user
            self.password = password
            self.use_tls = use_tls

            # Arrancar siempre en Latin-1 (ISO-8859-1).
            # Python 3.9+ cambió el default de ftplib a UTF-8, pero muchos servidores
            # (NAS, vsftpd, ProFTPD) dicen "200 UTF8 OK" y luego interpretan los bytes
            # como Latin-1 → las tildes se convierten en Ã³, Ã©, etc.
            # Latin-1 cubre todo el alfabeto español y europeo occidental sin problemas.
            # Solo subimos a UTF-8 si el servidor lo confirma explícitamente via FEAT.
            if use_tls:
                self.ftp = ftplib.FTP_TLS(encoding="latin-1")
            else:
                self.ftp = ftplib.FTP(encoding="latin-1")

            self.ftp.connect(host, port, timeout=15)
            self.ftp.login(user, password)

            if use_tls:
                self.ftp.prot_p()

            # Detectar soporte UTF-8 real via FEAT (más fiable que OPTS)
            try:
                feat = self.ftp.sendcmd("FEAT")
                if "UTF8" in feat.upper():
                    resp = self.ftp.sendcmd("OPTS UTF8 ON")
                    if resp.startswith("2"):
                        self.ftp.encoding = "utf-8"
            except Exception:
                pass   # Quedarse en latin-1

            return True, f"Conectado a {host}"
        except ftplib.all_errors as e:
            self.ftp = None
            return False, f"Error FTP: {e}"
        except OSError as e:
            self.ftp = None
            return False, f"Error de red: {e}"

    def disconnect(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass
            self.ftp = None

    def is_connected(self) -> bool:
        if not self.ftp:
            return False
        try:
            self.ftp.voidcmd("NOOP")
            return True
        except Exception:
            self.ftp = None
            return False

    def build_remote_path(self, template: str, serie: str, season: int = None, year: str = "", media_type: str = "tv") -> str:
        """
        Construye la ruta remota según la plantilla.
        Ejemplo: /Plex/{serie}/Temporada {temporada:02d}/
        """
        replacements = {
            "serie": _ftp_safe(serie),
            "temporada": season or 1,
            "año": year or "",
            "tipo": {"tv": "Series", "movie": "Películas", "libro": "Libros"}.get(media_type, "Películas"),
        }
        try:
            path = template.format(**replacements)
        except (KeyError, ValueError):
            path = template  # fallback sin formato

        # Normalizar separadores
        path = path.replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        return path

    def ensure_remote_dir(self, remote_path: str) -> tuple[bool, str]:
        """Crea la carpeta remota si no existe (recursivo)."""
        if not self.is_connected():
            return False, "No conectado al servidor FTP."
        try:
            parts = [p for p in remote_path.split("/") if p]
            current = "/"
            for part in parts:
                current = f"{current}{part}/"
                try:
                    self.ftp.cwd(current)
                except ftplib.error_perm:
                    try:
                        self.ftp.mkd(current)
                    except ftplib.error_perm as e:
                        return False, f"No se pudo crear {current}: {e}"
            return True, remote_path
        except ftplib.all_errors as e:
            return False, f"Error FTP: {e}"

    def file_exists(self, remote_file: str) -> bool:
        """Devuelve True si el archivo ya existe en el servidor."""
        if not self.ftp:
            return False
        try:
            self.ftp.size(remote_file)
            return True
        except ftplib.all_errors:
            return False

    def get_remote_size(self, remote_file: str) -> Optional[int]:
        """Tamaño en bytes del archivo remoto, o None si no existe/no se puede consultar."""
        if not self.ftp:
            return None
        try:
            return self.ftp.size(remote_file)
        except ftplib.all_errors:
            return None

    @contextmanager
    def widened_timeout(self, seconds: int = 120):
        """Amplía temporalmente el timeout del socket de control -- el fijo
        de 15s puesto en connect() vale para comandos rápidos, pero listar
        una carpeta con miles de archivos (categorías grandes, cientos o
        miles de películas sueltas sin carpeta propia) puede tardar bastante
        más en un NAS lento, y el comando fallaría a mitad con un timeout
        silencioso -- ftplib.all_errors lo cubre, así que el código que
        llama ni se entera de que fue un timeout, solo ve "no hay datos" o
        cae al método más lento (list_dirs en vez de LIST -R), agravando
        aún más la lentitud. Mismo patrón que upload_file ya usa durante la
        transferencia de archivos grandes, ver ahí. Usar alrededor de
        cualquier operación de listado que pueda ser grande (ver
        gui/app.py::_bootstrap_category_stats)."""
        orig = None
        try:
            if self.ftp and self.ftp.sock:
                orig = self.ftp.sock.gettimeout()
                self.ftp.sock.settimeout(seconds)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                if self.ftp and self.ftp.sock:
                    self.ftp.sock.settimeout(orig if orig is not None else 30)
            except Exception:
                pass

    def list_dirs(self, path: str) -> list[str]:
        """Devuelve los nombres de las subcarpetas directas de *path* (sin archivos).
        Usa MLSD si el servidor lo soporta; si no, cae a parsear LIST (formato Unix)."""
        if not self.is_connected():
            return []
        try:
            names = []
            for name, facts in self.ftp.mlsd(path):
                if name in (".", "..") or facts.get("type") != "dir":
                    continue
                names.append(name)
            return names
        except ftplib.all_errors:
            pass   # servidor sin soporte MLSD -> probar LIST
        try:
            lines = self._retrlines_lenient(f"LIST {path}")
            return _parse_unix_list_dirs(lines)
        except ftplib.all_errors:
            return []

    def list_files(self, path: str) -> list[str]:
        """Devuelve los nombres de los archivos directos de *path* (sin
        subcarpetas). Mismo patrón que list_dirs: MLSD si el servidor lo
        soporta, si no cae a parsear LIST (formato Unix)."""
        if not self.is_connected():
            return []
        try:
            names = []
            for name, facts in self.ftp.mlsd(path):
                if name in (".", "..") or facts.get("type") != "file":
                    continue
                names.append(name)
            return names
        except ftplib.all_errors:
            pass   # servidor sin soporte MLSD -> probar LIST
        try:
            lines = self._retrlines_lenient(f"LIST {path}")
            return _parse_unix_list_files(lines)
        except ftplib.all_errors:
            return []

    def _retrlines_lenient(self, cmd: str) -> list[str]:
        """Igual que ftplib.FTP.retrlines(), pero decodificando cada línea
        con errors="replace" en vez de estricto. Necesario porque un
        servidor puede tener archivos antiguos con nombres en latin-1
        (de antes de activar utf8_filesystem, por ejemplo) mezclados con
        otros en UTF-8 -- con decodificación estricta, una sola línea con
        bytes inválidos tira abajo el listado ENTERO de la carpeta
        (UnicodeDecodeError, que ftplib.all_errors no cubre). Con
        errors="replace" esa línea concreta sale con un "?" en el
        carácter problemático (en vez de crash), pero el resto del
        nombre -- y sobre todo el patrón NxNN de temporada/episodio, que
        no usa acentos -- se mantiene intacto y se puede seguir parseando."""
        self.ftp.sendcmd("TYPE A")
        lines = []
        with self.ftp.transfercmd(cmd) as conn, \
                conn.makefile("r", encoding=self.ftp.encoding, errors="replace") as fp:
            while True:
                line = fp.readline(self.ftp.maxline + 1)
                if not line:
                    break
                if line[-2:] == "\r\n":
                    line = line[:-2]
                elif line[-1:] == "\n":
                    line = line[:-1]
                lines.append(line)
        self.ftp.voidresp()
        return lines

    def list_files_recursive(self, path: str, max_depth: int = 3) -> list[str]:
        """Nombres de TODOS los archivos bajo *path*, bajando por sus
        subcarpetas (p.ej. "Serie/Temporada 01/", "Serie/Temporada 02/")
        hasta max_depth niveles -- usado por el detector de episodios que
        faltan para comprobar de verdad qué hay en el FTP para una serie
        concreta (ver core/missing_episodes.py), sin necesitar saber de
        antemano cómo se llaman las subcarpetas de temporada. Solo
        nombres de archivo sueltos (no rutas completas): basta para que
        detect_episode() saque la temporada/episodio del propio nombre."""
        if max_depth <= 0 or not self.is_connected():
            return []
        files = list(self.list_files(path))
        for sub in self.list_dirs(path):
            files.extend(self.list_files_recursive(f"{path.rstrip('/')}/{sub}", max_depth - 1))
        return files

    def list_tree_recursive(self, path: str) -> "dict | None":
        """Intenta traer el árbol COMPLETO bajo *path* en una sola
        petición con "LIST -R" -- una extensión no estándar de FTP que
        soportan varios servidores (vsftpd, con ls_recurse_enable=YES en
        vsftpd.conf; también proftpd y pure-ftpd), mucho más rápido que
        recorrer subcarpeta por subcarpeta con un listado por cada una
        (ver get_folder_size). Devuelve {ruta_de_carpeta: [(nombre,
        tamaño), ...], ...} -- una entrada por cada carpeta del árbol,
        con sus archivos directos -- o None si el servidor no soporta -R
        o la respuesta no tiene el formato esperado, en cuyo caso hay que
        recurrir al recorrido manual carpeta por carpeta."""
        if not self.is_connected():
            return None
        try:
            lines = self._retrlines_lenient(f"LIST -R {path}")
        except ftplib.all_errors:
            return None
        return _parse_recursive_list_sections(lines)

    def list_files_with_sizes(self, path: str) -> list[tuple[str, int]]:
        """[(nombre, tamaño_en_bytes), ...] de los archivos directos de
        *path* -- mismo patrón MLSD-primero-LIST-después que list_files,
        pero conservando el tamaño (que list_files descarta). Usado por
        get_folder_size(), para la herramienta de liberar espacio."""
        if not self.is_connected():
            return []
        try:
            result = []
            for name, facts in self.ftp.mlsd(path):
                if name in (".", "..") or facts.get("type") != "file":
                    continue
                try:
                    size = int(facts.get("size", 0))
                except (TypeError, ValueError):
                    size = 0
                result.append((name, size))
            return result
        except ftplib.all_errors:
            pass   # servidor sin soporte MLSD -> probar LIST
        try:
            lines = self._retrlines_lenient(f"LIST {path}")
            return _parse_unix_list_files_with_sizes(lines)
        except ftplib.all_errors:
            return []

    def get_folder_tree(self, path: str, max_depth: int = 6) -> "dict | None":
        """{ruta_de_carpeta: [(nombre, tamaño), ...], ...} de TODO el árbol
        bajo *path* -- LIST -R si el servidor lo soporta (ver
        list_tree_recursive, mucho más rápido), si no recorre subcarpeta
        por subcarpeta con list_dirs/list_files_with_sizes (mismo patrón
        que get_folder_size, pero acumulando el árbol completo en vez de
        solo el total). Para el desplegable "ver árbol de archivos" de una
        serie en Episodios que faltan (ver
        gui/app.py::_toggle_missing_ep_ftp_tree) -- a diferencia de
        list_files_recursive (que descarta la carpeta y solo deja el
        nombre de archivo suelto, pensado para detect_episode()), aquí
        hace falta conservar la estructura de carpetas para poder
        dibujarla con indentación. None si no hay conexión."""
        if not self.is_connected():
            return None
        tree = self.list_tree_recursive(path)
        if tree is not None:
            return tree
        return self._walk_folder_tree(path, max_depth)

    def _walk_folder_tree(self, path: str, max_depth: int) -> dict:
        if max_depth <= 0:
            return {path: []}
        result = {path: self.list_files_with_sizes(path)}
        for sub in self.list_dirs(path):
            result.update(self._walk_folder_tree(f"{path.rstrip('/')}/{sub}", max_depth - 1))
        return result

    def get_folder_size(self, path: str, max_depth: int = 6) -> int:
        """Tamaño total en bytes de todos los archivos bajo *path*,
        recorriendo subcarpetas -- la herramienta de liberar espacio
        necesita saber cuánto ocupa cada serie/película completa (con
        todas sus temporadas) para el filtro de tamaño y para mostrar
        cuánto se liberaría al borrar. max_depth más alto que
        list_files_recursive (6 en vez de 3) porque aquí sí puede haber
        estructuras más profundas (serie/temporada/extras/...) y calcular
        mal el tamaño (de menos) es peor que tardar un poco más."""
        if max_depth <= 0 or not self.is_connected():
            return 0
        total = sum(size for _name, size in self.list_files_with_sizes(path))
        for sub in self.list_dirs(path):
            total += self.get_folder_size(f"{path.rstrip('/')}/{sub}", max_depth - 1)
        return total

    def delete_folder_recursive(self, path: str, max_depth: int = 8,
                                progress_cb: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        """Borra una carpeta completa (todos sus archivos y subcarpetas,
        y al final la propia carpeta) del servidor FTP -- para la
        herramienta de liberar espacio (primera vez que la app borra algo
        de verdad; hasta ahora solo subía/listaba/renombraba). Borra
        primero el contenido de más profundo a menos y al final la propia
        carpeta, porque el protocolo FTP no permite RMD de una carpeta
        que no esté vacía. Se detiene y devuelve el fallo en cuanto algo
        no se puede borrar, en vez de seguir a medias -- mejor que el
        usuario vea un error claro a que quede la carpeta parcialmente
        vaciada sin saberlo.

        progress_cb: callback opcional (str) -> None llamado con la ruta de
        cada archivo/carpeta justo ANTES de intentar borrarla -- para que
        la GUI pueda mostrar "Eliminando …" durante un borrado largo (una
        serie puede tener cientos de archivos y tardar minutos; sin esto
        la app parece colgada, ver _delete_cleanup_item_worker)."""
        if not self.is_connected():
            return False, "No conectado al servidor FTP."
        if max_depth <= 0:
            return False, "Profundidad máxima de subcarpetas alcanzada"
        try:
            for sub in self.list_dirs(path):
                ok, msg = self.delete_folder_recursive(f"{path.rstrip('/')}/{sub}", max_depth - 1,
                                                       progress_cb=progress_cb)
                if not ok:
                    return False, msg
            for name in self.list_files(path):
                item_path = f"{path.rstrip('/')}/{name}"
                if progress_cb:
                    progress_cb(item_path)
                self._delete_remote_file(item_path)
            if progress_cb:
                progress_cb(path)
            self._remove_remote_dir(path)
            return True, "Carpeta eliminada"
        except ftplib.all_errors as e:
            return False, str(e)

    def get_free_space(self, path: Optional[str] = None) -> Optional[int]:
        """Intenta obtener espacio libre en bytes probando varios comandos.

        Si se indica *path*, se cambia primero a ese directorio (y se vuelve
        al de antes al terminar) para que, en los servidores que sí soportan
        alguno de estos comandos, el espacio reportado sea el del disco
        concreto donde va a aterrizar la subida — no el de donde sea que
        estuviera la conexión en ese momento. Necesario porque un mismo
        servidor puede tener varias rutas repartidas en discos distintos
        (visto de primera mano: la misma carpeta lógica montada sobre dos
        discos físicos separados puede dar cifras de espacio muy distintas
        según en cuál se pregunte)."""
        if not self.ftp:
            return None
        import re
        _num = re.compile(r'\d{6,}')  # número de ≥6 dígitos = bytes plausibles

        prev_dir = None
        if path:
            try:
                prev_dir = self.ftp.pwd()
                self.ftp.cwd(path)
            except ftplib.all_errors:
                prev_dir = None   # no se pudo entrar -- seguir preguntando donde estuviera

        try:
            return self._get_free_space_here(_num)
        finally:
            if prev_dir is not None:
                try:
                    self.ftp.cwd(prev_dir)
                except ftplib.all_errors:
                    pass

    def _get_free_space_here(self, _num) -> Optional[int]:
        # vsftpd no soporta ningún comando de espacio libre — salir inmediatamente
        try:
            stat = self.ftp.sendcmd("STAT")
            if "vsFTPd" in stat or "vsftpd" in stat.lower():
                return None
        except Exception:
            pass

        # 1. AVBL — Synology, QNAP, algunos NAS
        try:
            resp = self.ftp.sendcmd("AVBL")
            m = _num.search(resp)
            if m:
                return int(m.group())
        except Exception:
            pass

        # 2. SITE AVAIL — ProFTPD mod_site
        try:
            resp = self.ftp.sendcmd("SITE AVAIL")
            m = _num.search(resp)
            if m:
                return int(m.group())
        except Exception:
            pass

        # 3. XDISKFREE — FileZilla Server y algunos BSD
        try:
            resp = self.ftp.sendcmd("XDISKFREE")
            m = _num.search(resp)
            if m:
                return int(m.group())
        except Exception:
            pass

        # 4. SITE DISKFREE — vsftpd con módulo extra
        try:
            resp = self.ftp.sendcmd("SITE DISKFREE")
            m = _num.search(resp)
            if m:
                return int(m.group())
        except Exception:
            pass

        return None

    # ---------------------------------------------------------------------
    # Primitivas de transporte. Son lo ÚNICO que cambia entre FTP y SFTP: el
    # resto de la clase (recorrer carpetas, calcular tamaños, el límite de
    # velocidad y el progreso de la subida, la verificación del tamaño final)
    # es lógica de la aplicación y la comparten los dos, ver
    # core/sftp_client.py.
    # ---------------------------------------------------------------------

    def _store_stream(self, fileobj, remote_file: str, blocksize: int,
                      callback, resume_offset: int = 0, streams: int = 1):
        """Vuelca *fileobj* (ya posicionado) en el archivo remoto, llamando a
        *callback* con cada bloque enviado."""
        if resume_offset > 0:
            self.ftp.storbinary(f"STOR {remote_file}", fileobj, blocksize,
                                callback, rest=resume_offset)
        else:
            self.ftp.storbinary(f"STOR {remote_file}", fileobj, blocksize, callback)

    def _delete_remote_file(self, remote_file: str):
        self.ftp.delete(remote_file)

    def _remove_remote_dir(self, remote_dir: str):
        self.ftp.rmd(remote_dir)

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_cb: Optional[Callable[[int, int, float], None]] = None,
        cancel_event=None,
        skip_event=None,
        speed_limit_kbs: int = 0,
        try_resume: bool = False,
        remote_filename: Optional[str] = None,
        streams: int = 1,
    ) -> tuple[bool, str]:
        """
        Sube un archivo al servidor FTP.
        progress_cb(bytes_sent, total_bytes, speed_bps).
        cancel_event: threading.Event — para toda la cola.
        skip_event:   threading.Event — salta solo este archivo.
        speed_limit_kbs: límite de velocidad en KB/s (0 = sin límite).
        remote_filename: nombre a usar en el servidor si debe ser distinto al
          nombre del archivo local (p.ej. "renombrar en destino" sin haber
          renombrado en origen). Por defecto, el nombre del archivo local.
        streams: cuántas conexiones se reparten ESTE archivo. Solo lo aprovecha
          SFTP (ver core/sftp_client.py); por FTP se ignora. El servidor limita
          cada conexión por separado (~2 MB/s medidos), así que con una sola no
          se llega al límite de velocidad configurado por muy alto que esté.
        Retorna: (ok, msg) donde msg puede ser 'cancelado' o 'saltado'.
        """
        if not self.is_connected():
            return False, "No conectado al servidor FTP."

        local = Path(local_path)
        if not local.exists():
            return False, f"Archivo no encontrado: {local_path}"

        remote_file = f"{remote_path.rstrip('/')}/{remote_filename or local.name}"
        total = local.stat().st_size
        sent = [0]

        # Detectar si hay una subida parcial y reanudar desde ahí
        resume_offset = 0
        if try_resume:
            try:
                # get_remote_size() y no self.ftp.size(): así la reanudación
                # funciona igual por SFTP, donde no hay ningún self.ftp.
                remote_size = self.get_remote_size(remote_file)
                if remote_size is not None:
                    if remote_size >= total:
                        # Archivo ya completo en el servidor
                        return True, remote_file
                    if remote_size > 0:
                        resume_offset = remote_size
                        sent[0] = resume_offset
            except ftplib.all_errors:
                resume_offset = 0  # no existe o no soporta SIZE — subir de cero

        import time
        t_window = [time.monotonic()]
        sent_window = [0]
        speed_last = [0.0]
        # La velocidad que se enseña es la cruda de cada tramo (bytes del
        # tramo / tiempo del tramo), sin suavizar -- a petición del usuario,
        # que prefiere ver la velocidad actual aunque fluctúe.

        # speed_limit_kbs puede ser int o callable que devuelve KB/s
        def _get_speed_limit() -> int:
            """Devuelve el límite actual en bytes/s (0 = sin límite). Robusto ante errores."""
            try:
                kbs = speed_limit_kbs() if callable(speed_limit_kbs) else speed_limit_kbs
                return int(kbs) * 1024 if kbs and int(kbs) > 0 else 0
            except Exception:
                return 0

        # 256 KB y no 4 MB: con el archivo repartido entre varias conexiones,
        # un bloque grande hace que los avisos de progreso lleguen muy
        # espaciados y a trompicones, y la velocidad mostrada oscila aunque la
        # real sea estable (medido: saltos del 57 % de la media con 4 MB
        # frente al 22 % con 256 KB). El caudal no se resiente: lo marca el
        # servidor, no el número de llamadas de Python.
        BLOCKSIZE = 256 * 1024
        REPORT_INTERVAL = 0.4

        class _Cancelled(Exception):
            pass
        class _Skipped(Exception):
            pass

        # Punto de referencia para el throttle acumulativo (tiempo/bytes desde
        # el último cambio de límite). Se reinicia cada vez que el límite
        # configurado cambia de valor (activado, desactivado o modificado a
        # media subida), para no arrastrar "deuda"/"crédito" de antes del
        # cambio — si no, activar un límite tras subir un rato sin límite
        # provocaría una pausa larguísima intentando compensar los bytes que
        # ya se enviaron rápido, y viceversa.
        throttle_limit  = [_get_speed_limit()]
        throttle_time0  = [time.monotonic()]
        throttle_sent0  = [sent[0]]

        def callback(data: bytes):
            if cancel_event and cancel_event.is_set():
                raise _Cancelled()
            if skip_event and skip_event.is_set():
                raise _Skipped()
            sent[0] += len(data)

            # Se relee el límite en cada bloque (no solo al empezar la subida)
            # para poder activar/cambiar/desactivar el límite en caliente.
            cur_limit = _get_speed_limit()
            if cur_limit != throttle_limit[0]:
                throttle_limit[0] = cur_limit
                throttle_time0[0] = time.monotonic()
                throttle_sent0[0] = sent[0]

            # Cada bloque (BLOCKSIZE) ya se envió a la red antes de llegar aquí,
            # así que el retraso necesario para compensarlo puede superar 1s si
            # el límite configurado es menor que BLOCKSIZE/seg — hay que dormir
            # en tramos de máx. 1s hasta agotar el retraso completo (no solo un
            # tramo), si no el límite real acaba siendo ~BLOCKSIZE/seg pase lo
            # que pase se configure por debajo de eso. Se comprueba cancel/skip
            # entre tramos para no perder capacidad de respuesta al cancelar.
            if cur_limit > 0:
                elapsed  = time.monotonic() - throttle_time0[0]
                expected = (sent[0] - throttle_sent0[0]) / cur_limit
                delay    = expected - elapsed
                while delay > 0:
                    if cancel_event and cancel_event.is_set():
                        raise _Cancelled()
                    if skip_event and skip_event.is_set():
                        raise _Skipped()
                    nap = min(delay, 1.0)
                    time.sleep(nap)
                    delay -= nap

            now = time.monotonic()
            window_elapsed = now - t_window[0]
            if window_elapsed >= REPORT_INTERVAL:
                speed_last[0] = (sent[0] - sent_window[0]) / window_elapsed
                t_window[0] = now
                sent_window[0] = sent[0]
                if progress_cb:
                    progress_cb(sent[0], total, speed_last[0])

        try:
            ok, msg = self.ensure_remote_dir(remote_path)
            if not ok:
                return False, msg

            # Ampliar timeout durante la transferencia para evitar
            # "cannot read from timed out object" en archivos grandes.
            # El timeout de connect (15 s) es solo para comandos de control,
            # no debe aplicar al socket de datos que puede tardar minutos.
            _orig_timeout = None
            try:
                if self.ftp.sock:
                    _orig_timeout = self.ftp.sock.gettimeout()
                    self.ftp.sock.settimeout(600)  # 10 min máximo por transferencia
            except Exception:
                pass

            try:
                with open(local_path, "rb") as f:
                    if resume_offset > 0:
                        f.seek(resume_offset)
                    self._store_stream(f, remote_file, BLOCKSIZE, callback,
                                       resume_offset, streams)
            finally:
                # Restaurar timeout original (o 30 s como valor seguro)
                try:
                    if self.ftp and self.ftp.sock:
                        self.ftp.sock.settimeout(_orig_timeout if _orig_timeout is not None else 30)
                except Exception:
                    pass

            # Verificación de integridad: el servidor puede devolver "226
            # Transfer complete" aunque el archivo haya quedado incompleto o
            # corrupto (corte de red silencioso, disco lleno sin error FTP
            # claro...). Comparar el tamaño final contra el local antes de
            # dar la subida por buena -- si el servidor no soporta SIZE, se
            # omite el chequeo en vez de fallar (igual que get_free_space).
            final_size = self.get_remote_size(remote_file)
            if final_size is not None and final_size != total:
                return False, (f"Subida incompleta: el servidor tiene {final_size} bytes, "
                                f"se esperaban {total} (posible corte o corrupción)")

            return True, remote_file
        except _Cancelled:
            try:
                self.ftp.abort()
            except Exception:
                self.ftp = None
            return False, "cancelado"
        except _Skipped:
            try:
                self.ftp.abort()
            except Exception:
                self.ftp = None
            return False, "saltado"
        except ftplib.error_perm as e:
            if "452" in str(e) or "quota" in str(e).lower() or "no space" in str(e).lower():
                return False, "disco_lleno"
            return False, f"Error FTP: {e}"
        except ftplib.all_errors as e:
            self.ftp = None  # Marcar como desconectado para forzar reconexión
            return False, f"Error FTP: {e}"
        except OSError as e:
            return False, f"Error de archivo: {e}"

    def upload_bytes(self, data: bytes, remote_path: str) -> tuple[bool, str]:
        """Sube un blob pequeño (p.ej. el JSON de favoritos compartidos)
        directamente desde memoria, sin pasar por un archivo local temporal
        -- storbinary acepta cualquier objeto tipo-archivo, y io.BytesIO
        cumple esa interfaz sobre bytes ya en memoria. Crea la carpeta
        remota si hace falta, igual que upload_file()."""
        if not self.is_connected():
            return False, "No conectado al servidor FTP."
        remote_dir = remote_path.rsplit("/", 1)[0] or "/"
        ok, msg = self.ensure_remote_dir(remote_dir)
        if not ok:
            return False, msg
        try:
            self.ftp.storbinary(f"STOR {remote_path}", io.BytesIO(data))
            return True, remote_path
        except ftplib.all_errors as e:
            return False, f"Error FTP: {e}"

    def download_bytes(self, remote_path: str) -> Optional[bytes]:
        """Descarga un blob pequeño a memoria -- None si no existe o falla
        (servidor sin ese archivo todavía, p.ej. la primera vez que se usan
        favoritos compartidos, se degrada a "sin favoritos" en vez de
        romper nada)."""
        if not self.is_connected():
            return None
        buf = io.BytesIO()
        try:
            self.ftp.retrbinary(f"RETR {remote_path}", buf.write)
            return buf.getvalue()
        except ftplib.all_errors:
            return None

    def rename_file(self, remote_from: str, remote_to: str) -> tuple[bool, str]:
        """Mueve/renombra un archivo YA en el servidor (RNFR/RNTO) -- mucho
        más barato que descargar+resubir+borrar cuando origen y destino
        están en el mismo disco/filesystem. En servidores/discos distintos
        esto normalmente falla (ver download_file() para el caso
        alternativo, que sí cruza discos)."""
        if not self.is_connected():
            return False, "No conectado al servidor FTP."
        remote_dir = remote_to.rsplit("/", 1)[0] or "/"
        ok, msg = self.ensure_remote_dir(remote_dir)
        if not ok:
            return False, msg
        try:
            self.ftp.rename(remote_from, remote_to)
            return True, remote_to
        except ftplib.all_errors as e:
            return False, f"Error FTP: {e}"

    def download_file(self, remote_file: str, local_path: str,
                       progress_cb: Optional[Callable[[int], None]] = None) -> tuple[bool, str]:
        """Descarga un archivo del servidor a disco local -- necesario para
        mover un archivo ya subido a un disco DISTINTO del servidor (RNFR/
        RNTO de rename_file() no cruza discos): se descarga aquí, se sube
        con upload_file() al nuevo destino, y solo entonces se borra el
        original."""
        if not self.is_connected():
            return False, "No conectado al servidor FTP."
        try:
            received = [0]
            def _write(chunk):
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                with open(local_path, "ab") as f:
                    f.write(chunk)
                received[0] += len(chunk)
                if progress_cb:
                    progress_cb(received[0])
            # Empezar en blanco: si ya existía un intento anterior a medias,
            # no se debe seguir escribiendo detrás de esos bytes.
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            open(local_path, "wb").close()
            self.ftp.retrbinary(f"RETR {remote_file}", _write)
            return True, local_path
        except ftplib.all_errors as e:
            return False, f"Error FTP: {e}"
        except OSError as e:
            return False, f"Error de archivo: {e}"

    def delete_file(self, remote_file: str, progress_cb: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        """Borra un archivo del servidor FTP. progress_cb: igual que en
        delete_folder_recursive -- se llama con la ruta justo antes de
        borrarla, para feedback visual en borrados largos."""
        if not self.ftp:
            return False, "No conectado"
        try:
            if progress_cb:
                progress_cb(remote_file)
            self.ftp.delete(remote_file)
            return True, remote_file
        except ftplib.all_errors as e:
            return False, str(e)

    def list_dir(self, path: str = "/") -> list[str]:
        """Lista el contenido de un directorio remoto."""
        if not self.is_connected():
            return []
        try:
            self.ftp.cwd(path)
            return self.ftp.nlst()
        except ftplib.all_errors:
            return []


def _ftp_safe(text: str) -> str:
    """Elimina caracteres problemáticos para rutas FTP/filesystem."""
    import re
    text = re.sub(r'[<>:"|?*]', "", text)
    return text.strip()


def _parse_unix_list_dirs(lines: list) -> list:
    """Extrae los nombres de carpeta de una salida LIST estilo Unix
    ('drwxr-xr-x ... nombre con espacios'). Ignora entradas que no sean directorio."""
    names = []
    for line in lines:
        if not line or line[0] != "d":
            continue
        parts = line.split(None, 8)
        if len(parts) == 9:
            name = parts[8]
            if name not in (".", ".."):
                names.append(name)
    return names


def _parse_unix_list_files(lines: list) -> list:
    """Igual que _parse_unix_list_dirs pero al revés: extrae los nombres de
    archivo ('-rw-r--r-- ... nombre con espacios'), ignorando directorios."""
    names = []
    for line in lines:
        if not line or line[0] != "-":
            continue
        parts = line.split(None, 8)
        if len(parts) == 9:
            names.append(parts[8])
    return names


def _parse_unix_list_files_with_sizes(lines: list) -> list[tuple[str, int]]:
    """Igual que _parse_unix_list_files, pero conservando el tamaño (el
    5º campo del formato Unix de LIST: permisos, enlaces, dueño, grupo,
    TAMAÑO, mes, día, hora/año, nombre)."""
    result = []
    for line in lines:
        if not line or line[0] != "-":
            continue
        parts = line.split(None, 8)
        if len(parts) == 9:
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            result.append((parts[8], size))
    return result


def _parse_recursive_list_sections(lines: list) -> "dict | None":
    """Parsea la salida de "LIST -R": una serie de secciones separadas
    por línea en blanco, cada una empezando con una línea "ruta:" seguida
    de líneas de listado Unix normales para esa carpeta -- el mismo
    formato que produce "ls -R". Devuelve {ruta: [(nombre, tamaño), ...],
    ...} o None si ninguna línea tiene forma de cabecera de sección (el
    servidor probablemente ignoró "-R" y devolvió un listado plano de la
    carpeta pedida nada más, no un árbol)."""
    sections: dict = {}
    current_path = None
    current_lines: list = []
    saw_header = False

    def _flush():
        nonlocal current_path, current_lines
        if current_path is not None:
            sections[current_path] = _parse_unix_list_files_with_sizes(current_lines)
        current_path, current_lines = None, []

    for line in lines:
        if not line.strip():
            _flush()
        elif line.endswith(":") and line[0] in ("/", ".") :
            _flush()
            current_path = line[:-1]
            saw_header = True
        else:
            current_lines.append(line)
    _flush()

    return sections if saw_header else None


def format_ftp_tree(root: str, tree: dict) -> str:
    """Texto indentado (para un CTkTextbox de solo lectura) del árbol que
    devuelve FTPClient.get_folder_tree(root) -- para el desplegable "ver
    árbol de archivos" de una serie en Episodios que faltan (ver
    gui/app.py::_toggle_missing_ep_ftp_tree). *root* mismo NO se imprime
    como carpeta (ya se ve en la ruta de arriba, ver _missing_ep_path_lbl)
    -- sus subcarpetas arrancan sin indentar, y sus archivos sueltos (si
    los hay) igual. Cada nivel de subcarpeta por debajo suma 2 espacios.
    "" si el árbol no tiene ningún archivo."""
    root = root.rstrip("/")

    def _fmt_size(nbytes: int) -> str:
        for unit, divisor in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
            if nbytes >= divisor:
                return f"{nbytes / divisor:.1f} {unit}"
        return f"{nbytes} B"

    def _depth(path: str) -> int:
        rel = path.rstrip("/")
        if rel == root:
            return 0
        return rel[len(root) + 1:].count("/") + 1

    lines = []
    for path in sorted(tree.keys(), key=lambda p: p.rstrip("/")):
        rel = path.rstrip("/")
        depth = _depth(path)
        if rel != root:
            name = rel.rsplit("/", 1)[-1]
            lines.append("  " * (depth - 1) + f"📁 {name}/")
        for name, size in sorted(tree[path]):
            lines.append("  " * depth + f"📄 {name}  ({_fmt_size(size)})")
    return "\n".join(lines)


def sizes_by_top_level_folder(tree: dict, root: str) -> dict:
    """A partir del árbol que devuelve list_tree_recursive(root), suma el
    tamaño de TODOS los archivos bajo cada carpeta de primer nivel
    (serie/película completa, con todas sus subcarpetas de temporada,
    extras, etc.) -- para la herramienta de liberar espacio, que necesita
    el tamaño total por serie/película, no por subcarpeta suelta.
    Devuelve {nombre_carpeta: tamaño_total_bytes, ...}."""
    root = root.rstrip("/")
    totals: dict = {}
    for path, files in tree.items():
        rel = path.rstrip("/")
        if rel == root or not rel.startswith(root + "/"):
            continue   # archivos sueltos en la raíz, no dentro de ninguna carpeta de primer nivel
        top_folder = rel[len(root) + 1:].split("/", 1)[0]
        totals[top_folder] = totals.get(top_folder, 0) + sum(size for _name, size in files)
    return totals


def files_by_top_level_folder(tree: dict, root: str) -> dict:
    """Como sizes_by_top_level_folder, pero devolviendo los NOMBRES de
    archivo (no el tamaño) de todos los archivos bajo cada carpeta de
    primer nivel -- para el detector de episodios que faltan, que cruza
    con el FTP real recorriendo los nombres con detect_episode() para
    sacar temporada/episodio, no necesita el tamaño.
    Devuelve {nombre_carpeta: [nombre_archivo, ...], ...}."""
    root = root.rstrip("/")
    result: dict = {}
    for path, files in tree.items():
        rel = path.rstrip("/")
        if rel == root or not rel.startswith(root + "/"):
            continue
        top_folder = rel[len(root) + 1:].split("/", 1)[0]
        result.setdefault(top_folder, []).extend(name for name, _size in files)
    return result
