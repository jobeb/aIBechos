from core.missing_ep_rows import dedupe_rows as _dedupe_missing_ep_rows


def _row(tmdb_id, name="Dragon Ball Daima"):
    return {"tmdb_id": tmdb_id, "name": name, "missing": {1: [9]}}


def test_mismo_id_distinto_tipo_dedupica():
    rows = [_row(236994), _row("236994", "Dragon Ball DAIMA")]
    out = _dedupe_missing_ep_rows(rows)
    assert len(out) == 1
    assert out[0]["tmdb_id"] == 236994


def test_ids_distintos_se_conservan():
    rows = [_row(236994), _row(129600, "Dragon Ball")]
    assert len(_dedupe_missing_ep_rows(rows)) == 2


def test_sin_id_dedupica_por_nombre():
    rows = [_row(None), _row("", "Dragon Ball Daima")]
    assert len(_dedupe_missing_ep_rows(rows)) == 1


def test_sin_identidad_no_se_pierde():
    rows = [{"missing": {1: [1]}}, {"missing": {1: [2]}}]
    assert len(_dedupe_missing_ep_rows(rows)) == 2


def test_vacia_y_nones():
    assert _dedupe_missing_ep_rows([]) == []
    assert _dedupe_missing_ep_rows(None) == []
