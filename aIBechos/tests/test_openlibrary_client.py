import requests

from core.openlibrary_client import OpenLibraryClient, OpenLibraryUnavailableError


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._json


def test_search_volumes_returns_docs(monkeypatch):
    client = OpenLibraryClient()
    payload = {"docs": [{"title": "El Nombre del Viento"}, {"title": "Otro Libro"}]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse(payload))
    results = client.search_volumes("nombre del viento")
    assert results == payload["docs"]


def test_search_volumes_returns_empty_list_when_no_docs(monkeypatch):
    client = OpenLibraryClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}))
    assert client.search_volumes("algo raro") == []


def test_search_volumes_no_api_key_required(monkeypatch):
    # A diferencia de Google Books/ComicVine, nunca se manda ninguna key --
    # confirma que no hay ningún atributo api_key ni parámetro "key" forzado.
    client = OpenLibraryClient()
    captured = {}

    def fake_get(url, params=None, **kw):
        captured["params"] = params
        return _FakeResponse({"docs": []})

    monkeypatch.setattr(client.session, "get", fake_get)
    client.search_volumes("algo")
    assert "key" not in captured["params"]


def test_build_book_info_maps_fields():
    client = OpenLibraryClient()
    result = {
        "key": "/works/OL1815415W",
        "title": "El Nombre del Viento",
        "author_name": ["Patrick Rothfuss"],
        "first_publish_year": 2007,
        "cover_i": 12345,
        "first_sentence": ["Fue una noche de tres partes."],
    }
    info = client.build_book_info(result)
    assert info.tmdb_id == "/works/OL1815415W"
    assert info.media_type == "libro"
    assert info.title == "El Nombre del Viento"
    assert info.year == "2007"
    assert info.genre_ids == ["ebook"]
    assert info.season is None
    assert info.episode is None
    assert info.poster_url == "https://covers.openlibrary.org/b/id/12345-L.jpg"
    assert "Patrick Rothfuss" in info.overview
    assert "Fue una noche de tres partes." in info.overview


def test_build_book_info_missing_cover_gives_none_poster():
    client = OpenLibraryClient()
    result = {"key": "/works/OL1W", "title": "Sin portada"}
    info = client.build_book_info(result)
    assert info.poster_url is None


def test_build_book_info_missing_authors_overview_is_just_first_sentence():
    client = OpenLibraryClient()
    result = {"key": "/works/OL1W", "title": "T", "first_sentence": ["Una frase."]}
    info = client.build_book_info(result)
    assert info.overview == "Una frase."


def test_build_book_info_first_sentence_as_plain_string():
    # La API a veces devuelve first_sentence como string suelto en vez de lista.
    client = OpenLibraryClient()
    result = {"key": "/works/OL1W", "title": "T", "first_sentence": "Una frase suelta."}
    info = client.build_book_info(result)
    assert "Una frase suelta." in info.overview


def test_search_volumes_raises_friendly_error_on_persistent_5xx(monkeypatch):
    monkeypatch.setattr("core.openlibrary_client.time.sleep", lambda s: None)
    client = OpenLibraryClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=503))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado OpenLibraryUnavailableError"
    except OpenLibraryUnavailableError as e:
        assert "no está disponible" in str(e)


def test_search_volumes_retries_on_transient_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr("core.openlibrary_client.time.sleep", lambda s: None)
    client = OpenLibraryClient()
    calls = []

    def _fake_get(url, **kw):
        calls.append(1)
        if len(calls) < 2:
            return _FakeResponse({}, status_code=503)
        return _FakeResponse({"docs": [{"title": "x"}]})

    monkeypatch.setattr(client.session, "get", _fake_get)
    results = client.search_volumes("algo")
    assert results == [{"title": "x"}]
    assert len(calls) == 2


def test_search_volumes_connection_error(monkeypatch):
    client = OpenLibraryClient()

    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(client.session, "get", _raise)
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado ConnectionError"
    except ConnectionError as e:
        assert "conexión" in str(e).lower()


def test_get_work_description_plain_string(monkeypatch):
    client = OpenLibraryClient()
    monkeypatch.setattr(client.session, "get",
                         lambda url, **kw: _FakeResponse({"description": "Una sinopsis directa."}))
    assert client.get_work_description("/works/OL1W") == "Una sinopsis directa."


def test_get_work_description_dict_with_value(monkeypatch):
    client = OpenLibraryClient()
    monkeypatch.setattr(
        client.session, "get",
        lambda url, **kw: _FakeResponse({"description": {"type": "/type/text", "value": "Sinopsis en dict."}}))
    assert client.get_work_description("/works/OL1W") == "Sinopsis en dict."


def test_get_work_description_missing_returns_empty_string(monkeypatch):
    client = OpenLibraryClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}))
    assert client.get_work_description("/works/OL1W") == ""


def test_get_work_description_never_raises_on_failure(monkeypatch):
    client = OpenLibraryClient()

    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(client.session, "get", _raise)
    assert client.get_work_description("/works/OL1W") == ""


def test_throttle_limits_requests_within_window(monkeypatch):
    client = OpenLibraryClient()
    client._MAX_REQUESTS_PER_WINDOW = 3
    client._WINDOW_SECONDS = 100.0
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({"docs": []}))

    slept = []
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    def fake_sleep(s):
        slept.append(s)
        fake_now[0] += s

    monkeypatch.setattr("core.openlibrary_client.time.monotonic", fake_monotonic)
    monkeypatch.setattr("core.openlibrary_client.time.sleep", fake_sleep)

    for _ in range(3):
        client.search_volumes("algo")
    assert slept == []

    client.search_volumes("algo")
    assert len(slept) == 1 and slept[0] > 0
