import requests

from core.mangadex_client import MangaDexClient, MangaDexUnavailableError


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
    "id": "b70113a5-32a3-44e8-a28f-0e88392808ba",
    "type": "manga",
    "attributes": {
        "title": {"en": "One Piece Academy"},
        "altTitles": [{"ja": "ONE PIECE学園"}, {"en": "One Piece School"}],
        "description": {"en": "A spin off manga.", "vi": "Otro idioma."},
        "year": 2019,
    },
    "relationships": [
        {"id": "author-id", "type": "author"},
        {
            "id": "cover-id",
            "type": "cover_art",
            "attributes": {"fileName": "22f544f1-32fc-4750-a1c4-2c851c876eb1.jpg"},
        },
    ],
}


def test_search_volumes_no_api_key_required_and_expands_cover_art(monkeypatch):
    client = MangaDexClient()
    captured = {}

    def fake_get(url, params=None, **kw):
        captured["params"] = params
        return _FakeResponse({"data": [_SAMPLE_RESULT]})

    monkeypatch.setattr(client.session, "get", fake_get)
    results = client.search_volumes("one piece")
    assert results == [_SAMPLE_RESULT]
    assert "key" not in captured["params"]
    assert captured["params"]["title"] == "one piece"
    assert captured["params"]["includes[]"] == "cover_art"


def test_search_volumes_returns_empty_list_when_no_data(monkeypatch):
    client = MangaDexClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}))
    assert client.search_volumes("algo raro") == []


def test_build_manga_info_maps_fields_and_composes_cover_url():
    client = MangaDexClient()
    info = client.build_manga_info(_SAMPLE_RESULT, episode=5)
    assert info.tmdb_id == "b70113a5-32a3-44e8-a28f-0e88392808ba"
    assert info.media_type == "libro"
    assert info.title == "One Piece Academy"
    assert info.year == "2019"
    assert info.genre_ids == ["comic"]
    assert info.episode == 5
    assert info.overview == "A spin off manga."
    assert info.poster_url == (
        "https://uploads.mangadex.org/covers/b70113a5-32a3-44e8-a28f-0e88392808ba/"
        "22f544f1-32fc-4750-a1c4-2c851c876eb1.jpg.256.jpg"
    )


def test_build_manga_info_falls_back_to_alt_titles_when_title_missing():
    client = MangaDexClient()
    result = {
        "id": "x",
        "attributes": {
            "title": {},
            "altTitles": [{"ja": "何か"}, {"en": "Something"}],
            "description": {},
            "year": None,
        },
        "relationships": [],
    }
    info = client.build_manga_info(result)
    assert info.title == "何か"


def test_build_manga_info_missing_cover_art_gives_none_poster():
    client = MangaDexClient()
    result = {"id": "x", "attributes": {"title": {"en": "T"}}, "relationships": []}
    info = client.build_manga_info(result)
    assert info.poster_url is None


def test_search_volumes_raises_friendly_error_on_persistent_5xx(monkeypatch):
    monkeypatch.setattr("core.mangadex_client.time.sleep", lambda s: None)
    client = MangaDexClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=503))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado MangaDexUnavailableError"
    except MangaDexUnavailableError as e:
        assert "no está disponible" in str(e)


def test_search_volumes_connection_error(monkeypatch):
    client = MangaDexClient()

    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(client.session, "get", _raise)
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado ConnectionError"
    except ConnectionError as e:
        assert "conexión" in str(e).lower()


def test_throttle_window_is_short_enough_to_never_hang_for_an_hour():
    # Mismo criterio que ComicVineClient tras el bug real de esta sesión
    # (ventana de 1 hora -> cuelgue silencioso de "Buscando..."): la espera
    # máxima debe ser de decenas de segundos, no de una hora.
    assert MangaDexClient._WINDOW_SECONDS <= 120
    assert MangaDexClient._MAX_REQUESTS_PER_WINDOW >= 1


def test_throttle_limits_requests_within_window(monkeypatch):
    client = MangaDexClient()
    client._MAX_REQUESTS_PER_WINDOW = 3
    client._WINDOW_SECONDS = 100.0
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({"data": []}))

    slept = []
    fake_now = [1000.0]
    monkeypatch.setattr("core.mangadex_client.time.monotonic", lambda: fake_now[0])

    def fake_sleep(s):
        slept.append(s)
        fake_now[0] += s

    monkeypatch.setattr("core.mangadex_client.time.sleep", fake_sleep)

    for _ in range(3):
        client.search_volumes("algo")
    assert slept == []

    client.search_volumes("algo")
    assert len(slept) == 1 and slept[0] > 0
