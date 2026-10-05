import requests

from core.rtve_client import (
    RTVEClient,
    RTVERateLimitError,
    RTVEUnavailableError,
    _ep_numbers,
    _match_ratio,
    _names_match,
    _parse_program_page,
    _slugify,
    cutoff_from_episodes,
    episode_is_castilian,
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


_PROGRAM_PAGE = ('<html><head><meta name="DC.identifier" content="1573" />'
                 '<meta property="og:title" content="Cuéntame cómo pasó" />'
                 "</head></html>")

_PROGRAM_RECORD = {"id": "1573", "name": "Cuéntame cómo pasó",
                   "language": "es", "numSeasons": 23,
                   "seasons": [{"orden": 1, "numEpisodes": 33},
                               {"orden": 2, "numEpisodes": 14}]}

_EP_ES = {"id": "1", "episode": 1, "temporadaOrden": 1, "language": "es",
          "type": {"id": 39816, "name": "Completo"}}
_EP_CA = {"id": "2", "episode": 2, "temporadaOrden": 1, "language": "ca",
          "type": {"id": 39816, "name": "Completo"}}


def test_slugify_removes_accents_and_normalizes():
    assert _slugify("Cuéntame cómo pasó") == "cuentame-como-paso"
    assert _slugify("  La  Promesa!! ") == "la-promesa"
    assert _slugify("") == ""


def test_parse_program_page_extracts_id_and_title():
    assert _parse_program_page(_PROGRAM_PAGE) == ("1573",
                                                  "Cuéntame cómo pasó")
    assert _parse_program_page("<html>sin ficha</html>") == ("", "")
    assert _parse_program_page("") == ("", "")


def test_names_match_tolerates_suffixes_but_not_short_substrings():
    assert _names_match("Cuéntame cómo pasó",
                        "Cuéntame cómo pasó - Programa") is True
    assert _names_match("La promesa", "La Promesa") is True
    # "I+" cabe en "breaking bad" pero no es el mismo programa.
    assert _names_match("Breaking Bad", "I+") is False
    assert _names_match("", "Algo") is False


def test_match_ratio_guards_short_substrings():
    assert _match_ratio("breaking bad", "i") < 0.7
    assert _match_ratio("cuentame como paso",
                        "cuentame como paso programa") == 1.0


def test_episode_is_castilian_accepts_es():
    assert episode_is_castilian(_EP_ES) is True
    assert episode_is_castilian({"qualities": [{"language": "es"}]}) is True


def test_episode_is_castilian_rejects_non_spanish_only():
    assert episode_is_castilian(_EP_CA) is False


def test_episode_is_castilian_returns_none_without_language():
    assert episode_is_castilian({}) is None
    assert episode_is_castilian(None) is None


def test_ep_numbers_rejects_clips_without_season():
    assert _ep_numbers(_EP_ES) == (1, 1)
    assert _ep_numbers({"episode": 0, "temporadaOrden": None}) == (None, None)
    assert _ep_numbers({}) == (None, None)


def test_ep_numbers_defaults_to_single_season():
    video = {"episode": 7, "temporadaOrden": None, "language": "es"}
    assert _ep_numbers(video, default_season=1) == (1, 7)
    # Sin temporada por defecto no se adivina (multi-temporada).
    assert _ep_numbers(video) == (None, None)
    # Los clips (episodio 0) nunca entran, ni con defecto.
    assert _ep_numbers({"episode": 0}, default_season=1) == (None, None)


def test_cutoff_from_episodes_only_counts_initial_dubbed_run():
    verdicts = {(1, 1): True, (1, 2): True, (1, 3): False, (1, 4): True}
    assert cutoff_from_episodes(verdicts) == {1: 2}
    assert cutoff_from_episodes({}) == {}


def _client_with_program(monkeypatch, record=None):
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")
    monkeypatch.setattr(client, "program_by_slug",
                        lambda q: record)
    monkeypatch.setattr(client, "program_by_index",
                        lambda q: None)
    return client


def test_program_by_slug_returns_verified_record(monkeypatch):
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")

    def fake_get(url, **kw):
        if url.endswith("/play/videos/cuentame-como-paso/"):
            return _FakeResponse(text=_PROGRAM_PAGE)
        if url.endswith("/programas/1573.json"):
            return _FakeResponse({"page": {"items": [_PROGRAM_RECORD]}})
        raise AssertionError(f"GET inesperado: {url}")

    monkeypatch.setattr(client.session, "get", fake_get)
    assert client.program_by_slug("Cuéntame cómo pasó") == _PROGRAM_RECORD


def test_program_by_slug_returns_none_on_404(monkeypatch):
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")
    monkeypatch.setattr(client.session, "get",
                        lambda url, **kw: _FakeResponse(status_code=404))
    assert client.program_by_slug("Serie Inexistente Xyz") is None


def test_program_by_slug_rejects_title_mismatch(monkeypatch):
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")

    def fake_get(url, **kw):
        if "play/videos" in url:
            return _FakeResponse(
                text='<html><meta name="DC.identifier" content="99" />'
                     '<meta property="og:title" content="Otro programa" />'
                     "</html>")
        return _FakeResponse({"page": {"items": [
            {"id": "99", "name": "Otro programa"}]}})

    monkeypatch.setattr(client.session, "get", fake_get)
    assert client.program_by_slug("Cuéntame cómo pasó") is None


def test_get_full_videos_paginates(monkeypatch):
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")
    seen = []

    def fake_get(url, **kw):
        seen.append(kw.get("params", {}).get("page"))
        assert kw.get("params", {}).get("type") == 39816
        if kw.get("params", {}).get("page") == 1:
            return _FakeResponse({"page": {"items": [_EP_ES],
                                           "totalPages": 2}})
        return _FakeResponse({"page": {"items": [_EP_CA],
                                       "totalPages": 2}})

    monkeypatch.setattr(client.session, "get", fake_get)
    videos = client.get_full_videos("1573")
    assert videos == [_EP_ES, _EP_CA]
    assert seen == [1, 2]


def test_program_seasons_reads_sizes():
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")
    assert client.program_seasons(_PROGRAM_RECORD) == {1: 33, 2: 14}
    assert client.program_seasons({}) == {}


def test_cutoff_for_series_builds_cutoff(monkeypatch):
    client = _client_with_program(monkeypatch, _PROGRAM_RECORD)
    monkeypatch.setattr(
        client, "get_full_videos",
        lambda pid: [_EP_ES, _EP_CA,
                     {"episode": 3, "temporadaOrden": 1, "language": "es"}])
    # E02 solo en catalán frena el tramo: corte en 1.
    assert client.cutoff_for_series("Cuéntame cómo pasó", [1]) == {1: 1}


def test_cutoff_for_series_defaults_single_season_program(monkeypatch):
    record = {"id": "1031120", "name": "Dragon Ball DAIMA"}
    client = _client_with_program(monkeypatch, record)
    monkeypatch.setattr(
        client, "get_full_videos",
        lambda pid: [{"episode": n, "temporadaOrden": None, "language": "es"}
                     for n in (1, 2, 3)])
    assert client.cutoff_for_series("Dragon Ball Daima", [1]) == {1: 3}


def test_cutoff_for_series_no_default_in_multi_season_program(monkeypatch):
    client = _client_with_program(monkeypatch, _PROGRAM_RECORD)
    monkeypatch.setattr(
        client, "get_full_videos",
        lambda pid: [{"episode": 1, "temporadaOrden": None,
                      "language": "es"}])
    # Cuéntame tiene 23 temporadas: sin temporada no se atribuye.
    assert client.cutoff_for_series("Cuéntame cómo pasó", [1]) == {}


def test_cutoff_for_series_returns_empty_without_program(monkeypatch):
    client = _client_with_program(monkeypatch, None)
    assert client.cutoff_for_series("Serie Inexistente Xyz", [1]) == {}


def test_cutoff_for_series_ignores_seasons_out_of_scope(monkeypatch):
    client = _client_with_program(monkeypatch, _PROGRAM_RECORD)
    monkeypatch.setattr(client, "get_full_videos", lambda pid: [_EP_ES])
    assert client.cutoff_for_series("Cuéntame cómo pasó", [2]) == {}


def test_cutoff_for_series_never_raises(monkeypatch):
    client = _client_with_program(monkeypatch, _PROGRAM_RECORD)

    def _boom(pid):
        raise ConnectionError("Sin conexión a internet.")

    monkeypatch.setattr(client, "get_full_videos", _boom)
    assert client.cutoff_for_series("Cuéntame cómo pasó", [1]) == {}


def test_rate_limit_error_on_429(monkeypatch):
    client = RTVEClient(index_path="/nonexistent/rtve_index.json")
    monkeypatch.setattr(client.session, "get",
                        lambda url, **kw: _FakeResponse(status_code=429))
    try:
        client.program_by_slug("Algo")
        assert False, "debería haber lanzado RTVERateLimitError"
    except RTVERateLimitError as e:
        assert "límite" in str(e).lower()


def test_index_refresh_writes_slim_entries_and_stays_fresh(tmp_path):
    client = RTVEClient(index_path=str(tmp_path / "idx.json"))
    assert client._load_index() == []
    assert client._index_is_fresh() is False

    def fake_json(url, **kw):
        return {"page": {"items": [
            {"id": 1573, "name": "Cuéntame cómo pasó",
             "description": "texto largo que no se guarda"},
            {"id": 169690, "name": "La Promesa"}],
            "totalPages": 1}}

    import unittest.mock as mock
    with mock.patch.object(client, "_get_json", side_effect=fake_json):
        items = client.refresh_index()
    assert items == [{"id": "1573", "name": "Cuéntame cómo pasó"},
                     {"id": "169690", "name": "La Promesa"}]

    fresh = RTVEClient(index_path=str(tmp_path / "idx.json"))
    assert fresh._index_is_fresh() is True
    assert fresh._load_index() == items


def test_program_by_index_finds_close_match(tmp_path):
    import json as _json
    path = tmp_path / "idx.json"
    path.write_text(_json.dumps(
        {"checked_at": 9999999999.0,
         "items": [{"id": "1573", "name": "Cuéntame cómo pasó"}]}),
        encoding="utf-8")
    client = RTVEClient(index_path=str(path))
    import unittest.mock as mock
    with mock.patch.object(
            client, "_get_json",
            return_value={"page": {"items": [_PROGRAM_RECORD]}}):
        assert client.program_by_index("cuentame como paso") == \
            _PROGRAM_RECORD


def test_program_by_index_returns_none_without_match(tmp_path):
    import json as _json
    path = tmp_path / "idx.json"
    path.write_text(_json.dumps(
        {"checked_at": 9999999999.0,
         "items": [{"id": "9", "name": "Telediario"}]}),
        encoding="utf-8")
    client = RTVEClient(index_path=str(path))
    assert client.program_by_index("Breaking Bad") is None


def test_throttle_window_is_short_enough_to_never_hang_for_an_hour():
    assert RTVEClient._WINDOW_SECONDS <= 120
    assert RTVEClient._MAX_REQUESTS_PER_WINDOW >= 1
