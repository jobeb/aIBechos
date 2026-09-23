import requests

from core.book_client import GoogleBooksClient, GoogleBooksRateLimitError, GoogleBooksUnavailableError


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._json


def test_search_volumes_returns_items(monkeypatch):
    client = GoogleBooksClient()
    payload = {"items": [{"id": "abc"}, {"id": "def"}]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse(payload))
    results = client.search_volumes("nombre del viento")
    assert results == [{"id": "abc"}, {"id": "def"}]


def test_search_volumes_returns_empty_list_when_no_items(monkeypatch):
    client = GoogleBooksClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}))
    assert client.search_volumes("algo raro") == []


def test_build_book_info_maps_fields():
    client = GoogleBooksClient()
    result = {
        "id": "zyTCAlFPjgYC",
        "volumeInfo": {
            "title": "El Nombre del Viento",
            "authors": ["Patrick Rothfuss"],
            "publishedDate": "2007-03-27",
            "description": "Un joven de talento excepcional...",
            "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=zyTCAlFPjgYC"},
        },
    }
    info = client.build_book_info(result)
    assert info.tmdb_id == "zyTCAlFPjgYC"
    assert info.media_type == "libro"
    assert info.title == "El Nombre del Viento"
    assert info.year == "2007"
    assert info.genre_ids == ["ebook"]
    assert info.season is None
    assert info.episode is None
    assert "Patrick Rothfuss" in info.overview
    assert "Un joven de talento excepcional" in info.overview


def test_build_book_info_upgrades_thumbnail_to_https():
    client = GoogleBooksClient()
    result = {"id": "x", "volumeInfo": {
        "title": "T", "imageLinks": {"thumbnail": "http://books.google.com/img.png"}}}
    info = client.build_book_info(result)
    assert info.poster_url == "https://books.google.com/img.png"


def test_build_book_info_missing_image_links_gives_none_poster():
    client = GoogleBooksClient()
    result = {"id": "x", "volumeInfo": {"title": "Sin portada"}}
    info = client.build_book_info(result)
    assert info.poster_url is None


def test_build_book_info_missing_authors_overview_is_just_description():
    client = GoogleBooksClient()
    result = {"id": "x", "volumeInfo": {"title": "T", "description": "Solo descripción"}}
    info = client.build_book_info(result)
    assert info.overview == "Solo descripción"


def test_search_volumes_raises_friendly_error_on_429(monkeypatch):
    # Bug real: la cuota anónima de Google Books es compartida globalmente,
    # no por IP/equipo -- puede devolver 429 incluso en la primera búsqueda
    # del día si otros usuarios la están agotando, sin que este cliente
    # haya hecho ninguna otra petición antes. Sin el mensaje claro, se
    # mostraba el texto crudo de requests ("429 Client Error: Too Many
    # Requests for url: ...").
    client = GoogleBooksClient()
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=429))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado GoogleBooksRateLimitError"
    except GoogleBooksRateLimitError as e:
        assert "Límite de peticiones" in str(e)


def test_search_volumes_raises_value_error_on_400_when_key_set(monkeypatch):
    # Google Books devuelve 400 (no 401) cuando la key en sí es inválida.
    client = GoogleBooksClient(api_key="mala")
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=400))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado ValueError"
    except ValueError as e:
        assert "inválida" in str(e)


def test_validate_key_false_on_invalid_key(monkeypatch):
    client = GoogleBooksClient(api_key="mala")
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=400))
    assert client.validate_key() is False


def test_validate_key_true_on_success(monkeypatch):
    client = GoogleBooksClient(api_key="buena")
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({"items": []}))
    assert client.validate_key() is True


def test_validate_key_does_not_report_invalid_on_rate_limit(monkeypatch):
    # Un 429 es "inténtalo más tarde", no "la key está mal" -- validate_key
    # no debe devolver False (que la GUI muestra como "API Key inválida")
    # para un problema de cuota transitorio.
    client = GoogleBooksClient(api_key="buena_pero_con_mala_suerte")
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=429))
    assert client.validate_key() is True


