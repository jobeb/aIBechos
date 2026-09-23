"""Saber si un archivo está también en el servidor, con un historial a medias.

Contexto: hasta que se corrigió el emisor, las subidas del modo automático
guardaban la ruta LOCAL en el campo "remote" de upload_history.json -- 241 de
500 registros en una instalación real. El diálogo de la ✕ necesita ese dato
para decidir si ofrece "borrar también del servidor", así que hay que tratar
con los registros viejos que están mal.
"""

import pytest

from core.remote_presence import (QUITAR_LISTA, QUITAR_LOCAL_Y_REMOTO, QUITAR_Y_LOCAL,
                                   is_destructive, join_remote, looks_like_remote_path,
                                   remote_path_from_history, removal_options,
                                   resolve_remote_path, was_uploaded_according_to_history)

LOCAL = r"C:\Users\Jose\Downloads\eMule\Incoming\Peli.2024.mkv"

BUENA = {
    "status": "ok",
    "filename": "Peli (2024).mkv",
    "local_path": LOCAL,
    "remote": "/datos2/peliculas/Peli (2024)/Peli (2024).mkv",
}

# Registro con el campo estropeado: "remote" repite la ruta local.
ESTROPEADA = {
    "status": "ok",
    "filename": "Peli (2024).mkv",
    "local_path": LOCAL,
    "remote": LOCAL,
}


# ── Distinguir una ruta de servidor de una local ─────────────────────────

@pytest.mark.parametrize("valor", [
    "/datos2/series/Andor/Andor 1x01.mkv",
    "/datos2/peliculas",
])
def test_reconoce_rutas_de_servidor(valor):
    assert looks_like_remote_path(valor)


@pytest.mark.parametrize("valor", [
    r"C:\Users\Jose\Downloads\peli.mkv",
    r"D:\medios\serie.mkv",
    "relativa/sin/barra/inicial.mkv",
    "",
    None,
])
def test_rechaza_lo_que_no_es_ruta_de_servidor(valor):
    assert not looks_like_remote_path(valor)


# ── Lectura del historial ────────────────────────────────────────────────

def test_devuelve_la_ruta_cuando_el_registro_es_bueno():
    assert remote_path_from_history([BUENA], LOCAL) == BUENA["remote"]


def test_no_devuelve_nada_cuando_el_registro_esta_estropeado():
    """Lo importante: no confundir la ruta local con una del servidor y
    acabar intentando borrar 'C:\\Users\\...' en el FTP."""
    assert remote_path_from_history([ESTROPEADA], LOCAL) == ""


def test_distingue_no_subido_de_subido_con_registro_roto():
    assert was_uploaded_according_to_history([ESTROPEADA], LOCAL) is True
    assert was_uploaded_according_to_history([], LOCAL) is False


def test_manda_la_subida_mas_reciente():
    vieja = dict(BUENA, remote="/datos2/viejo/Peli (2024).mkv")
    nueva = dict(BUENA, remote="/datos2/nuevo/Peli (2024).mkv")
    assert remote_path_from_history([vieja, nueva], LOCAL) == nueva["remote"]


def test_ignora_las_subidas_fallidas():
    fallida = dict(BUENA, status="error")
    assert remote_path_from_history([fallida], LOCAL) == ""
    assert was_uploaded_according_to_history([fallida], LOCAL) is False


def test_encuentra_por_nombre_si_no_hay_ruta_local():
    """Los registros antiguos no traen local_path."""
    sin_local = {k: v for k, v in BUENA.items() if k != "local_path"}
    assert remote_path_from_history([sin_local], "", "Peli (2024).mkv") == BUENA["remote"]


# ── El híbrido ───────────────────────────────────────────────────────────

def test_con_registro_bueno_no_toca_el_ftp():
    def _no_llamar(_):
        pytest.fail("no debe consultarse el FTP si el historial ya lo sabe")

    assert resolve_remote_path([BUENA], LOCAL, "Peli (2024).mkv", _no_llamar) == BUENA["remote"]


def test_con_registro_estropeado_pregunta_al_ftp():
    llamadas = []

    def _buscar(nombre):
        llamadas.append(nombre)
        return "/datos2/peliculas/Peli (2024)/Peli (2024).mkv"

    r = resolve_remote_path([ESTROPEADA], LOCAL, "Peli (2024).mkv", _buscar)

    assert llamadas == ["Peli (2024).mkv"]
    assert r == "/datos2/peliculas/Peli (2024)/Peli (2024).mkv"


def test_si_nunca_se_subio_no_pregunta_al_ftp():
    """Sin rastro en el historial no hay nada que buscar: no se paga una
    consulta de red por cada archivo que solo está en local."""
    def _no_llamar(_):
        pytest.fail("no debe consultarse el FTP si nunca se subió")

    assert resolve_remote_path([], LOCAL, "Peli (2024).mkv", _no_llamar) == ""


def test_un_ftp_que_falla_no_rompe_el_dialogo():
    def _revienta(_):
        raise OSError("servidor caído")

    assert resolve_remote_path([ESTROPEADA], LOCAL, "Peli (2024).mkv", _revienta) == ""


def test_sin_ftp_disponible_se_queda_con_el_historial():
    assert resolve_remote_path([ESTROPEADA], LOCAL, "Peli (2024).mkv", None) == ""
    assert resolve_remote_path([BUENA], LOCAL, "Peli (2024).mkv", None) == BUENA["remote"]


# ── Unión de rutas ───────────────────────────────────────────────────────

def test_une_con_separadores_del_servidor():
    """Con os.path.join saldría '\\' en Windows y el servidor no lo entiende."""
    assert join_remote("/datos2/series/Andor", "Andor 1x01.mkv") == "/datos2/series/Andor/Andor 1x01.mkv"
    assert join_remote("", "suelto.mkv") == "suelto.mkv"


# ── Opciones del diálogo de la ✕ ─────────────────────────────────────────

REMOTO = "/datos2/peliculas/Peli (2024)/Peli (2024).mkv"


def test_en_local_y_servidor_se_ofrecen_las_tres():
    assert removal_options(True, REMOTO) == [
        QUITAR_LISTA, QUITAR_Y_LOCAL, QUITAR_LOCAL_Y_REMOTO]


def test_solo_en_local_no_se_ofrece_borrado_remoto():
    assert removal_options(True, "") == [QUITAR_LISTA, QUITAR_Y_LOCAL]


def test_sin_archivo_en_disco_se_puede_borrar_del_servidor():
    assert removal_options(False, REMOTO) == [QUITAR_LISTA, QUITAR_LOCAL_Y_REMOTO]


def test_una_ruta_local_en_el_campo_remoto_no_habilita_el_borrado_remoto():
    """El registro estropeado del historial no debe acabar ofreciendo
    borrar 'C:\\Users\\...' en el servidor."""
    assert removal_options(True, LOCAL) == [QUITAR_LISTA, QUITAR_Y_LOCAL]


def test_quitar_de_la_lista_se_ofrece_siempre():
    for local in (True, False):
        for remoto in (REMOTO, "", LOCAL):
            assert QUITAR_LISTA in removal_options(local, remoto)


def test_solo_las_que_borran_piden_segunda_confirmacion():
    assert not is_destructive(QUITAR_LISTA)
    assert is_destructive(QUITAR_Y_LOCAL)
    assert is_destructive(QUITAR_LOCAL_Y_REMOTO)
