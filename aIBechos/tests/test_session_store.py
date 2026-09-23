import json

from core.session_store import load_session_dicts, save_session_dicts


def _good():
    return [{"path": "Serie 1x01.mkv", "status": "pendiente"}, {"path": "Serie 1x02.mkv"}]


def test_load_missing_file_returns_empty_without_error(tmp_path):
    dicts, err = load_session_dicts(tmp_path / "session.json")
    assert dicts == [] and err is None


def test_load_roundtrip(tmp_path):
    p = tmp_path / "session.json"
    save_session_dicts(p, _good())
    dicts, err = load_session_dicts(p)
    assert err is None
    assert [d["path"] for d in dicts] == ["Serie 1x01.mkv", "Serie 1x02.mkv"]


def test_load_corrupt_json_reports_error(tmp_path):
    p = tmp_path / "session.json"
    p.write_text('[{"path": "a.mkv"}, {"path":', encoding="utf-8")
    dicts, err = load_session_dicts(p)
    assert dicts == [] and err is not None


def test_load_non_list_reports_error(tmp_path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps({"path": "a.mkv"}), encoding="utf-8")
    dicts, err = load_session_dicts(p)
    assert dicts == [] and err is not None


def test_load_keeps_raw_items_for_per_entry_isolation(tmp_path):
    """Los elementos raros (null, texto) se devuelven tal cual para que el
    llamador los descarte uno a uno sin tumbar la carga entera."""
    p = tmp_path / "session.json"
    p.write_text(json.dumps([None, {"path": "a.mkv"}, "basura"]), encoding="utf-8")
    dicts, err = load_session_dicts(p)
    assert err is None and len(dicts) == 3


def test_save_never_leaves_half_file(tmp_path):
    """Si la serialización falla, el fichero original sigue intacto (no a
    medias): un objeto no serializable debe lanzar SIN tocar el destino."""
    p = tmp_path / "session.json"
    save_session_dicts(p, _good())
    before = p.read_text(encoding="utf-8")
    try:
        save_session_dicts(p, [{"path": object()}])
        raised = False
    except Exception:
        raised = True
    assert raised
    assert p.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("session.json.tmp-*"))
