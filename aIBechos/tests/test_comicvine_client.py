from core.comicvine_client import ComicVineClient


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_search_volumes_always_sends_api_key_and_json_format(monkeypatch):
    client = ComicVineClient(api_key="secret")
    captured = {}

    def fake_get(url, params=None, **kw):
        captured["params"] = params
        return _FakeResponse({"status_code": 1, "results": [{"id": 1}]})

    monkeypatch.setattr(client.session, "get", fake_get)
    client.search_volumes("avatar")
    assert captured["params"]["api_key"] == "secret"
    assert captured["params"]["format"] == "json"
    assert captured["params"]["query"] == "avatar"


def test_client_sets_a_non_default_user_agent():
    client = ComicVineClient(api_key="x")
    assert "python-requests" not in client.session.headers["User-Agent"]


def test_search_volumes_raises_on_comicvine_error_status_code(monkeypatch):
    # ComicVine devuelve HTTP 200 con su propio status_code de error en el
    # cuerpo (p.ej. una API key inválida) -- sin comprobar esto, una key
    # mala parecería una búsqueda sin resultados en vez de un error real.
    client = ComicVineClient(api_key="mala")
    monkeypatch.setattr(
        client.session, "get",
        lambda url, **kw: _FakeResponse({"status_code": 100, "error": "Invalid API Key"}))
    try:
        client.search_volumes("avatar")
        assert False, "debería haber lanzado ValueError"
    except ValueError as e:
        assert "Invalid API Key" in str(e)


def test_search_volumes_returns_results_list(monkeypatch):
    client = ComicVineClient(api_key="x")
    payload = {"status_code": 1, "results": [{"id": 1, "name": "Avatar"}]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeResponse(payload))
    assert client.search_volumes("avatar") == [{"id": 1, "name": "Avatar"}]


def test_build_comic_info_maps_fields_and_uses_local_episode():
    client = ComicVineClient(api_key="x")
    result = {
        "id": 12345,
        "volume": {"name": "Avatar - The Last Airbender - The Promise"},
        "start_year": "2012",
        "image": {"small_url": "https://comicvine.gamespot.com/img.jpg"},
        "description": "<p>Some <b>HTML</b> description</p>",
    }
    info = client.build_comic_info(result, episode=1)
    assert info.tmdb_id == 12345
    assert info.media_type == "libro"
    assert info.title == "Avatar - The Last Airbender - The Promise"
    assert info.year == "2012"
    assert info.genre_ids == ["comic"]
    assert info.season is None
    assert info.episode == 1
    assert "<" not in info.overview
    assert "Some HTML description" in info.overview


def test_build_comic_info_falls_back_to_top_level_name():
    client = ComicVineClient(api_key="x")
    result = {"id": 1, "name": "Solo Name, sin volume"}
    info = client.build_comic_info(result)
    assert info.title == "Solo Name, sin volume"
    assert info.episode is None


def test_throttle_window_is_short_enough_to_never_hang_for_an_hour():
    # Bug real: la ventana era 150 peticiones / 3600s (1 hora) -- una
    # identificación en bloque de una colección grande (~25 cómics, más
    # reintentos/IA) agotaba las 150 en minutos, y cualquier búsqueda
    # posterior se quedaba bloqueada en _throttle() sin aviso hasta que se
    # liberase hueco en ESA hora entera -- desde la GUI eso se veía como
    # "Buscando..." colgado indefinidamente. La espera máxima ahora debe
    # ser de decenas de segundos, no de una hora.
    assert ComicVineClient._WINDOW_SECONDS <= 120
    assert ComicVineClient._MAX_REQUESTS_PER_WINDOW >= 1


def test_throttle_blocks_once_window_is_full_and_logs_the_wait(monkeypatch):
    client = ComicVineClient(api_key="x")
    client._MAX_REQUESTS_PER_WINDOW = 2
    client._WINDOW_SECONDS = 60.0

    fake_now = [1000.0]
    monkeypatch.setattr("core.comicvine_client.time.monotonic", lambda: fake_now[0])

    slept = []
    def fake_sleep(seconds):
        slept.append(seconds)
        fake_now[0] += seconds  # el tiempo "avanza" al dormir
    monkeypatch.setattr("core.comicvine_client.time.sleep", fake_sleep)

    logged = []
    monkeypatch.setattr("core.comicvine_client._log.warning",
                         lambda *a, **k: logged.append((a, k)))

    client._throttle()  # 1ª petición: hueco libre
    client._throttle()  # 2ª petición: hueco libre, ventana ahora llena
    client._throttle()  # 3ª petición: debe esperar a que la ventana libere hueco

    assert slept, "la 3a peticion deberia haber tenido que esperar"
    assert logged, "debe quedar constancia en el log de que se esperó (antes no se registraba nada)"
