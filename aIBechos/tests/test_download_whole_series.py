"""Recomendado: "⬇ Todo" baja la serie entera que falta, no solo el
piloto; y el ⚡ de una serie sin fila en Episodios ni en Recomendado usa
la ficha de TMDB (real: Blade Runner 2099 no se intentaba)."""

import threading
from types import SimpleNamespace

import pytest

from core import amule_download as ad
from core.app_auto_complete import AutoCompleteMixin
from core.app_movies_core import MoviesCoreMixin


class _Host(MoviesCoreMixin):
    def __init__(self, missing, dub_eps=None, cfg=None):
        self.config_data = dict(cfg or {})
        self._amule_ec_lock = threading.Lock()
        self._spanish_dub_cache = {"171802": {"episodes": dub_eps or {}}}
        self._missing = missing
        self.checked = []

    def _tmdb_expected_episodes(self, tmdb_id):
        return {1: list(range(1, 9))}

    def _cross_check_single_result_with_ftp(self, tmp):
        return dict(tmp, missing=self._missing)

    def _dub_cutoff_for_series(self, tmdb_id, ai):
        return None

    def _set_auto_checked_episode(self, tid, s, e):
        self.checked.append((s, e))


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(cfg, lock, query, **kw):
        seen.append((query, kw))
        if query.endswith("1x03"):
            return False, ad.NO_CASTELLANO_REASON, "", ""
        if query.endswith("1x04"):
            return False, "no encontrado", "", ""
        return True, "", "h", f"{query}.mkv"
    monkeypatch.setattr(ad, "auto_download", fake)
    monkeypatch.setattr(ad, "typical_size_for_series", lambda *a, **k: 500)
    return seen


def test_lanza_todos_los_que_faltan(calls):
    host = _Host({1: [2, 3, 4, 5]}, dub_eps={"1x02": True})
    res = host._download_whole_series(171802, "Blade Runner 2099", "2026")
    assert res["total"] == 4
    assert res["launched"] == ["1x02", "1x05"]
    assert res["no_castellano"] == ["1x03"]
    assert res["failed"] == [("1x04", "no encontrado")]
    assert host.checked == [(1, 2), (1, 5)]
    by_ep = {q[-4:]: kw for q, kw in calls}
    # Doblaje confirmado -> cualquier versión; sin confirmar -> solo castellano.
    assert by_ep["1x02"]["require_castellano"] is False
    assert by_ep["1x05"]["require_castellano"] is True
    assert by_ep["1x05"]["origin"]["tmdb_id"] == 171802
    assert by_ep["1x05"]["expected_year"] == 2026


def test_descargar_sin_doblaje_no_exige_castellano(calls):
    host = _Host({1: [5]}, cfg={"download_undubbed": True})
    host._download_whole_series(171802, "Blade Runner 2099")
    assert calls[0][1]["require_castellano"] is False


def test_nada_que_bajar(calls):
    res = _Host({})._download_whole_series(171802, "Blade Runner 2099")
    assert res["total"] == 0 and not calls


def test_amule_caido_corta(monkeypatch):
    seen = []
    monkeypatch.setattr(ad, "auto_download",
                        lambda *a, **k: (seen.append(1), (False, "aMule no disponible", "", ""))[1])
    monkeypatch.setattr(ad, "typical_size_for_series", lambda *a, **k: None)
    res = _Host({1: [1, 2, 3]})._download_whole_series(171802, "X")
    assert len(seen) == 1 and res["failed"] == [("1x01", "aMule no disponible")]


def test_autocompletado_sin_fila_usa_tmdb():
    host = SimpleNamespace(tmdb=SimpleNamespace(
        get_tv_details=lambda tid: {"id": tid, "name": "Blade Runner 2099", "first_air_date": "2026-11-24"}))
    rec = AutoCompleteMixin._auto_complete_rec_from_tmdb(host, 171802)
    assert rec == {"title": "Blade Runner 2099", "year": "2026", "folder_name": None}
    host.tmdb.get_tv_details = lambda tid: {}
    assert AutoCompleteMixin._auto_complete_rec_from_tmdb(host, 1) is None


def test_boton_todo_en_series():
    from gui_qt.movies.cards import card_menu_entries, card_secondary, _compact_primary, card_buttons
    tv = {"tmdb_id": 1, "media_type": "tv", "title": "Blade Runner 2099"}
    assert card_secondary({"media_type": "movie"}, {}) is None
    assert card_secondary(tv, {"in_server": True, "next_gap": None}) is None
    act = card_secondary(tv, {})
    assert act.id == "download_all" and act.enabled
    assert not card_secondary(tv, {"dl_all": "busy"}).enabled
    assert "download_all" in [e[0] for e in card_menu_entries(tv, {})]
    primary, _ = card_buttons(tv, {"in_server": True, "next_gap": (3, 2)})
    assert _compact_primary(primary).glyph == "⬇ 3x02"
    assert _compact_primary(card_buttons(tv, {})[0]).glyph == "⬇ Piloto"
