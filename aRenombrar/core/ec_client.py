"""Cliente del protocolo EC (External Connections) binario de aMule.

Motivo de existir: amulecmd recorta el nombre de cada resultado de búsqueda
a un ancho fijo (name_max=80) al imprimirlo (ShowResults en TextClient.cpp),
y esa columna truncada es lo único que se podía parsear desde la GUI. Este
módulo habla directamente el protocolo EC por TCP (el mismo que usa
amulecmd internamente) y lee el nombre COMPLETO del paquete binario
(EC_TAG_PARTFILE_NAME), sin el truncado del printf.

La implementación reproduce el wire format de aMule master
(src/libs/ec/cpp/{ECCodes.h, ECTag.h, ECTag.cpp, ECPacket.h}):
  * Cabecera de paquete: 8 bytes = flags (uint32 BE, siempre 0x20 salvo
    flags de capacidad que no anunciamos) + longitud (uint32 BE) del cuerpo
    (sin contar la cabecera).
  * Cuerpo: opcode (uint8) + número de tags hijos (uint16 BE) + tags.
  * Tag: TAGNAME (uint16 BE = (nombre << 1) | tiene_hijos) + TAGTYPE
    (uint8) + TAGLEN (uint32 BE: longitud de hijos + datos, excluye la
    cabecera del tag) + [TAGCOUNT uint16 BE + sub-tags] + datos.
  * Enteros: ancho más estrecho que admita el valor (UINT8/16/32/64) y en
    big-endian (CECTag::InitInt). Al leer hay que ser agnóstico al ancho.
  * Strings: UTF-8 terminadas en NUL (m_dataLen incluye el NUL).
  * Tag vacío de capacidad: type=CUSTOM(1), len=0 (CECEmptyTag).

Autenticación (CECLoginPacket / CECAuthPacket / ExternalConn.cpp):
  1. EC_OP_AUTH_REQ con EC_TAG_CLIENT_NAME/VERSION y
     EC_TAG_PROTOCOL_VERSION = 0x0204.
  2. El servidor responde EC_OP_AUTH_SALT con EC_TAG_PASSWD_SALT (uint64).
  3. salt_str = "%lX" % salt  (hex mayúscula SIN ceros a la izquierda,
     formato wxString CFormat -- verificado contra amuled real en Windows).
     salt_hash = MD5(salt_str) (hex).
     passwd_hash = MD5( md5(password_plano).hexdigest().lower() + salt_hash )
     (16 bytes crudos, se envían como tag EC_TAG_PASSWD_HASH type HASH16).
  4. EC_OP_AUTH_PASSWD -> EC_OP_AUTH_OK (o AUTH_FAIL con EC_TAG_STRING).

Búsqueda:
  * EC_OP_SEARCH_START con un tag EC_TAG_SEARCH_TYPE (0x0701) cuyo propio
    dato es el tipo de búsqueda (uint, 0=local 1=global 2=kad 3=web) y que
    lleva como hijos EC_TAG_SEARCH_NAME (string) y EC_TAG_SEARCH_FILE_TYPE
    (string, vacío = todos) -- igual que CEC_Search_Tag.
  * EC_OP_SEARCH_RESULTS devuelve un tag EC_TAG_SEARCHFILE (0x0700) por
    resultado; el dato del propio tag es el ECID (uint32, el "número" que
    amulecmd muestra), y sus hijos incluyen EC_TAG_PARTFILE_NAME (0x0301,
    nombre COMPLETO), EC_TAG_PARTFILE_SIZE_FULL (0x0303, bytes),
    EC_TAG_PARTFILE_SOURCE_COUNT (0x030A), SOURCE_COUNT_XFER (0x030D,
    completos) y STATUS (0x0308).
  * Descarga: EC_OP_DOWNLOAD_SEARCH_RESULT con un tag EC_TAG_PARTFILE
    (0x0300) cuyo dato es el hash MD4 (HASH16) y que lleva un hijo
    EC_TAG_PARTFILE_CAT (uint) -- igual que el `download` de amulecmd.

Se mantiene el mismo contrato de datos que core/amule_client.py
(AmuleSearchResult) para que la GUI no se entere de qué backend se usa.
"""

import hashlib
import binascii
import os
import re
import socket
import struct
import subprocess
import threading
import time as _time
from typing import List, Optional, Tuple

from core.amule_client import AmuleSearchResult

# ---- Opcodes (ECCodes.h) ----
EC_OP_AUTH_REQ = 0x02
EC_OP_AUTH_FAIL = 0x03
EC_OP_AUTH_OK = 0x04
EC_OP_FAILED = 0x05
EC_OP_STRINGS = 0x06
EC_OP_SEARCH_START = 0x26
EC_OP_SEARCH_RESULTS = 0x28
EC_OP_DOWNLOAD_SEARCH_RESULT = 0x2A
EC_OP_GET_DLOAD_QUEUE = 0x31
EC_OP_DLOAD_QUEUE = 0x32
EC_OP_PARTFILE_REMOVE = 0x2B
EC_OP_AUTH_SALT = 0x4F
EC_OP_AUTH_PASSWD = 0x50

