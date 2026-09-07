"""Detectar restos de subidas anteriores bajo un nombre viejo (cuando se
reasigna un archivo mal identificado y se vuelve a subir).

Contexto: "Papillon (2017)" se detectó mal como "Papillon (1973)" y se
empezó a subir con ese nombre. Al reasignarlo bien y volver a subir, el
servidor puede haber quedado con un resto bajo el nombre viejo. Esto mira
el historial para ofrecer borrarlo antes de subir la versión corregida.
"""

import pytest

from core.remote_presence import stale_remote_from_history

LOCAL = r"C:\Users\Jose\Downloads\eMule\Incoming\Papillon.2017.mkv"


def _ok(remote, local=LOCAL, filename="Papillon (2017).mkv", status="ok"):
    return {"status": status, "filename": filename,
            "local_path": local, "remote": remote}


NUEVO = "/datos2/peliculas/Papillon (2017)/Papillon (2017).mkv"
VIEJO = "/datos2/peliculas/Papillon (1973)/Papillon (1973).mkv"


def test_detecta_resto_con_nombre_viejo_distinto():
    assert stale_remote_from_history([_ok(VIEJO)], LOCAL, "x", NUEVO) == VIEJO


def test_misma_ruta_no_devuelve_nada():
    """Resubir el mismo archivo al mismo sitio no es reasignar: no hay
    resto distinto que borrar."""
    assert stale_remote_from_history([_ok(NUEVO)], LOCAL, "x", NUEVO) == ""


def test_sin_historial_no_devuelve_nada():
    assert stale_remote_from_history([], LOCAL, "x", NUEVO) == ""


def test_manda_el_resto_mas_reciente():
    viejo_2 = "/datos2/peliculas/Papillon (1975)/Papillon (1975).mkv"
    hist = [_ok(VIEJO), _ok(viejo_2)]
    assert stale_remote_from_history(hist, LOCAL, "x", NUEVO) == viejo_2


def test_ignora_el_nombre_viejo_que_ya_coincide():
    """Si en el historial lo último fue el nombre nuevo (ya se corrigió
    antes y se subió bien), aún así un resto VIEJO subido antes al servidor
    puede seguir ahí sin borrar -- se sigue detectando para ofrecer
    limpiarlo. Solo se ignora cuando NO queda nada con otro nombre."""
    hist = [_ok(VIEJO), _ok(NUEVO)]
    assert stale_remote_from_history(hist, LOCAL, "x", NUEVO) == VIEJO


def test_ignora_cuando_no_queda_ninguna_ruta_con_nombre_antiguo():
    """Resubir el mismo archivo al mismo sitio exacto (sin ningún registro
    histórico con otro nombre) no es reasignar: no hay resto que borrar."""
    solo_nuevo = [_ok(NUEVO), _ok(NUEVO)]
    assert stale_remote_from_history(solo_nuevo, LOCAL, "x", NUEVO) == ""


def test_los_registros_cancelados_no_cuentan():
    """"saltado"/"cancelado" no dejan nada en el servidor."""
    cancelado = _ok(VIEJO)
    cancelado["status"] = "saltado"
    assert stale_remote_from_history([cancelado], LOCAL, "x", NUEVO) == ""


def test_el_error_a_medias_si_cuenta():
    erro = _ok(VIEJO, status="error")
    assert stale_remote_from_history([erro], LOCAL, "x", NUEVO) == VIEJO


def test_encuentra_por_nombre_si_no_hay_ruta_local():
    sin_local = {k: v for k, v in _ok(VIEJO).items() if k != "local_path"}
    assert stale_remote_from_history([sin_local], "", "Papillon (2017).mkv", NUEVO) == VIEJO


def test_ignora_el_campo_remote_estropeado():
    """El registro viejo que guardó la ruta local en 'remote' no debe
    acabar proponiendo borrar 'C:\\Users\\...' en el servidor."""
    estropeado = {k: v for k, v in _ok(LOCAL).items()}
    assert stale_remote_from_history([estropeado], LOCAL, "x", NUEVO) == ""


def test_sin_ruta_nueva_no_hay_nada_que_comparar():
    assert stale_remote_from_history([_ok(VIEJO)], LOCAL, "x", "") == ""


def test_ruta_nueva_local_no_genera_aviso():
    assert stale_remote_from_history([_ok(VIEJO)], LOCAL, "x", LOCAL) == ""
