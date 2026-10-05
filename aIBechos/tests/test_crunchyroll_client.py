import base64
import json

import requests

from core.crunchyroll_client import (
    CrunchyrollClient,
    CrunchyrollRateLimitError,
    CrunchyrollUnavailableError,
    _cms_host_from_policy,
    _ep_numbers,
    _parse_anon_config,
    _parse_search_items,
    cutoff_from_episodes,
    episode_is_castilian,
    has_castilian_audio,
)


class _FakeResponse:
    def __init__(self, json_data=None, text="", status_code=200):
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._json


_HOME_HTML = ('<html><script>{"cxApiParams":{"apiDomain":"https://beta-api.crunchyroll.com",'
              '"anonClientId":"abc123"}}</script></html>')

# Landing de captación: trae anonClientId pero NO apiDomain (verificado en
# vivo) -- no sirve como fuente de config.
_ACQUISITION_HTML = ('<html><script>{"cxApiParams":{"host":"https://www.crunchyroll.com",'
                     '"anonClientId":"cr_web"}}</script></html>')


def _cloudfront_policy(resource: str) -> str:
    """Policy firmada estilo CloudFront para *resource* (+ -> -, = -> _,
    / -> ~, como hace CloudFront de verdad)."""
    std = base64.b64encode(
        json.dumps({"Statement": [{"Resource": resource}]}).encode()).decode()
    return std.replace("+", "-").replace("=", "_").replace("/", "~")


def test_parse_anon_config_extracts_domain_and_client_id():
    domain, client_id = _parse_anon_config(_HOME_HTML)
    assert domain == "https://beta-api.crunchyroll.com"
    assert client_id == "abc123"


def test_parse_anon_config_returns_empty_when_format_changes():
    assert _parse_anon_config("<html>sin nada</html>") == ("", "")
    assert _parse_anon_config("") == ("", "")
    # La landing de captación no trae apiDomain: no vale como config.
    assert _parse_anon_config(_ACQUISITION_HTML) == ("", "cr_web")


def test_cms_host_from_policy_reads_authorized_host():
    policy = _cloudfront_policy(
        "https://beta-api.crunchyroll.com/cms/v?/ES/M2/-/*")
    assert _cms_host_from_policy(policy, "https://www.crunchyroll.com") == \
        "https://beta-api.crunchyroll.com"


def test_cms_host_from_policy_falls_back_on_garbage():
    assert _cms_host_from_policy("!!!no-es-base64!!!", "https://x.com") == \
        "https://x.com"
    assert _cms_host_from_policy("", "https://x.com") == "https://x.com"


def test_has_castilian_audio_accepts_es_es():
    assert has_castilian_audio(["ja-JP", "es-ES"]) is True
    assert has_castilian_audio(["es-es"]) is True


def test_has_castilian_audio_rejects_latin_only_and_japanese_only():
    # Solo latino: no vale como castellano (decisión del usuario).
    assert has_castilian_audio(["ja-JP", "es-419"]) is False
    assert has_castilian_audio(["ja-JP", "en-US"]) is False


def test_has_castilian_audio_returns_none_without_audio_info():
    assert has_castilian_audio([]) is None
    assert has_castilian_audio(None) is None
    assert has_castilian_audio([None, ""]) is None


def test_episode_is_castilian_checks_versions_first():
    ep = {"audio_locale": "ja-JP",
          "versions": [{"audio_locale": "ja-JP"}, {"audio_locale": "es-ES"}]}
    assert episode_is_castilian(ep) is True


def test_episode_is_castilian_rejects_latin_only():
    ep = {"audio_locale": "es-419",
          "versions": [{"audio_locale": "ja-JP"}, {"audio_locale": "es-419"}]}
    assert episode_is_castilian(ep) is False


def test_episode_is_castilian_returns_none_without_audio():
    assert episode_is_castilian({}) is None
    assert episode_is_castilian(None) is None


def test_ep_numbers_accepts_cms_variants():
    assert _ep_numbers({"season_number": 2, "sequence_number": 5}) == (2, 5)
    assert _ep_numbers({"seasonNumber": 1, "episode": 3}) == (1, 3)
    assert _ep_numbers({}) == (None, None)


def test_cutoff_from_episodes_only_counts_initial_dubbed_run():
    verdicts = {(1, 1): True, (1, 2): True, (1, 3): False, (1, 4): True,
                (2, 1): True, (2, 2): None}
    # T1: E04 tras el hueco de E03 no cuenta; T2: E01 solo, E02 sin dato frena.
    assert cutoff_from_episodes(verdicts) == {1: 2, 2: 1}