# ---- Tags de autenticación ----
EC_TAG_PASSWD_HASH = 0x0001
EC_TAG_PROTOCOL_VERSION = 0x0002
EC_TAG_PASSWD_SALT = 0x000B
EC_TAG_CLIENT_NAME = 0x0100
EC_TAG_CLIENT_VERSION = 0x0101
EC_TAG_STRING = 0x0000
EC_TAG_SERVER_VERSION = 0x050B

# ---- Tags de archivos (EC_TAG_PARTFILE_*) ----
EC_TAG_PARTFILE = 0x0300
EC_TAG_PARTFILE_NAME = 0x0301
EC_TAG_PARTFILE_SIZE_FULL = 0x0303
EC_TAG_PARTFILE_SIZE_DONE = 0x0305
EC_TAG_PARTFILE_SIZE_XFER = 0x0306
EC_TAG_PARTFILE_SPEED = 0x0302
EC_TAG_PARTFILE_SOURCE_COUNT = 0x030A
EC_TAG_PARTFILE_SOURCE_COUNT_XFER = 0x030D
EC_TAG_PARTFILE_STATUS = 0x0308
EC_TAG_PARTFILE_PRIO = 0x0309
EC_TAG_PARTFILE_CAT = 0x030F
EC_TAG_PARTFILE_HASH = 0x031E

# ---- Tags de búsqueda ----
EC_TAG_SEARCHFILE = 0x0700
EC_TAG_SEARCH_TYPE = 0x0701
EC_TAG_SEARCH_NAME = 0x0702
EC_TAG_SEARCH_FILE_TYPE = 0x0705

EC_CURRENT_PROTOCOL_VERSION = 0x0204

EC_FLAG_BASE = 0x20
# Tipos (ECTagTypes.h)
EC_TAGTYPE_CUSTOM = 1
EC_TAGTYPE_UINT8 = 2
EC_TAGTYPE_UINT16 = 3
EC_TAGTYPE_UINT32 = 4
EC_TAGTYPE_UINT64 = 5
EC_TAGTYPE_STRING = 6
EC_TAGTYPE_HASH16 = 9

# Tipos de búsqueda (EC_SEARCH_TYPE en ECCodes.h)
EC_SEARCH_LOCAL = 0
EC_SEARCH_GLOBAL = 1
EC_SEARCH_KAD = 2
EC_SEARCH_WEB = 3

_SEARCH_TYPE_MAP = {
    "local": EC_SEARCH_LOCAL,
    "global": EC_SEARCH_GLOBAL,
    "kad": EC_SEARCH_KAD,
    "web": EC_SEARCH_WEB,
}

_CLIENT_NAME = "aIBechos"
_CLIENT_VERSION = "1.0"


class EcProtocolError(Exception):
    pass


class EcAuthError(EcProtocolError):
    pass


class EcConnectionError(EcProtocolError):
    pass


class EcSearchResultError(EcProtocolError):
    pass


def _pack_int(value: int) -> Tuple[int, bytes]:
    """Codifica un entero con el ancho más estrecho que quepa (BE)."""
    if value <= 0xFF:
        return EC_TAGTYPE_UINT8, struct.pack(">B", value)
    if value <= 0xFFFF:
        return EC_TAGTYPE_UINT16, struct.pack(">H", value)
    if value <= 0xFFFFFFFF:
        return EC_TAGTYPE_UINT32, struct.pack(">I", value)
    return EC_TAGTYPE_UINT64, struct.pack(">Q", value)


def _pack_string(text: str) -> bytes:
    return text.encode("utf-8") + b"\x00"


def _make_tag(name: int, tagtype: int, data: bytes, children: Optional[list] = None) -> bytes:
    child_bytes = b""
    if children:
        for child in children:
            child_bytes += child
    body = child_bytes + data
    wire_name = (name << 1) | (1 if children else 0)
    # taglen = bytes de los hijos (sin el campo de cuenta) + data
    result = struct.pack(">HBI", wire_name, tagtype, len(body))
    if children:
        result += struct.pack(">H", len(children))
    return result + body


def _make_int_tag(name: int, value: int) -> bytes:
    tagtype, data = _pack_int(value)
    return _make_tag(name, tagtype, data)


def _make_string_tag(name: int, text: str) -> bytes:
    return _make_tag(name, EC_TAGTYPE_STRING, _pack_string(text))


