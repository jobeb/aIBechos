"""Aislamiento por entrada en la carga de session.json (caso Victor
2026-09-11: Archivos vacía al arrancar, sin rastro en el log, porque UNA
entrada inválida tumbaba _load_session entera en silencio)."""

import sys

import pytest

from gui.app import _dedupe_entries, _entries_from_dicts, FileEntry

WIN = sys.platform.startswith("win")


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


@pytest.mark.skipif(not WIN, reason="caja y separadores solo colapsan en Windows")
def test_dedupe_colapsa_misma_ruta_distinta_caja_o_separadores():
    """En Windows, distinta caja o "/" vs "\\" es el MISMO archivo: una
    sola fila (ver core/path_key.py). Sin esto, al reiniciar aparecían
    duplicados (la fila restaurada no casaba con el path del watcher)."""
    a = FileEntry(r"C:\vigilada\serie 1x01.mkv")
    a.status = "pendiente"
    b = FileEntry("C:/vigilada/serie 1x01.mkv")
    b.status = "listo"
    c = FileEntry(r"C:\vigilada\otra 1x02.mkv")
    out = _dedupe_entries([a, b, c])
    assert len(out) == 2
    kept = next(e for e in out if "otra" not in e.path)
    assert kept.status == "listo", "se queda con el estado más avanzado"
    assert any("otra" in e.path for e in out)


@pytest.mark.skipif(not WIN, reason="caja solo colapsa en Windows")
def test_dedupe_empate_prefiere_con_media_info():
    a = FileEntry(r"C:\vigilada\x 1x01.mkv")
    a.status = "listo"
    b = FileEntry(r"c:\vigilada\X 1x01.mkv")
    b.status = "listo"
    b.media_info = object()
    out = _dedupe_entries([a, b])
    assert len(out) == 1
    assert out[0] is b
