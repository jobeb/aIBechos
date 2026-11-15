"""Ignorados de "Episodios que faltan": comunes a todos (archivo propio
con una marca por cosa ignorada) y a salvo de quien reescriba la caché."""

import json

import pytest

from core import missing_ep_ignored as ign
from core import missing_episodes_cache as mec


@pytest.fixture(autouse=True)
def _aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(ign, "_path", lambda: tmp_path / "ignored.json")
    monkeypatch.setattr(mec, "_path", lambda: tmp_path / "cache.json")
    ign._reset_for_tests()
    mec._reset_cache_for_tests()
    yield
    ign._reset_for_tests()
    mec._reset_cache_for_tests()


def test_marcas_y_campos():
    m = ign.set_mark({}, 10, on=True, now=1)
    m = ign.set_mark(m, 10, 2, on=True, now=1)
    m = ign.set_mark(m, 10, 1, 3, on=True, now=1)
    m = ign.set_mark(m, 10, 1, 4, on=True, now=1)
    m = ign.set_mark(m, 10, 1, 4, on=False, now=2)     # quitado: marca "off"
    m = ign.set_mark(m, 100, 1, 1, on=True, now=1)    # otra serie (prefijo parecido)
    assert ign.fields_for(m, 10) == (True, {2}, {1: {3}})
    assert ign.fields_for(m, 99) == (False, set(), {})


def test_fusion_gana_lo_mas_reciente_de_cada_marca():
    a = ign.set_mark({}, 10, 1, 3, on=True, by="Jose", now=100)
    b = ign.set_mark({}, 10, 1, 3, on=False, by="Efren", now=200)
    b = ign.set_mark(b, 20, on=True, now=50)
    for merged in (ign.merge(a, b), ign.merge(b, a)):
        assert merged["10/1/3"]["on"] is False      # quitar más tarde gana
        assert merged["20"]["on"] is True           # lo de un solo lado se queda
    assert ign.merge({"x": "basura"}, None) == {}


def test_la_cache_reescrita_con_copia_vieja_no_pierde_ignorados():
    """Real: el escaneo copia la caché al empezar y la guarda al acabar."""
    mec.save_cache({"10": {"name": "Serie", "missing": {"1": [3, 4]}}})
    stale = json.loads(json.dumps(mec.load_cache()))   # copia del escaneo
    ign.save_marks(ign.set_mark(ign.load_marks(), 10, 1, 3, on=True, now=5))
    mec.save_cache(dict(mec.load_cache()))             # lo que hace la pestaña
    mec.save_cache(stale)                              # el escaneo termina
    mec._reset_cache_for_tests()
    assert mec.load_cache()["10"]["ignored_episodes"] == {"1": [3]}


def test_serie_quitada_y_vuelta_a_aparecer_conserva_lo_ignorado():
    ign.save_marks(ign.set_mark({}, 10, on=True, now=5))
    mec.save_cache({})                                 # Jellyfin caído / fusión
    mec.save_cache({"10": {"name": "Serie"}})          # vuelve en otro escaneo
    assert mec.load_cache()["10"]["ignored"] is True


def test_primera_vez_trae_los_ignorados_que_habia_en_la_cache(tmp_path):
    (tmp_path / "cache.json").write_text(json.dumps({
        "10": {"ignored": True, "ignored_seasons": [2], "ignored_episodes": {"1": [3]}},
        "20": {"ignored": False},
        "_meta": {}}), encoding="utf-8")
    assert ign.fields_for(ign.load_marks(), 10) == (True, {2}, {1: {3}})
    assert (tmp_path / "ignored.json").exists()
    # Hora mínima: cualquier cambio real de otro equipo gana.
    other = ign.set_mark({}, 10, on=False, now=2)
    assert ign.merge(ign.load_marks(), other)["10"]["on"] is False


def test_filas_de_la_pestana():
    m = ign.set_mark({}, 10, 1, 3, on=True, now=1)
    rows = [{"tmdb_id": 10, "ignored": True, "ignored_seasons": {5}, "ignored_episodes": {}}]
    ign.apply_to_rows(rows, m)
    assert rows[0]["ignored"] is False
    assert rows[0]["ignored_seasons"] == set()
    assert rows[0]["ignored_episodes"] == {1: {3}}
