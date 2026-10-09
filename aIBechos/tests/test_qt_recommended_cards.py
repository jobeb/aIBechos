"""Cards de Recomendado (gui_qt/movies/cards.py + RowModel): botones por
tipo/estado, hit-testing del delegate y modelo de fila. Sin red."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QStyleOptionViewItem  # noqa: E402

from gui_qt.movies.cards import _Layout, card_buttons, card_state_key  # noqa: E402
from gui_qt.movies.tab import RowModel  # noqa: E402


def _item(**kw):
    d = {"tmdb_id": 1, "media_type": "movie", "title": "Dune", "year": "2024",
         "vote": 8.2, "poster_url": None, "overview": "Desierto.", "genre_ids": [878]}
    d.update(kw)
    return d


def test_botones_pelicula_y_serie():
    primary, icons = card_buttons(_item(), {})
    assert primary.id == "download" and primary.enabled
    assert [a.id for a in icons] == ["search", "copy", "dismiss", "fav", "lock"]
    primary_tv, icons_tv = card_buttons(_item(media_type="tv"), {"auto_on": True})
    assert "Piloto" in primary_tv.glyph
    assert icons_tv[0].id == "auto"
    assert all(a.enabled for a in icons_tv)


def test_botones_segun_estado():
    primary, _icons = card_buttons(_item(), {"in_server": True})
    assert not primary.enabled and "servidor" in primary.glyph
    primary, _icons = card_buttons(_item(), {"dl": "busy"})
    assert "⏳" in primary.glyph
    _primary, icons = card_buttons(_item(), {"fav": True, "locked": True})
    assert icons[3].glyph == "★" and icons[4].glyph == "🔒"
    assert card_state_key({"in_server": True}) == "owned"
    assert card_state_key({"dl": "busy"}) == "downloading"
    assert card_state_key({"dl": "fail"}) == "failed"
    assert card_state_key({}) == ""
    # En servidor manda sobre todo lo demás (como la web).
    assert card_state_key({"in_server": True, "dl": "fail"}) == "owned"


def _delegate():
    from gui_qt.movies.cards import CardDelegate
    d = CardDelegate()
    d.get_state = lambda _it: {}
    return d


def _option(rect):
    opt = QStyleOptionViewItem()
    opt.rect = QRect(*rect)
    return opt


def test_hit_testing(qapp):
    from gui_qt.movies.tab import RowModel
    d = _delegate()
    item = _item()
    model = RowModel()
    model.set_items([item])
    index = model.index(0)
    rect = (0, 0, 170, 358)
    lay = _Layout(QRect(*rect), 5)
    assert d._hit(_option(rect), index, lay.primary.center())[1].id == "download"
    assert d._hit(_option(rect), index, lay.icons[0].center())[1].id == "search"
    assert d._hit(_option(rect), index, lay.icons[4].center())[1].id == "lock"
    hit_item, hit_act = d._hit(_option(rect), index, lay.poster.center())
    assert hit_item == item and hit_act is None
    model.set_skeleton(3)
    assert d._hit(_option(rect), model.index(1), lay.primary.center()) == (None, None)


def test_row_model(qapp):
    model = RowModel()
    model.set_skeleton()
    assert model.rowCount() == 7
    from gui_qt.movies.cards import CardRole
    assert model.data(model.index(0), CardRole) is None
    a, b = _item(tmdb_id=1), _item(tmdb_id=2, media_type="tv")
    model.set_items([a, b])
    assert model.data(model.index(1), CardRole) == b
    seen = []
    model.dataChanged.connect(lambda _a, _b: seen.append(True))
    model.refresh_key(("tv", 2))
    assert seen
    model.refresh_all()
    assert len(seen) == 2
