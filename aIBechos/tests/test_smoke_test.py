"""--smoke-test (lo corre el CI sobre el ejecutable compilado)."""

from core import smoke_test


def test_arranque_ok(qapp, tmp_path, monkeypatch):
    out = tmp_path / "smoke.txt"
    monkeypatch.setenv("AIBECHOS_SMOKE_OUT", str(out))
    assert smoke_test.run() == 0
    assert out.read_text(encoding="utf-8").strip().endswith("RESULT OK")


def test_falta_un_modulo_falla(qapp, monkeypatch):
    monkeypatch.setattr(smoke_test, "LATE_IMPORTS", ("modulo_que_no_existe",))
    monkeypatch.setattr(smoke_test, "UI_MODULES", ())
    assert smoke_test.run() == 1
