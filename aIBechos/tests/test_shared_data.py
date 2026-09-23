"""Nombres de los archivos que el grupo comparte en el servidor.

El nombre de la aplicación va delante de cada uno, así que al renombrarla hay
que renombrarlos. Estaban escritos a mano en doce sitios distintos: renombrar
once y olvidar uno habría dejado la mitad de los datos del grupo en un sitio y
la otra mitad en otro, sin ningún error a la vista.
"""

from core.appdirs import APP_NAME, LEGACY_APP_NAME
from core import shared_data


def test_estan_los_trece_archivos_compartidos():
    """Si alguno se pierde por el camino, esos datos dejan de compartirse."""
    assert len(shared_data.SHARED_DATA_FILES) == 13
    assert shared_data.filename("auto_series") == f"{APP_NAME}_auto_series.json"


def test_el_nombre_lleva_delante_el_de_la_aplicacion():
    assert shared_data.filename("favoritos") == f"{APP_NAME}_favoritos.json"
    assert shared_data.filename("episodios_que_faltan") == \
        f"{APP_NAME}_episodios_que_faltan.json"


def test_se_puede_pedir_el_nombre_anterior_para_traer_los_datos():
    assert shared_data.filename("reservas", legacy=True) == \
        f"{LEGACY_APP_NAME}_reservas.json"


def test_ningun_nombre_se_repite():
    nombres = [shared_data.filename(k) for k in shared_data.SHARED_DATA_FILES]
    assert len(set(nombres)) == len(nombres)


def test_la_carpeta_se_renombra_cuando_se_llama_como_la_aplicacion():
    """El caso real: /datos2/aRenombrar -> /datos2/aIBechos."""
    assert shared_data.compute_new_folder(f"/datos2/{LEGACY_APP_NAME}") == \
        f"/datos2/{APP_NAME}"


def test_la_barra_final_no_estorba():
    assert shared_data.compute_new_folder(f"/datos2/{LEGACY_APP_NAME}/") == \
        f"/datos2/{APP_NAME}"


def test_una_carpeta_con_nombre_propio_no_se_toca():
    """Reemplazar a ciegas dentro de una ruta escrita a mano podría acertar por
    casualidad en mitad de otro nombre y mandar los datos del grupo a una
    carpeta que no existe."""
    assert shared_data.compute_new_folder("/datos2/compartido") == "/datos2/compartido"
    assert shared_data.compute_new_folder("/mnt/aRenombrar_backups") == \
        "/mnt/aRenombrar_backups"


def test_sin_carpeta_configurada_no_hay_nada_que_calcular():
    assert shared_data.compute_new_folder("") == ""
    assert shared_data.compute_new_folder(None) == ""


class _FakeFtp:
    """Doble mínimo para read_shared_json: solo download_bytes/file_exists."""

    def __init__(self, blobs=None, fail_read=False):
        # blobs: {ruta: bytes} ; fail_read simula un fallo transitorio de red
        self.blobs = dict(blobs or {})
        self.fail_read = fail_read

    def download_bytes(self, path):
        if self.fail_read:
            return None
        return self.blobs.get(path)

    def file_exists(self, path):
        return path in self.blobs


def test_leer_json_valido_devuelve_datos():
    ftp = _FakeFtp({"/datos/a.json": b'{"jose": 1}'})
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json")
    assert data == {"jose": 1}
    assert is_new is False


def test_archivo_inexistente_devuelve_base_vacia_para_crear():
    ftp = _FakeFtp({})
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json")
    assert data == {}
    assert is_new is True
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json", kind="list")
    assert data == []
    assert is_new is True


def test_fallo_transitorio_con_archivo_existente_no_toca():
    """El bug que vaciaba las estadísticas: download_bytes devuelve None
    tanto si no existe como si falla la red. Si el archivo EXISTE, hay que
    abortar (None) en vez de reescribirlo con una base vacía."""
    ftp = _FakeFtp({"/datos/a.json": b'{"jose": 1}'}, fail_read=True)
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json")
    assert data is None
    assert is_new is False


def test_archivo_corrupto_existente_no_toca():
    ftp = _FakeFtp({"/datos/a.json": b"{esto no es json"})
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json")
    assert data is None
    assert is_new is False


def test_forma_inesperada_no_toca():
    ftp = _FakeFtp({"/datos/a.json": b"[1, 2]"})
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json")
    assert data is None
    assert is_new is False
    ftp = _FakeFtp({"/datos/a.json": b'{"a": 1}'})
    data, is_new = shared_data.read_shared_json(ftp, "/datos/a.json", kind="list")
    assert data is None
    assert is_new is False
