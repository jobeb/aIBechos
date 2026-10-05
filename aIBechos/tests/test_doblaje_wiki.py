from core import doblaje_wiki as wiki


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_cutoff_desde_wiki_con_texto_explicito(monkeypatch):
    def _fake_get(url, params=None, timeout=None, headers=None):
        if (params or {}).get("list") == "search":
            return _FakeResponse({"query": {"search": [{"title": "Dragon Ball Daima"}]}})
        return _FakeResponse({"query": {"pages": {"1": {
            "extract": "Serie doblada al castellano hasta la temporada 1. La temporada 2 no fue doblada."}}}})
    monkeypatch.setattr(wiki.requests, "get", _fake_get)
    assert wiki.cutoff_for_series("Dragon Ball Daima", [1, 2]) == {2: 0}


def test_sin_resultados_no_inventa(monkeypatch):
    monkeypatch.setattr(wiki.requests, "get",
                        lambda *a, **kw: _FakeResponse({"query": {"search": []}}))
    assert wiki.search_series("Xyz Inexistente") == []
    assert wiki.cutoff_for_series("Xyz Inexistente", [1]) == {}


def test_fallo_red_no_lanza(monkeypatch):
    def _boom(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(wiki.requests, "get", _boom)
    assert wiki.search_series("Daima") == []
    assert wiki.get_summary("Daima", wiki._APIS[0]) == ""
    assert wiki.cutoff_for_series("Daima", [1]) == {}
