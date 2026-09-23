import json
import json as _json

import core.missing_episodes_ai as mea


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _groq_payload(content: str, total_tokens=42):
    return {"choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": total_tokens}}


def test_returns_empty_without_api_key():
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    assert mea.analyze_missing_episodes(shows, api_key="") == {}


def test_returns_empty_without_shows():
    assert mea.analyze_missing_episodes([], api_key="fake-key") == {}


def test_successful_call_returns_verdicts(monkeypatch):
    content = json.dumps({"veredictos": [
        {"tmdb_id": 73021, "veredicto": "numeracion_distinta", "motivo": "Publicada en dos partes"},
        {"tmdb_id": 1396, "veredicto": "hueco_real", "motivo": "Faltan episodios sueltos"},
    ]})
    monkeypatch.setattr(mea.requests, "post", lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    shows = [
        {"tmdb_id": 73021, "name": "Disenchantment", "tmdb_seasons": {"1": 20}, "server_seasons": {"1": 10, "2": 10}},
        {"tmdb_id": 1396, "name": "Breaking Bad", "tmdb_seasons": {"2": 13}, "server_seasons": {"2": 10}},
    ]
    result = mea.analyze_missing_episodes(shows, api_key="fake-key")
    assert result == {
        73021: {"veredicto": "numeracion_distinta", "motivo": "Publicada en dos partes"},
        1396: {"veredicto": "hueco_real", "motivo": "Faltan episodios sueltos"},
    }


def test_network_failure_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(mea.requests, "post", _raise)
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    assert mea.analyze_missing_episodes(shows, api_key="fake-key") == {}


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setattr(mea.requests, "post",
                        lambda *a, **kw: _FakeResponse(_groq_payload("no es json valido")))
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    assert mea.analyze_missing_episodes(shows, api_key="fake-key") == {}


def test_ignores_entries_with_unknown_verdict_value(monkeypatch):
    content = json.dumps({"veredictos": [
        {"tmdb_id": 1, "veredicto": "no_estoy_seguro", "motivo": "..."},
        {"tmdb_id": 2, "veredicto": "hueco_real", "motivo": "ok"},
    ]})
    monkeypatch.setattr(mea.requests, "post", lambda *a, **kw: _FakeResponse(_groq_payload(content)))
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}},
             {"tmdb_id": 2, "name": "Y", "tmdb_seasons": {}, "server_seasons": {}}]
    result = mea.analyze_missing_episodes(shows, api_key="fake-key")
    assert result == {2: {"veredicto": "hueco_real", "motivo": "ok"}}


def test_ignores_entries_missing_tmdb_id(monkeypatch):
    content = json.dumps({"veredictos": [{"veredicto": "hueco_real", "motivo": "sin id"}]})
    monkeypatch.setattr(mea.requests, "post", lambda *a, **kw: _FakeResponse(_groq_payload(content)))
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    assert mea.analyze_missing_episodes(shows, api_key="fake-key") == {}


def test_optional_context_fields_included_when_present(monkeypatch):
    sent_payloads = []
    def _fake_post(url, headers, json, timeout):
        sent_payloads.append(json["messages"][1]["content"])
        content = _json.dumps({"veredictos": [{"tmdb_id": 30984, "veredicto": "hueco_real", "motivo": "x"}]})
        return _FakeResponse(_groq_payload(content))
    monkeypatch.setattr(mea.requests, "post", _fake_post)

    shows = [{"tmdb_id": 30984, "name": "Bleach", "original_name": "BLEACH",
              "first_air_date": "2004-10-05", "origin_country": ["JP"],
              "genres": ["Animation"], "tmdb_seasons": {"1": 366}, "server_seasons": {"1": 109},
              "missing_episodes": {"1": [110, 111, 112]},
              "present_episodes": {"1": list(range(1, 110))},
              "ftp_filenames": ["Bleach 1x01.mkv", "Bleach 1x02-1x03.mkv"]}]
    mea.analyze_missing_episodes(shows, api_key="fake-key")

    sent = _json.loads(sent_payloads[0])[0]
    assert sent["original_name"] == "BLEACH"
    assert sent["first_air_date"] == "2004-10-05"
    assert sent["origin_country"] == ["JP"]
    assert sent["genres"] == ["Animation"]
    assert sent["missing_episodes"] == {"1": [110, 111, 112]}
    assert sent["present_episodes"] == {"1": list(range(1, 110))}
    assert sent["ftp_filenames"] == ["Bleach 1x01.mkv", "Bleach 1x02-1x03.mkv"]


