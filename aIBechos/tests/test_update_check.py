import core.update_check as uc


class _FakeResponse:
    def __init__(self, json_data=None, status=200):
        self._json = json_data or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_parse_version_strips_v_prefix_and_extra_text():
    assert uc._parse_version("v1.10.2") == (1, 10, 2)
    assert uc._parse_version("1.2.0-beta") == (1, 2, 0)


def test_check_for_update_returns_none_when_current(monkeypatch):
    monkeypatch.setattr(uc.requests, "get", lambda *a, **kw: _FakeResponse(
        {"tag_name": "v1.1.0", "html_url": "https://example.com/releases/v1.1.0"}))
    assert uc.check_for_update("1.1.0") is None


def test_check_for_update_returns_none_when_older_release(monkeypatch):
    monkeypatch.setattr(uc.requests, "get", lambda *a, **kw: _FakeResponse(
        {"tag_name": "v1.0.0", "html_url": "https://example.com/releases/v1.0.0"}))
    assert uc.check_for_update("1.1.0") is None


def test_check_for_update_returns_tag_and_url_when_newer(monkeypatch):
    monkeypatch.setattr(uc.requests, "get", lambda *a, **kw: _FakeResponse(
        {"tag_name": "v1.2.0", "html_url": "https://example.com/releases/v1.2.0"}))
    result = uc.check_for_update("1.1.0")
    assert result == ("v1.2.0", "https://example.com/releases/v1.2.0")


def test_check_for_update_handles_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(uc.requests, "get", _raise)
    assert uc.check_for_update("1.1.0") is None


def test_check_for_update_handles_missing_fields(monkeypatch):
    monkeypatch.setattr(uc.requests, "get", lambda *a, **kw: _FakeResponse({}))
    assert uc.check_for_update("1.1.0") is None
