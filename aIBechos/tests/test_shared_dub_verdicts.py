from core.shared_dub_verdicts import (
    set_verdict, load_local_cache, save_local_cache,
)


def test_set_verdict_returns_new_dict_without_mutating_original():
    original = {}
    verdict = {"veredicto": "hueco_real", "motivo": "Faltan episodios sueltos"}
    result = set_verdict(original, 1234, verdict, "Jose")
    assert original == {}   # no mutado
    assert "1234" in result


def test_set_verdict_stores_expected_fields():
    verdict = {"veredicto": "hueco_real", "motivo": "x", "doblaje_castellano": {"2": 2}}
    result = set_verdict({}, 1234, verdict, "Jose", checked_at=1000.0)
    assert result["1234"] == {
        "veredicto": "hueco_real", "motivo": "x", "doblaje_castellano": {"2": 2},
        "checked_at": 1000.0, "checked_by": "Jose",
    }


def test_set_verdict_uses_current_time_when_not_given():
    verdict = {"veredicto": "hueco_real", "motivo": "x"}
    result = set_verdict({}, 1234, verdict, "Jose")
    assert result["1234"]["checked_at"] > 0


def test_set_verdict_overwrites_existing_entry_for_same_series():
    data = set_verdict({}, 1234, {"veredicto": "hueco_real", "motivo": "primero"}, "Jose", checked_at=1000.0)
    result = set_verdict(data, 1234, {"veredicto": "numeracion_distinta", "motivo": "segundo"}, "Ana", checked_at=2000.0)
    assert result["1234"]["veredicto"] == "numeracion_distinta"
    assert result["1234"]["checked_by"] == "Ana"


def test_set_verdict_does_not_touch_other_series():
    data = set_verdict({}, 1, {"veredicto": "hueco_real", "motivo": "a"}, "Jose", checked_at=1000.0)
    result = set_verdict(data, 2, {"veredicto": "hueco_real", "motivo": "b"}, "Jose", checked_at=1000.0)
    assert "1" in result and "2" in result


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.shared_dub_verdicts.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.shared_dub_verdicts.app_data_dir", lambda: tmp_path)
    data = set_verdict({}, 1234, {"veredicto": "hueco_real", "motivo": "x"}, "Jose", checked_at=1000.0)

    save_local_cache(data)

    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.shared_dub_verdicts.app_data_dir", lambda: tmp_path)
    (tmp_path / "shared_dub_verdicts.json").write_text("{esto no es json", encoding="utf-8")
    assert load_local_cache() == {}