def test_optional_context_fields_omitted_when_absent(monkeypatch):
    sent_payloads = []
    def _fake_post(url, headers, json, timeout):
        sent_payloads.append(json["messages"][1]["content"])
        content = _json.dumps({"veredictos": [{"tmdb_id": 1, "veredicto": "hueco_real", "motivo": "x"}]})
        return _FakeResponse(_groq_payload(content))
    monkeypatch.setattr(mea.requests, "post", _fake_post)

    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    mea.analyze_missing_episodes(shows, api_key="fake-key")

    sent = _json.loads(sent_payloads[0])[0]
    for key in ("original_name", "first_air_date", "origin_country", "genres",
                "missing_episodes", "present_episodes", "ftp_filenames"):
        assert key not in sent


def test_check_spanish_dub_false_never_includes_dub_field(monkeypatch):
    # Aunque Groq devuelva "doblaje_castellano" por su cuenta, si no se pidió
    # (check_spanish_dub=False) no debe colarse en el resultado -- ni
    # siquiera debe pedirse el campo en el prompt.
    sent_prompts = []
    def _fake_post(url, headers, json, timeout):
        sent_prompts.append(json["messages"][0]["content"])
        content = _json.dumps({"veredictos": [
            {"tmdb_id": 1, "veredicto": "hueco_real", "motivo": "ok",
             "doblaje_castellano": {"1": 109}},
        ]})
        return _FakeResponse(_groq_payload(content))
    monkeypatch.setattr(mea.requests, "post", _fake_post)
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    result = mea.analyze_missing_episodes(shows, api_key="fake-key", check_spanish_dub=False)
    assert result == {1: {"veredicto": "hueco_real", "motivo": "ok"}}
    assert "doblaje_castellano" not in sent_prompts[0]


def test_check_spanish_dub_true_parses_dub_cutoff(monkeypatch):
    content = json.dumps({"veredictos": [
        {"tmdb_id": 30984, "veredicto": "hueco_real", "motivo": "Bleach",
         "doblaje_castellano": {"1": 109}},
        {"tmdb_id": 1396, "veredicto": "hueco_real", "motivo": "sin recorte"},
    ]})
    monkeypatch.setattr(mea.requests, "post", lambda *a, **kw: _FakeResponse(_groq_payload(content)))
    shows = [{"tmdb_id": 30984, "name": "Bleach", "tmdb_seasons": {"1": 366}, "server_seasons": {"1": 109}},
             {"tmdb_id": 1396, "name": "Breaking Bad", "tmdb_seasons": {}, "server_seasons": {}}]
    result = mea.analyze_missing_episodes(shows, api_key="fake-key", check_spanish_dub=True)
    assert result[30984]["doblaje_castellano"] == {1: 109}
    assert result[1396]["doblaje_castellano"] == {}


def test_check_spanish_dub_true_warns_against_using_present_episodes_as_dub_cutoff(monkeypatch):
    # Caso real: Arcadia -- la IA dijo que el doblaje castellano cortaba
    # justo donde el usuario tenía descargado (T2 hasta el 2x02), cuando
    # en realidad toda la serie está doblada. El prompt debe dejar claro
    # que missing_episodes/present_episodes son solo lo que hay
    # descargado, no un dato real sobre el doblaje.
    sent_prompts = []
    def _fake_post(url, headers, json, timeout):
        sent_prompts.append(json["messages"][0]["content"])
        content = _json.dumps({"veredictos": [{"tmdb_id": 1, "veredicto": "hueco_real", "motivo": "x"}]})
        return _FakeResponse(_groq_payload(content))
    monkeypatch.setattr(mea.requests, "post", _fake_post)
    shows = [{"tmdb_id": 1, "name": "Arcadia", "tmdb_seasons": {}, "server_seasons": {},
              "present_episodes": {"2": [1, 2]}}]
    mea.analyze_missing_episodes(shows, api_key="fake-key", check_spanish_dub=True)
    assert "NUNCA son un dato" in sent_prompts[0]
    assert "último episodio presente en el servidor" in sent_prompts[0]