def test_search_volumes_raises_friendly_error_on_5xx(monkeypatch):
    # Bug real: una API Key propia y válida devolvió 503 "backendFailed" de
    # forma PERSISTENTE (no un pico puntual) -- fallo del propio servidor de
    # Google, nada que ver con cuota ni con la key. Sin el mensaje claro, se
    # mostraba el texto crudo de requests ("503 Server Error: Service
    # Unavailable for url: ...").
    monkeypatch.setattr("core.book_client.time.sleep", lambda s: None)
    client = GoogleBooksClient(api_key="buena")
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=503))
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado GoogleBooksUnavailableError"
    except GoogleBooksUnavailableError as e:
        assert "no está disponible" in str(e)


def test_search_volumes_retries_on_transient_5xx_then_succeeds(monkeypatch):
    # Bug real: el backend de Google Books falla de forma INTERMITENTE
    # (aprox. la mitad de las peticiones en rachas reales) incluso con key
    # válida -- sin reintento, cada búsqueda tenía una probabilidad alta de
    # fallar aunque el servicio SÍ estuviera respondiendo la mayoría de las
    # veces justo después.
    monkeypatch.setattr("core.book_client.time.sleep", lambda s: None)
    client = GoogleBooksClient(api_key="buena")
    calls = []

    def _fake_get(url, **kw):
        calls.append(1)
        if len(calls) < 3:
            return _FakeResponse({}, status_code=503)
        return _FakeResponse({"items": [{"id": "x"}]}, status_code=200)

    monkeypatch.setattr(client.session, "get", _fake_get)
    results = client.search_volumes("algo")
    assert results == [{"id": "x"}]
    assert len(calls) == 3


def test_search_volumes_gives_up_after_max_5xx_retries(monkeypatch):
    monkeypatch.setattr("core.book_client.time.sleep", lambda s: None)
    client = GoogleBooksClient(api_key="buena")
    calls = []

    def _fake_get(url, **kw):
        calls.append(1)
        return _FakeResponse({}, status_code=503)

    monkeypatch.setattr(client.session, "get", _fake_get)
    try:
        client.search_volumes("algo")
        assert False, "debería haber lanzado GoogleBooksUnavailableError"
    except GoogleBooksUnavailableError:
        pass
    assert len(calls) == client._MAX_5XX_RETRIES + 1


def test_validate_key_does_not_report_invalid_on_server_error(monkeypatch):
    # Mismo criterio que con el 429 -- un 503 no dice nada sobre si la key
    # es válida o no.
    monkeypatch.setattr("core.book_client.time.sleep", lambda s: None)
    client = GoogleBooksClient(api_key="buena")
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({}, status_code=503))
    assert client.validate_key() is True


def test_throttle_limits_requests_within_window(monkeypatch):
    """Autolimitado del lado del cliente (mismo criterio que
    ComicVineClient) -- sin esto, un "Buscar todos" sobre muchos libros en
    secuencia puede superar el límite documentado de la API anónima antes
    de acabar la tanda."""
    client = GoogleBooksClient()
    client._MAX_REQUESTS_PER_WINDOW = 3
    client._WINDOW_SECONDS = 100.0
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse({"items": []}))

    slept = []

    # Congelar el reloj monótono salvo por lo que "duerme" _throttle, para
    # que la 4ª petición dentro del mismo instante SÍ tenga que esperar.
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    def fake_sleep(s):
        slept.append(s)
        fake_now[0] += s

    monkeypatch.setattr("core.book_client.time.monotonic", fake_monotonic)
    monkeypatch.setattr("core.book_client.time.sleep", fake_sleep)

    for _ in range(3):
        client.search_volumes("algo")
    assert slept == []   # las primeras 3 caben dentro del cupo sin esperar

    client.search_volumes("algo")
    assert len(slept) == 1 and slept[0] > 0   # la 4ª tiene que esperar al cupo
