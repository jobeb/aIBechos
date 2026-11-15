"""Favoritos/reservas sin subir: no se pierden bajo la copia del servidor."""

import pytest

from core import shared_pending as sp


@pytest.fixture(autouse=True)
def _aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_path", lambda: tmp_path / "pending.json")


def test_pendiente_se_reaplica_sobre_lo_del_servidor():
    sp.add("favoritos", "add", ("tv", 10, "Serie", "Jose", 1))
    remoto = {"tv:20": {"media_type": "tv", "tmdb_id": 20}}      # sin el cambio
    out = sp.replay("favoritos", remoto, sp.pending("favoritos"))
    assert set(out) == {"tv:10", "tv:20"}
    assert remoto == {"tv:20": {"media_type": "tv", "tmdb_id": 20}}, "no muta"


def test_quitar_tambien_y_en_orden():
    sp.add("reservas", "add", ("tv", 10, "Serie", 5, "Jose", 1))
    sp.add("reservas", "remove", ("tv", 10))
    out = sp.replay("reservas", {}, sp.pending("reservas"))
    assert out == {}


def test_confirmar_solo_lo_que_se_subio():
    sp.add("favoritos", "add", ("tv", 1, "A", "", 0))
    subida = sp.pending("favoritos")
    sp.add("favoritos", "add", ("tv", 2, "B", "", 0))         # llega durante la subida
    sp.confirm("favoritos", subida)
    left = sp.pending("favoritos")
    assert [x["args"][1] for x in left] == [2]
    sp.confirm("favoritos", subida)                            # repetir no quita nada más
    assert len(sp.pending("favoritos")) == 1


def test_operacion_rota_no_rompe():
    sp.add("favoritos", "desconocida", ())
    sp.add("favoritos", "remove", ())                          # faltan argumentos
    assert sp.replay("favoritos", {"x": {}}, sp.pending("favoritos")) == {"x": {}}
    assert sp.replay("otra", {"x": {}}, sp.pending("favoritos")) == {"x": {}}