def test_check_spanish_dub_true_ignores_malformed_dub_field(monkeypatch):
    content = json.dumps({"veredictos": [
        {"tmdb_id": 1, "veredicto": "hueco_real", "motivo": "x", "doblaje_castellano": "no es un objeto"},
    ]})
    monkeypatch.setattr(mea.requests, "post", lambda *a, **kw: _FakeResponse(_groq_payload(content)))
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]
    result = mea.analyze_missing_episodes(shows, api_key="fake-key", check_spanish_dub=True)
    assert result == {1: {"veredicto": "hueco_real", "motivo": "x", "doblaje_castellano": {}}}


def test_prompt_instructs_motivo_consistent_with_missing_episodes(monkeypatch):
    # Caso real reportado: una serie con episodios dobles fusionados en un
    # solo archivo en las temporadas 1-3 (server_seasons no encaja con
    # tmdb_seasons ahí, pero missing_episodes no las incluye porque no
    # falta nada de verdad) y un hueco real solo en la temporada 4 -- la IA
    # respondió con un "motivo" que generalizaba a "todas las temporadas",
    # contradiciendo la lista (ya correcta). El prompt debe dejar claro que
    # el motivo tiene que ceñirse a las temporadas que sí aparecen en
    # missing_episodes.
    sent_prompts = []
    def _fake_post(url, headers, json, timeout):
        sent_prompts.append(json["messages"][0]["content"])
        content = _json.dumps({"veredictos": [{"tmdb_id": 1, "veredicto": "hueco_real", "motivo": "T4"}]})
        return _FakeResponse(_groq_payload(content))
    monkeypatch.setattr(mea.requests, "post", _fake_post)
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {},
              "missing_episodes": {"4": [1, 2, 3]}}]
    mea.analyze_missing_episodes(shows, api_key="fake-key")
    assert "missing_episodes" in sent_prompts[0]
    assert "coherente" in sent_prompts[0]
    assert "todas las temporadas" in sent_prompts[0]


def test_check_spanish_dub_true_uses_dub_check_model_not_caller_model(monkeypatch):
    # Probado en vivo contra la API real de Groq (ver el comentario de
    # DUB_CHECK_MODEL): el modelo por defecto (y uno más pequeño) copian
    # el límite de present_episodes en vez de saber el dato real --
    # openai/gpt-oss-120b fue el único que no lo hizo. La pregunta de
    # doblaje debe usar SIEMPRE ese modelo, ignorando el *model* que pase
    # el llamador -- solo aplica cuando check_spanish_dub=True.
    sent_models = []
    def _fake_post(url, headers, json, timeout):
        sent_models.append(json["model"])
        content = _json.dumps({"veredictos": [{"tmdb_id": 1, "veredicto": "hueco_real", "motivo": "x"}]})
        return _FakeResponse(_groq_payload(content))
    monkeypatch.setattr(mea.requests, "post", _fake_post)
    shows = [{"tmdb_id": 1, "name": "X", "tmdb_seasons": {}, "server_seasons": {}}]

    mea.analyze_missing_episodes(shows, api_key="fake-key", model="modelo-normal", check_spanish_dub=False)
    mea.analyze_missing_episodes(shows, api_key="fake-key", model="modelo-normal", check_spanish_dub=True)

    assert sent_models == ["modelo-normal", mea.DUB_CHECK_MODEL]
    assert mea.DUB_CHECK_MODEL != "modelo-normal"
