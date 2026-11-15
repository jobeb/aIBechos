"""Caché de Episodios que faltan: un escaneo largo no deshace los cambios
pequeños hechos mientras escaneaba."""

import pytest

from core import missing_episodes_cache as mec
from core import missing_ep_ignored as ign


@pytest.fixture(autouse=True)
def _aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(mec, "_path", lambda: tmp_path / "cache.json")
    monkeypatch.setattr(ign, "_path", lambda: tmp_path / "ignored.json")
    mec._reset_cache_for_tests()
    ign._reset_for_tests()
    yield
    mec._reset_cache_for_tests()
    ign._reset_for_tests()


def test_episodio_subido_durante_el_escaneo_no_vuelve():
    mec.save_cache({"10": {"name": "Serie", "missing": {"1": [3, 4]}},
                    "20": {"name": "Otra", "missing": {"1": [1]}}})
    since = mec.generation()
    scan = {"10": {"name": "Serie", "missing": {"1": [3, 4]}},      # el escaneo aún lo ve
            "20": {"name": "Otra", "missing": {"1": [1]}}}
    assert mec.update_cache(lambda c: mec.remove_missing_episode_from_cache(c, 10, 1, 3))
    assert mec.update_cache(lambda c: mec.remove_series_from_cache(c, 20))
    mec.save_scan_result(scan, since)
    mec._reset_cache_for_tests()
    cache = mec.load_cache()
    assert cache["10"]["missing"] == {"1": [4]}
    assert "20" not in cache


def test_lo_anterior_al_escaneo_no_se_repite_y_sin_cambio_no_guarda():
    mec.save_cache({"10": {"missing": {"1": [3]}}})
    mec.update_cache(lambda c: mec.remove_missing_episode_from_cache(c, 10, 1, 3))
    since = mec.generation()
    mec.save_scan_result({"10": {"missing": {"1": [3, 5]}}}, since)   # el escaneo sabe más
    assert mec.load_cache()["10"]["missing"] == {"1": [3, 5]}
    assert not mec.update_cache(lambda c: False)


def test_fusion_sin_diario_no_se_reaplica():
    mec.save_cache({"10": {"missing": {"1": [3]}}})
    since = mec.generation()
    mec.update_cache(lambda c: c.update({"99": {"missing": {}}}) or True, journal=False)
    mec.save_scan_result({"10": {"missing": {"1": [3]}}}, since)
    assert "99" not in mec.load_cache()