def _make_hash16_tag(name: int, raw_hash: bytes) -> bytes:
    return _make_tag(name, EC_TAGTYPE_HASH16, raw_hash)


class _Tag:
    __slots__ = ("name", "tagtype", "children", "data")

    def __init__(self, name, tagtype, children, data):
        self.name = name
        self.tagtype = tagtype
        self.children = children
        self.data = data

    def to_int(self) -> int:
        if self.tagtype == EC_TAGTYPE_UINT8:
            return self.data[0]
        if self.tagtype == EC_TAGTYPE_UINT16:
            return struct.unpack(">H", self.data)[0]
        if self.tagtype == EC_TAGTYPE_UINT32:
            return struct.unpack(">I", self.data)[0]
        if self.tagtype == EC_TAGTYPE_UINT64:
            return struct.unpack(">Q", self.data)[0]
        raise EcProtocolError(f"tag 0x{self.name:04X} no es entero (type={self.tagtype})")

    def to_str(self) -> str:
        if self.tagtype != EC_TAGTYPE_STRING:
            raise EcProtocolError(f"tag 0x{self.name:04X} no es string (type={self.tagtype})")
        return self.data.split(b"\x00", 1)[0].decode("utf-8", "replace")

    def get_child(self, name: int):
        for child in self.children:
            if child.name == name:
                return child
        return None


def _read_tag(data: bytes, pos: int) -> Tuple[_Tag, int]:
    start = pos
    wire_name = struct.unpack(">H", data[pos:pos + 2])[0]
    pos += 2
    name = wire_name >> 1
    has_children = wire_name & 1
    tagtype = data[pos]
    pos += 1
    taglen = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4
    children = []
    if has_children:
        count = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        for _ in range(count):
            child, pos = _read_tag(data, pos)
            children.append(child)
    # taglen cuenta hijos + data, pero NO el campo de cuenta (uint16), así
    # que con hijos el final se desplaza 2 bytes más allá de start+7+taglen.
    end = start + 2 + 1 + 4 + (2 if has_children else 0) + taglen
    tagdata = data[pos:end]
    return _Tag(name, tagtype, children, tagdata), end


def _parse_packet(payload: bytes) -> Tuple[int, List[_Tag]]:
    if len(payload) < 3:
        raise EcProtocolError("paquete EC demasiado corto")
    opcode = payload[0]
    count = struct.unpack(">H", payload[1:3])[0]
    pos = 3
    tags = []
    for _ in range(count):
        tag, pos = _read_tag(payload, pos)
        tags.append(tag)
    return opcode, tags


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EcConnectionError("conexión EC cerrada por el servidor")
        buf += chunk
    return buf


def _make_packet(opcode: int, tags: Optional[list] = None) -> bytes:
    body = struct.pack(">B", opcode) + struct.pack(">H", len(tags or []))
    for tag in tags or []:
        body += tag
    return struct.pack(">II", EC_FLAG_BASE, len(body)) + body


def _format_size(bytes_size: int) -> str:
    """Formatea bytes como "450,5 MB" (mismo estilo que el size_human que
    pinta amulecmd/la GUI; _parse_size_human de app.py entiende coma o
    punto decimal y sufijo KB/MB/GB/TB)."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    if bytes_size < 1024 ** 2:
        return f"{bytes_size / 1024:.1f} KB"
    if bytes_size < 1024 ** 3:
        return f"{bytes_size / 1024 ** 2:.1f} MB"
    return f"{bytes_size / 1024 ** 3:.2f} GB"


def _no_console_kwargs():
    """Kwargs para subprocess.run que oculta la ventana en Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


