import requests

from core.kitsu_client import KitsuClient, KitsuUnavailableError


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._json


_SAMPLE_RESULT = {
    "id": "38",
    "type": "manga",
    "attributes": {
        "canonicalTitle": "One Piece",
        "titles": {"en": "One Piece", "ja_jp": "ONE PIECE"},
        "synopsis": "Gol D. Roger was known as the Pirate King.",
        "posterImage": {"small": "https://media.kitsu.app/manga/38/poster_image/small.jpeg",
                         "original": "https://media.kitsu.app/manga/38/poster_image/original.jpg"},
        "startDate": "1997-07-22",
    },
}


def test_client_sends_json_api_accept_header():
    client = KitsuClient()
    assert client.session.headers["Accept"] == "application/vnd.api+json"


def test_search_volumes_no_api_key_required(monkeypatch):
    client = KitsuClient()
    captured = {}

    def fake_get(url, params=None, **kw):
        captured["params"] = params
        return _FakeResponse({"data": [_SAMPLE_RESULT]})

    monkeypatch.setattr(client.session, "get", fake_get)
    results = client.search_volumes("one piece")
    assert results == [_SAMPLE_RESULT]
    assert "key" not in captured["params"]
    assert captured["params"]["filter[text]"] == "one piece"


def test_search_volumes_returns_empty_list_when_no_data(monkeypatch):
    client = KitsuClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}))
    assert client.search_volumes("algo raro") == []


def test_build_manga_info_maps_fields():
    client = KitsuClient()
    info = client.build_manga_info(_SAMPLE_RESULT, episode=2)
    assert info.tmdb_id == "38"
    assert info.media_type == "libro"
    assert info.title == "One Piece"
    assert info.year == "1997"
    assert info.genre_ids == ["comic"]
    assert info.episode == 2
    assert info.overview == "Gol D. Roger was known as the Pirate King."
    assert info.poster_url == "https://media.kitsu.app/manga/38/poster_image/small.jpeg"


def test_build_manga_info_falls_back_to_titles_en_when_canonical_missing():
    client = KitsuClient()
    result = {"id": "1", "attributes": {"titles": {"en": "Fallback Title"}, "startDate": "2020-01-01"}}
    info = client.build_manga_info(result)
    assert info.title == "Fallback Title"


def test_build_manga_info_missing_poster_gives_none():
    client = KitsuClient()
    result = {"id": "1", "attributes": {"canonicalTitle": "T"}}
    info = client.build_manga_info(result)
    assert info.poster_url is None


def test_search_volumes_raises_friendly_error_on_persistent_5xx(monkeypatch):
    monkeypatch.setattr("core.kitsu_client.time.sleep", lambda s: None)
    client = KitsuClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=503))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado KitsuUnavailableError"
    except KitsuUnavailableError as e:
        assert "no está disponible" in str(e)


def test_search_volumes_connection_error(monkeypatch):
    client = KitsuClient()

    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(client.session, "get", _raise)
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado ConnectionError"
    except ConnectionError as e:
        assert "conexión" in str(e).lower()


def test_throttle_window_is_short_enough_to_never_hang_for_an_hour():
    assert KitsuClient._WINDOW_SECONDS <= 120
    assert KitsuClient._MAX_REQUESTS_PER_WINDOW >= 1


def test_throttle_limits_requests_within_window(monkeypatch):
    client = KitsuClient()
    client._MAX_REQUESTS_PER_WINDOW = 3
    client._WINDOW_SECONDS = 100.0
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({"data": []}))

    slept = []
    fake_now = [1000.0]
    monkeypatch.setattr("core.kitsu_client.time.monotonic", lambda: fake_now[0])

    def fake_sleep(s):
        slept.append(s)
        fake_now[0] += s

    monkeypatch.setattr("core.kitsu_client.time.sleep", fake_sleep)

    for _ in range(3):
        client.search_volumes("algo")
    assert slept == []

    client.search_volumes("algo")
    assert len(slept) == 1 and slept[0] > 0