def test_cutoff_from_episodes_ignores_bad_keys():
    assert cutoff_from_episodes({("x", 1): True, (1, 1): True}) == {1: 1}
    assert cutoff_from_episodes({}) == {}


def test_parse_search_items_splits_series_and_movies():
    data = {"items": [
        {"type": "series", "total": 1,
         "items": [{"id": "S1", "title": "Naruto"}]},
        {"type": "movie_listing", "total": 1,
         "items": [{"id": "M1", "title": "Peli"}]},
        {"type": "series", "total": 0, "items": [{"id": "X"}]},
    ]}
    series, movies = _parse_search_items(data)
    assert [s["id"] for s in series] == ["S1"]
    assert [m["id"] for m in movies] == ["M1"]


def _auth_ok_client(monkeypatch):
    client = CrunchyrollClient()
    calls = {}

    def fake_get(url, **kw):
        calls.setdefault("get", []).append(url)
        if url.endswith("/index/v2"):
            return _FakeResponse({"cms": {"bucket": "/ES/M2/-",
                                          "policy": _cloudfront_policy(
                                              "https://beta-api.crunchyroll.com/cms/v2/ES/M2/-/*"),
                                          "signature": "s",
                                          "key_pair_id": "k"}})
        return _FakeResponse(text=_HOME_HTML)

    def fake_post(url, **kw):
        calls.setdefault("post", []).append(url)
        auth = (kw.get("headers") or {}).get("Authorization", "")
        assert auth.startswith("Basic "), "el token anónimo va en Basic"
        return _FakeResponse({"access_token": "tok", "token_type": "Bearer",
                              "expires_in": 240})

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr(client.session, "post", fake_post)
    return client, calls


def test_ensure_auth_uses_cms_host_from_policy(monkeypatch):
    client, _calls = _auth_ok_client(monkeypatch)
    client._ensure_auth()
    assert client._cms["cms_host"] == "https://beta-api.crunchyroll.com"
    assert client._cms_base() == \
        "https://beta-api.crunchyroll.com/cms/v2/ES/M2/-"


def test_ensure_auth_skips_acquisition_page_without_api_domain(monkeypatch):
    from core import crunchyroll_client as cr_mod
    client = CrunchyrollClient()
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        if url.endswith("/index/v2"):
            return _FakeResponse({"cms": {"bucket": "/b"}})
        # La primera URL (ficha de serie) devuelve la landing sin apiDomain.
        if len(seen) == 1:
            return _FakeResponse(text=_ACQUISITION_HTML)
        return _FakeResponse(text=_HOME_HTML)

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr(
        client.session, "post",
        lambda url, **kw: _FakeResponse({"access_token": "tok",
                                         "token_type": "Bearer",
                                         "expires_in": 240}))
    client._ensure_auth()
    assert client._api_domain == "https://beta-api.crunchyroll.com"
    assert seen[0] == cr_mod._CONFIG_URLS[0]  # ficha de serie primero
    assert len(seen) >= 2  # y la home como respaldo


def test_ensure_auth_caches_token_and_cms(monkeypatch):
    client, calls = _auth_ok_client(monkeypatch)
    client._ensure_auth()
    client._ensure_auth()
    assert len(calls["post"]) == 1  # segunda vez: cache
    assert client._token == "tok"
    assert client._cms["bucket"] == "/ES/M2/-"


def test_ensure_auth_raises_friendly_error_when_format_changes(monkeypatch):
    client = CrunchyrollClient()
    monkeypatch.setattr(client.session, "get",
                        lambda url, **kw: _FakeResponse(text="<html>otro formato</html>"))
    try:
        client._ensure_auth()
        assert False, "debería haber lanzado CrunchyrollUnavailableError"
    except CrunchyrollUnavailableError as e:
        assert "formato" in str(e).lower()


def test_get_seasons_uses_series_id_query_form(monkeypatch):
    client, _calls = _auth_ok_client(monkeypatch)
    client._ensure_auth()
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _FakeResponse({"items": [{"id": "T1", "season_number": 1}]})

    monkeypatch.setattr(client.session, "get", fake_get)
    # Sin __links__: va directo a la forma ?series_id= (verificada en vivo).
    monkeypatch.setattr(client, "get_series", lambda sid, locale="es-ES": {})
    seasons = client.get_seasons("SERIE1")
    assert seasons == [{"id": "T1", "season_number": 1}]
    assert "seasons?series_id=SERIE1" in captured["url"]
    assert captured["url"].startswith("https://beta-api.crunchyroll.com/")


