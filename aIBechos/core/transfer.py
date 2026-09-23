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
