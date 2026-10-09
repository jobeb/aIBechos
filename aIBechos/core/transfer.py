"""De dónde sale el cliente de transferencia (FTP o SFTP).

Un único sitio que decide, para que añadir o cambiar un protocolo no obligue a
tocar los cincuenta puntos de la aplicación que abren una conexión propia.
Ambos clientes exponen la misma interfaz (SFTPClient hereda de FTPClient), así
que quien recibe uno no necesita saber cuál le ha tocado."""

from core.ftp_client import FTPClient

#: Puerto por defecto de cada protocolo, para proponerlo al cambiar de uno a
#: otro en Ajustes (el 21 de FTP no vale para SFTP y viceversa).
DEFAULT_PORTS = {"ftp": 21, "sftp": 22}


def make_client(protocol: str = "ftp") -> FTPClient:
    """Cliente listo para conectar, según el protocolo configurado."""
    if str(protocol or "").strip().lower() == "sftp":
        from core.sftp_client import SFTPClient   # importa paramiko, ver ahí
        return SFTPClient()
    return FTPClient()


def default_port(protocol: str) -> int:
    return DEFAULT_PORTS.get(str(protocol or "").strip().lower(), 21)


#: Ayuda de "Conexiones por archivo" (solo SFTP) en Ajustes.
TOOLTIP_CONEXIONES_POR_ARCHIVO = (
    "El servidor limita cada conexión por separado, así que un archivo solo no "
    "llega al límite de velocidad por muy alto que esté. Repartirlo entre varias "
    "lo acelera: medido contra el servidor, 1 conexión da 2 MB/s y 4 dan 6,5.\n"
    "Es un total: si se suben varios archivos a la vez, se reparte entre ellos."
)

#: Cómo se llama cada protocolo en la pestaña de conexión. FTPS es FTP con
#: TLS (mismo protocolo, cifrado); SFTP es SSH y no tiene nada que ver.
PROTOCOL_LABELS = {
    "ftp":  "FTP",
    "ftps": "FTPS (FTP con TLS)",
    "sftp": "SFTP (SSH)",
}