class EcClient:
    """Conexión directa por TCP al puerto EC de aMule.

    Uso típico:
        ec = EcClient(host, port, password)
        with ec.connect() as client:
            client.start_search(query, search_type="Kad")
            results = client.get_results()
            client.download(result)
    """

    def __init__(self, host: str, port: int, password: str,
                 timeout: float = 10.0, sock=None):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock = sock
        self._connected = False

    # ---- Conexión ----

    def connect(self):
        """Abre el socket y hace el handshake de autenticación. Devuelve
        self para poder usarlo como context manager."""
        try:
            sock = socket.create_connection((self.host, self.port),
                                            timeout=self.timeout)
        except OSError as exc:
            raise EcConnectionError(f"no se pudo conectar con aMule en "
                                    f"{self.host}:{self.port} ({exc})") from exc
        self._sock = sock
        self._connected = True
        try:
            self._authenticate()
        except Exception:
            self.close()
            raise
        return self

    def __enter__(self):
        if not self._connected or self._sock is None:
            return self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    def is_alive(self) -> bool:
        """Alias de is_connected para encajar en los puntos de integración
        de la GUI que llamaban AmuleSession.is_alive()."""
        return self.is_connected

    def test_connection(self) -> tuple:
        """Intenta conectar y autenticar (equivalente a
        AmuleClient.test_connection, que usaba amulecmd). Devuelve (bool,
        str). No deja la conexión abierta."""
        try:
            self.connect()
        except EcConnectionError as exc:
            return False, str(exc)
        except EcAuthError as exc:
            self.close()
            return False, str(exc)
        self.close()
        return True, "ok"

    # ---- Protocolo bajo nivel ----

    def _send_packet(self, opcode: int, tags: Optional[list] = None):
        if self._sock is None:
            raise EcConnectionError("no conectado")
        self._sock.sendall(_make_packet(opcode, tags))

    def _recv_packet(self) -> Tuple[int, List[_Tag]]:
        if self._sock is None:
            raise EcConnectionError("no conectado")
        header = _recv_exact(self._sock, 8)
        flags, length = struct.unpack(">II", header)
        if flags & 0x20 != 0x20:
            raise EcProtocolError(f"flags de paquete EC inesperados: 0x{flags:08X}")
        if length > 64 * 1024 * 1024:
            raise EcProtocolError(f"paquete EC demasiado grande: {length}")
        payload = _recv_exact(self._sock, length)
        return _parse_packet(payload)

    # ---- Autenticación ----

    def _authenticate(self):
        auth_tags = [
            _make_string_tag(EC_TAG_CLIENT_NAME, _CLIENT_NAME),
            _make_string_tag(EC_TAG_CLIENT_VERSION, _CLIENT_VERSION),
            _make_int_tag(EC_TAG_PROTOCOL_VERSION, EC_CURRENT_PROTOCOL_VERSION),
        ]
        self._send_packet(EC_OP_AUTH_REQ, auth_tags)
        opcode, tags = self._recv_packet()
        if opcode == EC_OP_AUTH_FAIL:
            raise EcAuthError(self._extract_error(tags) or "autenticación rechazada")
        if opcode != EC_OP_AUTH_SALT:
            raise EcProtocolError(f"respuesta EC inesperada al AUTH_REQ: 0x{opcode:02X}")

        salt = None
        for tag in tags:
            if tag.name == EC_TAG_PASSWD_SALT:
                salt = tag.to_int()
        if salt is None:
            raise EcProtocolError("AUTH_SALT sin EC_TAG_PASSWD_SALT")

        # amulecmd: -P toma la contraseña en plano y la convierte a MD5;
        # el servidor guarda ese mismo MD5 (hex minúscula) en sus prefs.
        pw_md5_hex = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        salt_str = "{:X}".format(salt)  # CFormat("%lX"): hex mayúscula sin padding
        salt_hash = hashlib.md5(salt_str.encode("ascii")).hexdigest()
        final = hashlib.md5((pw_md5_hex + salt_hash).encode("ascii")).digest()

        self._send_packet(EC_OP_AUTH_PASSWD,
                          [_make_hash16_tag(EC_TAG_PASSWD_HASH, final)])
        opcode, tags = self._recv_packet()
        if opcode == EC_OP_AUTH_FAIL:
            raise EcAuthError(self._extract_error(tags) or "contraseña incorrecta")
        if opcode != EC_OP_AUTH_OK:
            raise EcProtocolError(f"respuesta EC inesperada al AUTH_PASSWD: 0x{opcode:02X}")

    @staticmethod
    def _extract_error(tags: List[_Tag]) -> str:
        for tag in tags:
            if tag.name == EC_TAG_STRING and tag.tagtype == EC_TAGTYPE_STRING:
                return tag.to_str()
        return ""

    # ---- Búsqueda ----

    def start_search(self, query: str, search_type: str = "Kad",
                     file_type: str = ""):
        stype = _SEARCH_TYPE_MAP.get(search_type.lower())
        if stype is None:
            raise EcSearchResultError(f"tipo de búsqueda aMule no válido: {search_type!r}")
        search_tagtype, search_data = _pack_int(stype)
        search_tag = _make_tag(
            EC_TAG_SEARCH_TYPE, search_tagtype, search_data,
            children=[
                _make_string_tag(EC_TAG_SEARCH_NAME, query),
                _make_string_tag(EC_TAG_SEARCH_FILE_TYPE, file_type),
            ])
        self._send_packet(EC_OP_SEARCH_START, [search_tag])
        # El server responde SEARCH_START con EC_OP_STRINGS (búsqueda en
        # marcha) o EC_OP_FAILED (error); hay que consumirlo o quedaría en
        # el buffer y desincronizaría el siguiente SEARCH_RESULTS.
        opcode, tags = self._recv_packet()
        if opcode == EC_OP_AUTH_FAIL:
            raise EcAuthError(self._extract_error(tags) or "autenticación rechazada")
        if opcode == EC_OP_FAILED:
            raise EcSearchResultError(
                self._extract_error(tags) or "la búsqueda no pudo comenzar")

    def get_results(self) -> List[AmuleSearchResult]:
        """Pide EC_OP_SEARCH_RESULTS y devuelve la lista actual acumulada
        por aMule (igual que el "results" de amulecmd)."""
        self._send_packet(EC_OP_SEARCH_RESULTS)
        opcode, tags = self._recv_packet()
        if opcode == EC_OP_AUTH_FAIL:
            raise EcAuthError(self._extract_error(tags) or "autenticación rechazada")
        if opcode != EC_OP_SEARCH_RESULTS:
            raise EcProtocolError(f"respuesta EC inesperada a SEARCH_RESULTS: 0x{opcode:02X}")
        results = []
        for tag in tags:
            if tag.name != EC_TAG_SEARCHFILE:
                continue
            parsed = self._parse_search_file(tag)
            if parsed is not None:
                results.append(parsed)
        return results

    def iter_search(self, query: str, search_type: str = "Kad",
                    file_type: str = "", poll_interval: float = 3.0,
                    max_duration: float = 60.0, wake_event: "threading.Event | None" = None):
        """Lanza la búsqueda y va entregando la lista acumulada cada
        poll_interval segundos (generador), igual que el viejo
        AmuleClient.iter_search_in_session (amulecmd).

        *wake_event*: si se pasa, la espera entre sondeos usa
        event.wait(poll_interval) en vez de sleep, de modo que quien llame
        puede interrumpir la pausa (set()) para procesar algo sobre la MISMA
        conexión EC (p.ej. una descarga por hash) sin esperar el intervalo."""
        if wake_event is not None and not isinstance(wake_event, threading.Event):
            raise TypeError("wake_event debe ser un threading.Event")
        self.start_search(query, search_type=search_type, file_type=file_type)
        deadline = _time.monotonic() + max_duration
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return
            if wake_event is not None:
                wake_event.wait(min(poll_interval, remaining))
                wake_event.clear()
            else:
                _time.sleep(min(poll_interval, remaining))
            yield self.get_results()

    # ---- Parseo de resultados ----

    @staticmethod
    def _parse_search_file(tag: _Tag) -> Optional[AmuleSearchResult]:
        try:
            number = tag.to_int()
        except EcProtocolError:
            number = 0
        name_tag = tag.get_child(EC_TAG_PARTFILE_NAME)
        if name_tag is None:
            return None
        name = name_tag.to_str()
        if not name:
            return None
        size_tag = tag.get_child(EC_TAG_PARTFILE_SIZE_FULL)
        size_bytes = size_tag.to_int() if size_tag is not None else 0
        sources_tag = tag.get_child(EC_TAG_PARTFILE_SOURCE_COUNT)
        sources = sources_tag.to_int() if sources_tag is not None else 0
        complete_tag = tag.get_child(EC_TAG_PARTFILE_SOURCE_COUNT_XFER)
        complete = complete_tag.to_int() if complete_tag is not None else 0
        result = AmuleSearchResult(
            number=number,
            name=name,
            size_human=_format_size(size_bytes),
            sources=sources,
            complete=complete > 0,
        )
        # Guardamos el hash MD4 (EC_TAG_PARTFILE_HASH) sobre el objeto para
        # poder lanzar la descarga por hash sin re-buscar. No forma parte de
        # AmuleSearchResult (lo usa el backend, no la GUI).
        hash_tag = tag.get_child(EC_TAG_PARTFILE_HASH)
        if hash_tag is not None:
            result._ec_hash = hash_tag.data
        return result

    # ---- Descarga ----

    def download(self, result: AmuleSearchResult) -> Tuple[bool, str]:
        """Lanza la descarga de un resultado por su hash MD4.

        El server responde EC_OP_STRINGS confirmando la orden
        (Get_EC_Response_Search_Results_Download devuelve un paquete
        EC_OP_STRINGS, no es fire-and-forget); hay que consumirlo para no
        desincronizar la conexión. Si el resultado no conserva el hash EC
        (por ejemplo porque viene del parser de amulecmd) se devuelve
        (False, ...)."""
        raw_hash = getattr(result, "_ec_hash", None)
        if not raw_hash:
            return False, "este resultado no trae el hash MD4 (solo disponible vía EC)"
        partfile = _make_tag(
            EC_TAG_PARTFILE, EC_TAGTYPE_HASH16, raw_hash,
            children=[_make_int_tag(EC_TAG_PARTFILE_CAT, 0)])
        self._send_packet(EC_OP_DOWNLOAD_SEARCH_RESULT, [partfile])
        opcode, tags = self._recv_packet()
        if opcode == EC_OP_AUTH_FAIL:
            return False, self._extract_error(tags) or "autenticación rechazada"
        if opcode == EC_OP_FAILED:
            return False, self._extract_error(tags) or "aMule rechazó la descarga"
        if opcode == EC_OP_STRINGS:
            return True, ""
        return True, ""

    def get_download_queue(self) -> list[dict]:
        """Lista de descargas en cola de aMule vía EC.

        Cada elemento: {hash_hex, name, size_done, size_full, percent, speed, sources, status}
        Si el servidor no soporta la operación o no hay cola, devuelve [] sin lanzar.
        """
        def _get_download_queue_via_amulecmd(host="localhost", port=4712, password="", timeout=5) -> list[dict]:
            """Fallback: lee "show DL" desde amulecmd para obtener descargas activas.

            Si amulecmd no está disponible o falla, devuelve lista vacía.
            """
            # Detectar amulecmd
            from core.amule_client import _find_amulecmd
            exe = _find_amulecmd("")
            if not exe:
                return []

            args = [exe, "-c", "show DL"]
            if host:
                args.extend(["-h", host])
            if port:
                args.extend(["-p", str(port)])
            if password:
                args.extend(["-P", password])

            def _find_temp_dir() -> str:
                try:
                    if os.name == "nt":
                        conf_path = os.path.join(os.environ.get("APPDATA", ""),
                                                 "aMule", "aMule.conf")
                    else:
                        conf_path = os.path.join(os.path.expanduser("~"),
                                                 ".aMule", "aMule.conf")
                    if os.path.isfile(conf_path):
                        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
                            for line in f:
                                line=line.strip()
                                if line.startswith("TempDir="):
                                    return line[len("TempDir="):].replace("\\\\", "\\")
                except Exception:
                    pass
                return ""

            try:
                result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                        **_no_console_kwargs())
                combined = (result.stdout or "") + (result.stderr or "")
                if not combined.strip():
                    return []
                # Formato REAL de "show DL" (verificado contra amulecmd 3.0.1)
                # cada descarga ocupa DOS líneas:
                #   > <HASH32> <nombre del archivo>
                #   >     [<pct>%]  <sources>/<total> ... - <estado> - <xx>.part.met - <prio> [- <vel>]
                # La velocidad solo aparece cuando está descargando; Waiting no la trae.
                # El segundo número puede venir como "+01" o "(01)".
                lines = combined.splitlines()
                out = []
                i, n = 0, len(lines)
                while i < n:
                    raw = lines[i].lstrip()
                    m = re.match(r'>\s+([0-9A-Fa-f]{32})\s+(.+)', raw)
                    if not m:
                        i += 1
                        continue
                    hhex = m.group(1).lower()
                    name = m.group(2).strip()
                    percent = 0.0
                    sources = 0
                    status = 2          # código PartFileStatus: 2=descargando
                    speed = 0
                    part_met = ""
                    det = ""
                    if i + 1 < n:
                        det = lines[i + 1].lstrip()
                        # solo tratar la siguiente línea si es detalle de descarga
                        if det.startswith(">") and "[" in det and "%" in det:
                            # percent
                            pm = re.search(r'\[([\d.,]+)\s*%\]', det)
                            if pm:
                                try:
                                    percent = float(pm.group(1).replace(",", "."))
                                except ValueError:
                                    percent = 0.0
                            # sources = primer número antes de "/"
                            sm = re.search(r'\]\s+(\d+)\s*/', det)
                            if sm:
                                try:
                                    sources = int(sm.group(1))
                                except ValueError:
                                    sources = 0
                            # status: texto entre los primeros dos " - "
                            # ej: " - Waiting - 004.part.met - "
                            parts = det.split(" - ")
                            if len(parts) >= 2:
                                sc = parts[1].strip().lower()
                                for kw, code in (("complet", 4), ("pause", 1),
                                                 ("wait", 0), ("hash", 6),
                                                 ("morph", 3), ("alloc", 3),
                                                 ("stop", 7)):
                                    if kw in sc:
                                        status = code
                                        break
                            # part file
                            pm2 = re.search(r'(\d+\.part\.met)', det)
                            if pm2:
                                part_met = pm2.group(1)
                            # speed opcional al final: "12.40 kB/s" o "1.5 MB/s"
                            spm = re.search(r'(\d+(?:[.,]\d+)?)\s*([KMGT]?B)/s\s*$', det, re.IGNORECASE)
                            if spm:
                                try:
                                    sn = float(spm.group(1).replace(",", "."))
                                except ValueError:
                                    sn = 0.0
                                unit = spm.group(2).upper()
                                if unit.startswith("G"):
                                    speed = int(sn * 1024 * 1024 * 1024)
                                elif unit.startswith("M"):
                                    speed = int(sn * 1024 * 1024)
                                elif unit.startswith("K"):
                                    speed = int(sn * 1024)
                                else:
                                    speed = int(sn)
                            i += 2
                        else:
                            i += 1
                    else:
                        i += 1
                    # Enriquecer tamaños desde el .part en Temp si es posible.
                    # aMule pre-aloca el .part al tamaño completo, así que su
                    # tamaño en disco ≈ size_full; size_done ≈ percent*size_full.
                    size_full = 0
                    size_done = 0
                    if part_met:
                        try:
                            td = _find_temp_dir()
                            if td:
                                part_path = os.path.join(td, part_met.replace(".met", ""))
                                if os.path.isfile(part_path):
                                    size_full = os.path.getsize(part_path)
                                    # Si está completando (100%), size_done = size_full
                                    size_done = int(size_full * percent / 100.0) if percent else 0
                                    # Para 0% pero archivo pre-alocado grande, done sería 0; ok
                        except Exception:
                            pass
                    out.append({
                        "hash_hex": hhex,
                        "name": name,
                        "size_done": size_done,
                        "size_full": size_full,
                        "percent": percent,
                        "speed": speed,
                        "sources": sources,
                        "status": status,
                        "part_met": part_met,
                    })
                return out
            except Exception:
                return []

        try:
            self._send_packet(EC_OP_GET_DLOAD_QUEUE, [])
            opcode, tags = self._recv_packet()
        except Exception:
            # Fallo de red/conexión EC: intentar amulecmd como respaldo
            try:
                return _get_download_queue_via_amulecmd(self.host, self.port, self.password)
            except Exception:
                return []
        if opcode == EC_OP_FAILED:
            err = self._extract_error(tags) or ""
            # Si el servidor EC no soporta la operación (p.ej. "Unknown command"),
            # no lo tratamos como un fallo real: usamos el fallback amulecmd.
            if err and any(kw in err.lower() for kw in ("unknown", "unsupported", "not implemented", "command")):
                try:
                    return _get_download_queue_via_amulecmd(self.host, self.port, self.password)
                except Exception:
                    return []
            try:
                return _get_download_queue_via_amulecmd(self.host, self.port, self.password)
            except Exception:
                return []
        if opcode not in (EC_OP_DLOAD_QUEUE, EC_OP_STRINGS):
            # Si el servidor responde otro opcode (ej: status reply), intentar amulecmd
            try:
                return _get_download_queue_via_amulecmd(self.host, self.port, self.password)
            except Exception:
                return []
        try:
            out = []
            for t in tags:
                # Aceptar cualquier tag que traiga un nombre de partfile (robusto ante
                # variaciones de versión de aMule: PARTFILE 0x0300, KNOWNFILE 0x0400, etc.)
                if t.get_child(EC_TAG_PARTFILE_NAME) is None and getattr(t, "name", None) not in (EC_TAG_PARTFILE, 0x0400, 0x0401):
                    continue
                h_tag = t.get_child(EC_TAG_PARTFILE_HASH)
                raw = h_tag.data if h_tag and h_tag.tagtype == EC_TAGTYPE_HASH16 else None
                if not raw or not isinstance(raw, (bytes, bytearray)) or len(raw) != 16:
                    # Algunos servidores mandan el hash como dato del propio tag PARTFILE
                    if t.tagtype == EC_TAGTYPE_HASH16 and len(t.data) == 16:
                        raw = t.data
                    else:
                        # Buscar hash en cualquier hijo tipo HASH16
                        for c in getattr(t, "children", []):
                            if c.tagtype == EC_TAGTYPE_HASH16 and len(c.data) == 16:
                                raw = c.data
                                break
                        if not raw or len(raw) != 16:
                            continue
                hhex = binascii.hexlify(raw).decode("ascii").lower()
                name_t = t.get_child(EC_TAG_PARTFILE_NAME)
                name = name_t.to_str() if name_t else ""
                sd = t.get_child(EC_TAG_PARTFILE_SIZE_DONE)
                sf = t.get_child(EC_TAG_PARTFILE_SIZE_FULL)
                try:
                    size_done = sd.to_int() if sd else 0
                except Exception:
                    size_done = 0
                try:
                    size_full = sf.to_int() if sf else 0
                except Exception:
                    size_full = 0
                pct = (size_done / size_full * 100.0) if size_full else 0.0
                sp_t = t.get_child(EC_TAG_PARTFILE_SPEED)
                try:
                    speed = sp_t.to_int() if sp_t else 0
                except Exception:
                    speed = 0
                sc_t = t.get_child(EC_TAG_PARTFILE_SOURCE_COUNT)
                try:
                    sources = sc_t.to_int() if sc_t else 0
                except Exception:
                    sources = 0
                st_t = t.get_child(EC_TAG_PARTFILE_STATUS)
                try:
                    status = st_t.to_int() if st_t else 0
                except Exception:
                    status = 0
                out.append({
                    "hash_hex": hhex,
                    "name": name,
                    "size_done": size_done,
                    "size_full": size_full,
                    "percent": pct,
                    "speed": speed,
                    "sources": sources,
                    "status": status,
                    "raw_hash": raw,
                })
            # Si EC no mandó tamaños (común en esta versión de aMule),
            # enriquecer desde los .part en Temp mapeando por hash.
            try:
                if any(d.get("size_full", 0) == 0 for d in out):
                    if os.name == "nt":
                        conf_path = os.path.join(os.environ.get("APPDATA", ""),
                                                 "aMule", "aMule.conf")
                    else:
                        conf_path = os.path.join(os.path.expanduser("~"),
                                                 ".aMule", "aMule.conf")
                    td = ""
                    if os.path.isfile(conf_path):
                        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
                            for line in f:
                                line=line.strip()
                                if line.startswith("TempDir="):
                                    td = line[len("TempDir="):].replace("\\\\", "\\")
                                    break
                    if td and os.path.isdir(td):
                        # mapear hash -> part_path vía .part.met
                        h2part = {}
                        for fn in os.listdir(td):
                            if not fn.lower().endswith(".part.met"):
                                continue
                            fp = os.path.join(td, fn)
                            try:
                                with open(fp, "rb") as fh:
                                    data = fh.read(21)
                                if len(data) >= 21:
                                    hh = binascii.hexlify(data[5:21]).decode("ascii").lower()
                                    pp = os.path.join(td, fn[:-4])  # .part
                                    if os.path.isfile(pp):
                                        h2part[hh] = pp
                            except Exception:
                                continue
                        for d in out:
                            if d.get("size_full", 0) == 0:
                                pp = h2part.get(d.get("hash_hex","").lower())
                                if pp:
                                    try:
                                        sf = os.path.getsize(pp)
                                        d["size_full"] = sf
                                        pct = d.get("percent", 0) or 0
                                        d["size_done"] = int(sf * pct / 100.0) if pct else 0
                                        # si EC no mandó percent, derivarlo de tamaños
                                        if not pct and sf:
                                            d["percent"] = (d["size_done"]/sf*100.0) if sf else 0
                                    except Exception:
                                        pass
            except Exception:
                pass
            return out
        except Exception:
            return []

    def cancel_download(self, hash_hex: str) -> Tuple[bool, str]:
        """Cancela/elimina una descarga por hash MD4 hex (32 chars). Usa
        amulecmd como vía principal (fiable en aMule 3.0.1; EC_OP_GET_DLOAD_QUEUE
        ya falla con 'Servidor no añadido' y EC_OP_PARTFILE_REMOVE devuelve
        falsos positivos). EC queda como fallback secundario."""
        if not hash_hex or len(hash_hex) != 32:
            return False, "hash inválido"
        try:
            raw = binascii.unhexlify(hash_hex)
        except Exception:
            return False, "hash no es hex válido"
        # 1) Intentar por amulecmd primero (es el que devuelve 'Operation was successful' de forma fiable)
        try:
            from core.amule_client import _find_amulecmd
            exe = _find_amulecmd("")
            if exe:
                args = [exe, "-c", f"cancel {hash_hex}", "-h", self.host, "-p", str(self.port), "-P", self.password]
                result = subprocess.run(args, capture_output=True, text=True, timeout=10, **_no_console_kwargs())
                combined = (result.stdout or "") + (result.stderr or "")
                low = combined.lower()
                if "operation was successful" in low:
                    return True, ""
                # hash no encontrado o error explícito -> no considerar éxito, probar EC
                if "error" in low or "failed" in low or "not found" in low or "no such" in low:
                    pass
                elif "processing by hash" in low and "operation was successful" not in low:
                    # amulecmd dice 'Processing' pero sin éxito -> tratar como no encontrado
                    pass
        except Exception:
            pass
        # 2) Fallback EC (por si amulecmd no está disponible)
        try:
            partfile = _make_tag(EC_TAG_PARTFILE, EC_TAGTYPE_HASH16, raw)
            self._send_packet(EC_OP_PARTFILE_REMOVE, [partfile])
            opcode, tags = self._recv_packet()
            if opcode == EC_OP_FAILED:
                return False, self._extract_error(tags) or "aMule rechazó el borrado"
            # Cualquier otro opcode sin error se considera éxito (EC a veces devuelve 0x01)
            err = self._extract_error(tags)
            if err:
                return False, err
            return True, ""
        except Exception as e:
            return False, str(e)


# Re-export para los tests y para quien quiera el tipo sin importar amule_client
__all__ = [
    "EcClient", "EcProtocolError", "EcAuthError", "EcConnectionError",
    "EcSearchResultError", "AmuleSearchResult",
]
