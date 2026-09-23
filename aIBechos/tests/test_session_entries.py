"""Aislamiento por entrada en la carga de session.json (caso Victor
2026-09-11: Archivos vacía al arrancar, sin rastro en el log, porque UNA
entrada inválida tumbaba _load_session entera en silencio)."""

from gui.app import _entries_from_dicts


def _good(path="Serie 1x01.mkv"):
    return {"path": path, "status": "pendiente"}


def test_valid_entries_load():
    entries, skipped = _entries_from_dicts([_good("a.mkv"), _good("b.mkv")])
    assert [e.path for e in entries] and skipped == 0
    assert len(entries) == 2


def test_single_bad_entry_does_not_kill_the_rest():
    """Una entrada sin 'path' (o con forma rara) se descarta y cuenta, el
    resto carga igual."""
    dicts = [_good("a.mkv"), {}, None, "basura", {"no_path": 1}, _good("b.mkv")]
    entries, skipped = _entries_from_dicts(dicts)
    assert len(entries) == 2
    assert skipped == 4


def test_empty_and_none_input():
    assert _entries_from_dicts([]) == ([], 0)
    assert _entries_from_dicts(None) == ([], 0)