def test_get_seasons_follows_links_href_when_present(monkeypatch):
    client, _calls = _auth_ok_client(monkeypatch)
    client._ensure_auth()
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _FakeResponse({"items": [{"id": "T1"}]})

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr(
        client, "get_series",
        lambda sid, locale="es-ES": {
            "__links__": {"series/seasons": {
                "href": "/cms/v2/ES/M2/-/seasons?series_id=SERIE1"}}})
    assert client.get_seasons("SERIE1") == [{"id": "T1"}]
    assert captured["url"].startswith("https://beta-api.crunchyroll.com/cms/v2/")


def test_get_episodes_uses_season_id_query_form(monkeypatch):
    client, _calls = _auth_ok_client(monkeypatch)
    client._ensure_auth()
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _FakeResponse({"items": [{"id": "E1",
                                          "audio_locale": "es-ES"}]})

    monkeypatch.setattr(client.session, "get", fake_get)
    eps = client.get_episodes("TEMP1")
    assert eps == [{"id": "E1", "audio_locale": "es-ES"}]
    assert "episodes?season_id=TEMP1" in captured["url"]


def test_search_raises_rate_limit_on_429(monkeypatch):
    client = CrunchyrollClient()
    client._api_domain = "https://x"
    client._token = "t"
    client._token_exp = 9999999999.0
    client._cms = {"bucket": "/cms"}
    monkeypatch.setattr(client.session, "get",
                        lambda url, **kw: _FakeResponse(status_code=429))
    try:
        client.search("naruto")
        assert False, "debería haber lanzado CrunchyrollRateLimitError"
    except CrunchyrollRateLimitError as e:
        assert "límite" in str(e).lower()


def test_cutoff_for_series_builds_cutoff_from_episodes(monkeypatch):
    client = CrunchyrollClient()

    def fake_search(query, locale="es-ES"):
        assert query == "Naruto"
        return [{"id": "SERIE1", "title": "Naruto"}], []

    seasons = [{"id": "TEMP1"}]
    episodes = [{"season_number": 1, "sequence_number": 1, "audio_locale": "es-ES"},
                {"season_number": 1, "sequence_number": 2, "audio_locale": "es-ES"},
                {"season_number": 1, "sequence_number": 3, "audio_locale": "ja-JP"},
                {"season_number": 1, "sequence_number": 4, "audio_locale": "es-ES"}]
    monkeypatch.setattr(client, "search", fake_search)
    monkeypatch.setattr(client, "get_seasons", lambda sid, locale="es-ES": seasons)
    monkeypatch.setattr(client, "get_episodes", lambda sid, locale="es-ES": episodes)
    # E04 tras un hueco (E03 sin doblar) no cuenta: corte en 2.
    assert client.cutoff_for_series("Naruto", [1]) == {1: 2}


def test_cutoff_for_series_returns_empty_when_no_series(monkeypatch):
    client = CrunchyrollClient()
    monkeypatch.setattr(client, "search", lambda q, locale="es-ES": ([], []))
    assert client.cutoff_for_series("algo que no existe", [1]) == {}


def test_cutoff_for_series_never_raises_on_network_error(monkeypatch):
    client = CrunchyrollClient()

    def _raise(query, locale="es-ES"):
        raise ConnectionError("Sin conexión a internet.")

    monkeypatch.setattr(client, "search", _raise)
    assert client.cutoff_for_series("Naruto", [1]) == {}


def test_cutoff_for_series_ignores_seasons_out_of_scope(monkeypatch):
    client = CrunchyrollClient()
    monkeypatch.setattr(client, "search",
                        lambda q, locale="es-ES": ([{"id": "S"}], []))
    monkeypatch.setattr(client, "get_seasons", lambda sid, locale="es-ES": [{"id": "T"}])
    monkeypatch.setattr(client, "get_episodes",
                        lambda sid, locale="es-ES": [
                            {"season_number": 5, "sequence_number": 1,
                             "audio_locale": "es-ES"}])
    assert client.cutoff_for_series("Naruto", [1]) == {}


def test_throttle_window_is_short_enough_to_never_hang_for_an_hour():
    assert CrunchyrollClient._WINDOW_SECONDS <= 120
    assert CrunchyrollClient._MAX_REQUESTS_PER_WINDOW >= 1
