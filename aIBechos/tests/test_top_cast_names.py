"""Reparto principal a partir de la respuesta de TMDB (/credits).

Lo que se protege aquí es sobre todo el ORDEN: TMDB trae un campo "order"
(0 = cabeza de cartel) y en una serie con reparto largo la diferencia entre
usarlo o no es enseñar a los protagonistas o a tres secundarios cualesquiera.
"""

from core.api_client import top_cast_names


def _p(name, character="", order=None):
    p = {"name": name, "character": character}
    if order is not None:
        p["order"] = order
    return p


def test_formatea_actor_y_personaje():
    credits = {"cast": [_p("Bryan Cranston", "Walter White", 0)]}
    assert top_cast_names(credits) == ["Bryan Cranston (Walter White)"]


def test_sin_personaje_solo_el_actor():
    credits = {"cast": [_p("Bryan Cranston", "", 0)]}
    assert top_cast_names(credits) == ["Bryan Cranston"]


def test_ordena_por_order_no_por_orden_de_llegada():
    credits = {"cast": [_p("Secundario", "", 7), _p("Protagonista", "", 0),
                        _p("Reparto", "", 3)]}
    assert top_cast_names(credits, limit=3) == ["Protagonista", "Reparto", "Secundario"]


def test_los_que_no_traen_order_van_al_final():
    credits = {"cast": [_p("Sin order"), _p("Protagonista", "", 0)]}
    assert top_cast_names(credits, limit=2) == ["Protagonista", "Sin order"]


def test_respeta_el_limite():
    credits = {"cast": [_p(f"Actor {i}", "", i) for i in range(20)]}
    assert len(top_cast_names(credits, limit=6)) == 6


def test_descarta_entradas_sin_nombre():
    credits = {"cast": [_p("", "Nadie", 0), _p("   ", "", 1), _p("Real", "", 2)]}
    assert top_cast_names(credits) == ["Real"]


def test_sin_reparto_devuelve_lista_vacia():
    assert top_cast_names({}) == []
    assert top_cast_names({"cast": []}) == []
    assert top_cast_names(None) == []


def test_entradas_que_no_son_dict_no_revientan():
    # Defensivo: la respuesta viene de la red, no de nuestro código.
    assert top_cast_names({"cast": [None, "texto suelto", _p("Real", "", 0)]}) == ["Real"]
