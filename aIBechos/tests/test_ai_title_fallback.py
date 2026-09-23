import json
import logging

import core.ai_title_fallback as aif
from core import applog


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


def _isolated_logger(monkeypatch, tmp_path):
    """Log rotativo aislado en tmp_path, sin handlers de sesiones anteriores.
    El módulo ya no monta su propio RotatingFileHandler: usa core/applog.py,
    que comparte un handler por FICHERO para que la rotación funcione en
    Windows (ver test_applog_rotation.py), así que se aísla ahí."""
    monkeypatch.setattr(applog, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(applog, "_handlers", {})
    logging.getLogger("aIBechos.ai_fallback").handlers.clear()
    aif._log = applog.get_logger("aIBechos.ai_fallback", "ai_fallback.log")
    return aif._log


def test_returns_none_without_api_key():
    assert aif.guess_title_via_ai("Cualquier.Cosa.WEBDL", api_key="") is None


def test_successful_call_returns_title_and_tokens(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    content = json.dumps({"title": "La Mansion Embrujada",
                           "junk_tokens": ["HDTS", "Line", "Dual"]})
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    result = aif.guess_title_via_ai("La.Mansion.Embrujada.HDTS.Line.Dual", api_key="fake-key")
    assert result == {"title": "La Mansion Embrujada", "junk_tokens": ["HDTS", "Line", "Dual"]}


def test_network_failure_returns_none(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(aif.requests, "post", _raise)

    assert aif.guess_title_via_ai("Algo.mkv", api_key="fake-key") is None


def test_malformed_json_returns_none(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload("no es json valido")))

    assert aif.guess_title_via_ai("Algo.mkv", api_key="fake-key") is None


def test_empty_title_returns_none(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    content = json.dumps({"title": "", "junk_tokens": []})
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    assert aif.guess_title_via_ai("Algo.mkv", api_key="fake-key") is None


def test_every_call_gets_logged(monkeypatch, tmp_path):
    log = _isolated_logger(monkeypatch, tmp_path)
    content = json.dumps({"title": "Titulo", "junk_tokens": ["X"]})
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    aif.guess_title_via_ai("Nombre.De.Archivo", api_key="fake-key")

    for h in log.handlers:
        h.flush()
    log_path = tmp_path / "ai_fallback.log"
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "Nombre.De.Archivo" in text
    assert "Consulta a Groq" in text
    assert "Titulo" in text   # tambien queda registrado el resultado


def test_failed_call_also_gets_logged(monkeypatch, tmp_path):
    log = _isolated_logger(monkeypatch, tmp_path)
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(aif.requests, "post", _raise)

    aif.guess_title_via_ai("Nombre.Que.Falla", api_key="fake-key")

    for h in log.handlers:
        h.flush()
    text = (tmp_path / "ai_fallback.log").read_text(encoding="utf-8")
    assert "Nombre.Que.Falla" in text
    assert "Fallo la consulta" in text


def test_validate_api_key_false_without_key():
    assert aif.validate_api_key("") is False


def test_validate_api_key_true_on_200(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    monkeypatch.setattr(aif.requests, "get", lambda *a, **kw: _FakeResponse({}, status=200))
    assert aif.validate_api_key("fake-key") is True


def test_validate_api_key_false_on_401(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    monkeypatch.setattr(aif.requests, "get", lambda *a, **kw: _FakeResponse({}, status=401))
    assert aif.validate_api_key("fake-key") is False


def test_validate_api_key_false_on_network_error(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(aif.requests, "get", _raise)
    assert aif.validate_api_key("fake-key") is False


def test_validate_api_key_gets_logged(monkeypatch, tmp_path):
    log = _isolated_logger(monkeypatch, tmp_path)
    monkeypatch.setattr(aif.requests, "get", lambda *a, **kw: _FakeResponse({}, status=200))
    aif.validate_api_key("fake-key")
    for h in log.handlers:
        h.flush()
    text = (tmp_path / "ai_fallback.log").read_text(encoding="utf-8")
    assert "Validando API Key de Groq" in text


# ── guess_original_comic_title_via_ai (traducción de título para ComicVine) ──

def test_comic_translate_returns_none_without_api_key():
    assert aif.guess_original_comic_title_via_ai("La Promesa", api_key="") is None


def test_comic_translate_successful_call_returns_original_title(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    content = json.dumps({"original_title": "The Promise"})
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    result = aif.guess_original_comic_title_via_ai("La Promesa", api_key="fake-key")
    assert result == "The Promise"


def test_comic_translate_network_failure_returns_none(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(aif.requests, "post", _raise)

    assert aif.guess_original_comic_title_via_ai("La Promesa", api_key="fake-key") is None


def test_comic_translate_malformed_json_returns_none(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload("no es json valido")))

    assert aif.guess_original_comic_title_via_ai("La Promesa", api_key="fake-key") is None


def test_comic_translate_empty_title_returns_none(monkeypatch, tmp_path):
    _isolated_logger(monkeypatch, tmp_path)
    content = json.dumps({"original_title": ""})
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    assert aif.guess_original_comic_title_via_ai("La Promesa", api_key="fake-key") is None


def test_comic_translate_call_gets_logged(monkeypatch, tmp_path):
    log = _isolated_logger(monkeypatch, tmp_path)
    content = json.dumps({"original_title": "The Promise"})
    monkeypatch.setattr(aif.requests, "post",
                         lambda *a, **kw: _FakeResponse(_groq_payload(content)))

    aif.guess_original_comic_title_via_ai("La Promesa", api_key="fake-key")

    for h in log.handlers:
        h.flush()
    text = (tmp_path / "ai_fallback.log").read_text(encoding="utf-8")
    assert "La Promesa" in text
    assert "The Promise" in text
