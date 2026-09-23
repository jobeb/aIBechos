import requests

from core.anilist_client import AniListClient, AniListRateLimitError, AniListUnavailableError


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
    "id": 30013,
    "title": {"romaji": "ONE PIECE", "english": "One Piece", "native": "ONE PIECE"},
    "synonyms": ["원피스"],
    "description": "A great pirate story.\n<br><br>(Source: VIZ Media)",
    "coverImage": {"large": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/medium/bx30013.jpg"},
    "startDate": {"year": 1997},
    "format": "MANGA",
}


def test_search_volumes_posts_graphql_query_with_search_variable(monkeypatch):
    client = AniListClient()
    captured = {}

    def fake_post(url, json=None, **kw):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"data": {"Page": {"media": [_SAMPLE_RESULT]}}})

    monkeypatch.setattr(client.session, "post", fake_post)
    results = client.search_volumes("one piece")
    assert results == [_SAMPLE_RESULT]
    assert captured["url"] == "https://graphql.anilist.co"
    assert captured["json"]["variables"]["search"] == "one piece"
    assert "key" not in captured["json"]["variables"]


def test_search_volumes_returns_empty_list_when_no_media(monkeypatch):
    client = AniListClient()
    monkeypatch.setattr(client.session, "post",
                         lambda url, **kw: _FakeResponse({"data": {"Page": {"media": []}}}))
    assert client.search_volumes("algo raro") == []


def test_search_volumes_raises_on_graphql_errors_field(monkeypatch):
    client = AniListClient()
    monkeypatch.setattr(
        client.session, "post",
        lambda url, **kw: _FakeResponse({"errors": [{"message": "Invalid search"}]}))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado ValueError"
    except ValueError as e:
        assert "Invalid search" in str(e)


def test_search_volumes_raises_rate_limit_error_on_429(monkeypatch):
    client = AniListClient()
    monkeypatch.setattr(client.session, "post", lambda url, **kw: _FakeResponse({}, status_code=429))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado AniListRateLimitError"
    except AniListRateLimitError as e:
        assert "límite" in str(e).lower()


def test_search_volumes_raises_friendly_error_on_5xx(monkeypatch):
    client = AniListClient()
    monkeypatch.setattr(client.session, "post", lambda url, **kw: _FakeResponse({}, status_code=503))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado AniListUnavailableError"
    except AniListUnavailableError as e:
        assert "no está disponible" in str(e)


def test_search_volumes_connection_error(monkeypatch):
    client = AniListClient()

    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(client.session, "post", _raise)
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado ConnectionError"
    except ConnectionError as e:
        assert "conexión" in str(e).lower()


def test_build_manga_info_maps_fields_and_strips_html():
    client = AniListClient()
    info = client.build_manga_info(_SAMPLE_RESULT, episode=3)
    assert info.tmdb_id == 30013
    assert info.media_type == "libro"
    assert info.title == "One Piece"
    assert info.year == "1997"
    assert info.genre_ids == ["comic"]
    assert info.episode == 3
    assert "<" not in info.overview
    assert "A great pirate story." in info.overview
    assert info.poster_url == "https://s4.anilist.co/file/anilistcdn/media/manga/cover/medium/bx30013.jpg"


def test_build_manga_info_falls_back_when_english_title_missing():
    client = AniListClient()
    result = {"id": 1, "title": {"romaji": "Wan Piisu", "english": None, "native": "わんぴいす"}}
    info = client.build_manga_info(result)
    assert info.title == "Wan Piisu"


def test_build_manga_info_handles_null_description():
    client = AniListClient()
    result = {"id": 1, "title": {"romaji": "T", "english": None, "native": None}, "description": None}
    info = client.build_manga_info(result)
    assert info.overview == ""


def test_throttle_window_is_short_enough_to_never_hang_for_an_hour():
    assert AniListClient._WINDOW_SECONDS <= 120
    assert AniListClient._MAX_REQUESTS_PER_WINDOW >= 1


def test_throttle_limits_requests_within_window(monkeypatch):
    client = AniListClient()
    client._MAX_REQUESTS_PER_WINDOW = 3
    client._WINDOW_SECONDS = 100.0
    monkeypatch.setattr(client.session, "post",
                         lambda url, **kw: _FakeResponse({"data": {"Page": {"media": []}}}))

    slept = []
    fake_now = [1000.0]
    monkeypatch.setattr("core.anilist_client.time.monotonic", lambda: fake_now[0])

    def fake_sleep(s):
        slept.append(s)
        fake_now[0] += s

    monkeypatch.setattr("core.anilist_client.time.sleep", fake_sleep)

    for _ in range(3):
        client.search_volumes("algo")
    assert slept == []

    client.search_volumes("algo")
    assert len(slept) == 1 and slept[0] > 0
