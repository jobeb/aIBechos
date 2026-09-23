"""Cómo se llaman los archivos que TODOS los usuarios comparten en el servidor.

Viven en la carpeta que cada uno configura en Ajustes ("Carpeta compartida
(datos)", `shared_data_ftp_path`), que no es exclusiva de esta aplicación: por
eso los archivos llevan delante el nombre de la app, para no chocar con nada más
que haya ahí.

El nombre estaba escrito a mano en los doce sitios de `gui/app.py` que
construyen esas rutas. Al cambiar el nombre de la aplicación eso era una trampa:
renombrar once y olvidar uno habría dejado a la mitad de los datos del grupo en
un sitio y a la otra mitad en otro, sin ningún error a la vista. Ahora el nombre
está aquí y solo aquí.
"""

import json

from core.applog import get_logger
from core.appdirs import APP_NAME, LEGACY_APP_NAME

_log = get_logger("aIBechos.shared", "app.log")

#: Cada archivo compartido, por su nombre corto (sin prefijo ni extensión).
#: La clave es la que se usa en el código; el valor, el nombre real en el
#: servidor -- se conservan tal cual estaban para no romper nada al renombrar.
SHARED_DATA_FILES = {
    "favoritos":                        "favoritos.json",
    "reservas":                         "reservas.json",
    "config_servidor":                  "config_servidor.json",
    "doblaje_ia":                       "doblaje_ia.json",
    "actividad":                        "actividad.json",
    "estadisticas_usuarios":            "estadisticas_usuarios.json",
    "estadisticas_categorias":          "estadisticas_categorias.json",
    "estadisticas_borrados":            "estadisticas_borrados.json",
    "estadisticas_subidores_categoria": "estadisticas_subidores_categoria.json",
    "liberar_espacio":                  "liberar_espacio.json",
    "episodios_que_faltan":             "episodios_que_faltan.json",
    "peliculas":                        "peliculas.json",
    "auto_series":                      "auto_series.json",
}


def filename(key: str, legacy: bool = False) -> str:
    """Nombre del archivo compartido *key* en el servidor.

    Con *legacy* se obtiene el nombre que tenía cuando la aplicación se llamaba
    de otra forma, que es lo que hay que leer para traerse los datos ya
    existentes (ver la migración en gui/app.py)."""
    prefijo = LEGACY_APP_NAME if legacy else APP_NAME
    return f"{prefijo}_{SHARED_DATA_FILES[key]}"


def read_shared_json(ftp_client, remote_path: str, kind: str = "dict"):
    """Lee un JSON compartido del FTP sin riesgo de borrarlo.

    Devuelve (datos, es_nuevo):
    - (datos, False): contenido válido del tipo pedido; fusionar sobre esto.
    - ({}, True) / ([], True): el archivo NO existe; crearlo desde cero es seguro.
    - (None, False): NO ESCRIBIR nada: el archivo existe pero no se pudo leer
      o parsear (fallo transitorio de red o corrupción). Reescribirlo con una
      base vacía BORRARÍA todo lo que otros clientes sumaron -- ese era el bug
      que vaciaba las estadísticas cada cierto tiempo (download_bytes devuelve
      None tanto si no existe como si falla, y los pushes lo trataban igual).

    Si el archivo existe pero está corrupto, se avisa en el log: hay que
    borrarlo a mano del servidor para que se regenere solo.
    """
    if kind not in ("dict", "list"):
        raise ValueError(f"kind debe ser 'dict' o 'list', no {kind!r}")
    try:
        raw = ftp_client.download_bytes(remote_path)
    except Exception as e:
        _log.warning("Compartido: fallo leyendo %s, no se toca el remoto: %s",
                     remote_path, e)
        return None, False
    if not raw:
        try:
            exists = ftp_client.file_exists(remote_path)
        except Exception:
            exists = False
        if exists:
            _log.warning("Compartido: %s existe pero no se pudo leer, "
                         "no se toca el remoto", remote_path)
            return None, False
        return ({} if kind == "dict" else []), True
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        _log.warning("Compartido: %s corrupto en el servidor, no se toca el "
                     "remoto (bórrelo a mano para regenerarlo)", remote_path)
        return None, False
    if kind == "dict" and not isinstance(data, dict):
        _log.warning("Compartido: %s con forma inesperada, no se toca el remoto",
                     remote_path)
        return None, False
    if kind == "list" and not isinstance(data, list):
        _log.warning("Compartido: %s con forma inesperada, no se toca el remoto",
                     remote_path)
        return None, False
    return data, False


def compute_new_folder(old_folder: str) -> str:
    """La carpeta compartida que corresponde ahora a *old_folder*.

    Solo se renombra si la carpeta se llamaba EXACTAMENTE como la aplicación
    (el caso real: "/datos2/aRenombrar" -> "/datos2/aIBechos"). Si el usuario le
    puso cualquier otro nombre, se respeta y solo cambia el prefijo de los
    archivos de dentro: buscar y reemplazar a ciegas dentro de una ruta escrita
    a mano podría acertar por casualidad en mitad de otro nombre y mandar los
    datos del grupo a una carpeta que no existe."""
    limpia = (old_folder or "").rstrip("/")
    if not limpia:
        return ""
    padre, _, ultimo = limpia.rpartition("/")
    if ultimo != LEGACY_APP_NAME:
        return limpia
    return f"{padre}/{APP_NAME}" if padre else APP_NAME
