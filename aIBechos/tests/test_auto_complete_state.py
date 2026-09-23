"""Decisión por episodio del autocompletado (core/auto_complete_state.py).

El fallo que esto evita: una descarga lanzada con éxito se marcaba "checked",
pero la pasada siguiente desmarcaba a cualquier episodio que siguiera faltando
en el servidor -- y uno recién encolado en aMule sigue faltando durante horas
(descarga + renombrado + subida). Resultado: la MISMA descarga se relanzaba
cada 30 minutos, indefinidamente. En el log de un usuario, 'My Hero Academia
7x21' se relanzó 9 veces en 20 h, y cada relanzamiento son ~20 s de búsqueda
Kad ocupando la única conexión EC que aMule admite.
"""

from core.auto_complete_state import (ATTEMPT, WAIT_BACKOFF, WAIT_DOWNLOAD,
                                      LAUNCHED_GRACE_S, RETRY_BASE_S,
                                      checked_ts, decide_episode,
                                      retry_wait_for, set_checked, unset_checked)

NOW = 1_700_000_000.0


# ─────────────────────────────────── el bucle de relanzamiento ──

def test_una_descarga_recien_lanzada_no_se_relanza():
    checked = set_checked({}, 73021, 7, 21, int(NOW))["73021"]
    accion, drop = decide_episode(checked, {}, 7, 21, NOW + 30 * 60)
    assert accion == WAIT_DOWNLOAD
    assert drop is False


def test_no_se_relanza_en_ninguna_pasada_del_periodo_de_gracia():
    """La regresión concreta: 40 pasadas de 30 min (20 h) sin un solo intento."""
    checked = set_checked({}, 73021, 7, 21, int(NOW))["73021"]
    acciones = {decide_episode(checked, {}, 7, 21, NOW + n * 30 * 60)[0]
                for n in range(1, 41)}
    assert acciones == {WAIT_DOWNLOAD}


def test_una_descarga_que_nunca_llego_se_reintenta_pasada_la_gracia():
    """Lo contrario tampoco vale: no puede quedarse bloqueado para siempre."""
    checked = set_checked({}, 73021, 7, 21, int(NOW))["73021"]
    accion, drop = decide_episode(checked, {}, 7, 21, NOW + LAUNCHED_GRACE_S + 1)
    assert accion == ATTEMPT
    assert drop is True      # la marca caducada se borra


def test_un_episodio_sin_marca_se_intenta_ya():
    accion, drop = decide_episode({}, {}, 7, 21, NOW)
    assert accion == ATTEMPT
    assert drop is False


# ─────────────────────────────────────────── backoff de fallos ──

def test_un_fallo_reciente_espera_su_backoff():
    retries = {"7": {"21": {"tries": 1, "ts": int(NOW)}}}
    accion, _ = decide_episode({}, retries, 7, 21, NOW + RETRY_BASE_S - 1)
    assert accion == WAIT_BACKOFF


def test_cumplido_el_backoff_se_reintenta():
    retries = {"7": {"21": {"tries": 1, "ts": int(NOW)}}}
    accion, _ = decide_episode({}, retries, 7, 21, NOW + RETRY_BASE_S + 1)
    assert accion == ATTEMPT


def test_el_backoff_crece_con_cada_intento():
    esperas = [retry_wait_for(n) for n in (1, 2, 3, 4)]
    assert esperas == [30 * 60, 60 * 60, 2 * 3600, 4 * 3600]
    assert retry_wait_for(99) == retry_wait_for(100)   # topado


def test_una_marca_caducada_no_se_salta_el_backoff():
    """Si además de la marca vieja hay un fallo reciente, manda el backoff --
    pero la marca caducada se limpia igualmente."""
    checked = set_checked({}, 73021, 7, 21, int(NOW))["73021"]
    retries = {"7": {"21": {"tries": 1, "ts": int(NOW + LAUNCHED_GRACE_S)}}}
    accion, drop = decide_episode(checked, retries, 7, 21, NOW + LAUNCHED_GRACE_S + 60)
    assert accion == WAIT_BACKOFF
    assert drop is True


# ──────────────────────────── compatibilidad con el formato viejo ──

def test_el_formato_antiguo_de_lista_se_sigue_leyendo():
    legacy = {"7": [19, 21]}
    assert checked_ts(legacy, 7, 21) == 0
    assert checked_ts(legacy, 7, 20) is None


def test_una_entrada_legacy_cae_al_backoff_en_la_primera_pasada():
    """El formato viejo anotaba aquí también los FALLOS, así que no se puede
    confiar en él: se desmarca y se reintenta (la migración de siempre)."""
    accion, drop = decide_episode({"7": [21]}, {}, 7, 21, NOW)
    assert accion == ATTEMPT
    assert drop is True


def test_marcar_una_temporada_legacy_la_convierte_sin_perder_episodios():
    checked = set_checked({"73021": {"7": [19]}}, 73021, 7, 21, int(NOW))
    assert checked["73021"]["7"] == {"19": 0, "21": int(NOW)}


# ─────────────────────────────────────────── set / unset checked ──

def test_unset_avisa_de_si_cambio_algo():
    checked = set_checked({}, 73021, 7, 21, int(NOW))
    assert unset_checked(checked, 73021, 7, 21) is True
    assert unset_checked(checked, 73021, 7, 21) is False   # ya no estaba
    assert checked == {}                                   # sin restos vacíos


def test_unset_sobre_el_formato_antiguo():
    checked = {"73021": {"7": [19, 21]}}
    assert unset_checked(checked, 73021, 7, 21) is True
    assert checked == {"73021": {"7": [19]}}


def test_unset_de_una_serie_que_no_esta_no_revienta():
    assert unset_checked({}, 73021, 7, 21) is False
