from core import streaming_availability as sa


def _opt(audios):
    return {"service": {"name": "Disney+"}, "audios": audios}


def _show(seasons_eps):
    """seasons_eps: [[veredicto_por_episodio...], ...] -> show fake con
    audios spa/ESP (True), spa/419 (False-latino), jpn (False-otro) o
    sin opciones (None)."""
    def _aud(v):
        if v is True:
            return [{"language": "spa", "region": "ESP"}]
        if v == "latino":
            return [{"language": "spa", "region": "419"}]
        if v is False:
            return [{"language": "jpn"}]
        return []
    seasons = []
    for eps in seasons_eps:
        seasons.append({"episodes": [
            {"streamingOptions": {"es": [_opt(_aud(v))]}} if v is not None
            else {"streamingOptions": {}}
            for v in eps]})
    return {"seasons": seasons}


def test_castellano_esp_vale_y_latino_no():
    assert sa._episode_castilian({"es": [_opt([{"language": "spa", "region": "ESP"}])]}) is True
    assert sa._episode_castilian({"es": [_opt([{"language": "spa", "region": "419"}])]}) is False
    assert sa._episode_castilian({"es": [_opt([{"language": "jpn"}])]}) is False
    assert sa._episode_castilian({}) is None
    assert sa._episode_castilian({"es": []}) is None


def test_latino_mas_castellano_gana_castellano():
    opts = {"es": [_opt([{"language": "spa", "region": "419"}]),
                   _opt([{"language": "spa", "region": "ESP"}])]}
    assert sa._episode_castilian(opts) is True


def test_spa_sin_region_se_acepta():
    assert sa._episode_castilian({"es": [_opt([{"language": "spa"}])]}) is True


def test_cutoff_tramo_inicial_y_validacion_tamanos():
    show = _show([[True, True, "latino", True], [True]])
    # T1: E03 solo latino -> corte en 2; T2 validada
    assert sa.cutoff_from_show(show, {1: 4, 2: 1}) == {1: 2, 2: 1}
    # T1 con tamaño distinto -> se omite entera
    assert sa.cutoff_from_show(show, {1: 5, 2: 1}) == {2: 1}
    # Sin tamaños no se puede validar el mapeo posicional
    assert sa.cutoff_from_show(show, None) == {}
    assert sa.cutoff_from_show(show, {}) == {}


def test_cutoff_for_series_usa_get_show(monkeypatch):
    show = _show([[True, True]])
    monkeypatch.setattr(sa, "get_show", lambda *a, **kw: show)
    assert sa.cutoff_for_series("key", 1, {1: 2}) == {1: 2}
    monkeypatch.setattr(sa, "get_show", lambda *a, **kw: None)
    assert sa.cutoff_for_series("key", 1, {1: 2}) == {}


def test_get_show_sin_key_no_llama(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("no debe llamar sin key")
    monkeypatch.setattr(sa.requests, "get", _boom)
    assert sa.get_show("", 1) is None
    assert sa.get_show(None, 1) is None
