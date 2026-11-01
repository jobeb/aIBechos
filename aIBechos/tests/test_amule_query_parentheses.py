"""Títulos con paréntesis: aMule rechazaba la búsqueda ("syntax error /
Undefined search expression", reales: "Incontrolable (I Swear)" y "El
juego de la sospecha (Cluedo)", fallidas tras 5 intentos)."""

from core.amule_search import build_amule_query, build_amule_season_query, search_title


def test_paréntesis_final_fuera_de_la_búsqueda():
    assert build_amule_query("Incontrolable (I Swear)", None, None, "2025") == "Incontrolable"
    assert build_amule_query("El juego de la sospecha (Cluedo)", None, None, "1985",
                             None, True) == "El juego de la sospecha castellano"
    assert build_amule_query("Doctor Who (2005)", 3, 4) == "Doctor Who 3x04"
    assert build_amule_season_query("Doctor Who (2005)", 3) == "Doctor Who 3x"


def test_paréntesis_del_principio_es_título():
    assert search_title("(500) días juntos") == "(500) días juntos"
    assert build_amule_query("(500) días juntos", None, None) == "500 días juntos"
    assert search_title("(Cluedo)") == "(Cluedo)"


def test_plantilla_se_busca_con_el_nombre_completo():
    tpl = {"Incontrolable (I Swear)": "{serie} {año}"}
    assert build_amule_query("Incontrolable (I Swear)", None, None, "2025", tpl) == "Incontrolable 2025"
    assert build_amule_query("Slime", 2, 3, templates={"Slime": "Tensura [{temporada}]"}) == "Tensura 2"


def test_ec_sanea_antes_de_enviar(monkeypatch):
    from core import ec_client
    sent = []
    c = ec_client.EcClient.__new__(ec_client.EcClient)
    monkeypatch.setattr(ec_client, "_make_string_tag",
                        lambda tag, value: sent.append(value) or b"")
    monkeypatch.setattr(ec_client, "_make_tag", lambda *a, **k: b"")
    c._send_packet = lambda *a: None
    c._recv_packet = lambda: (0, [])
    c.start_search("Algo (raro) [x]")
    assert sent[0] == "Algo raro x"
