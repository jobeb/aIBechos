import core.learned_comic_titles as lct


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(lct, "app_data_dir", lambda: tmp_path)
    lct._reset_cache_for_tests()
    return lct


def test_load_returns_empty_when_no_file(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    assert mod.load_comic_title_cache() == {}


def test_get_cached_translation_returns_none_when_absent(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    assert mod.get_cached_translation("La Promesa") is None


def test_add_and_get_cached_translation(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Promise")
    assert mod.get_cached_translation("La Promesa") == "The Promise"


def test_get_cached_translation_is_case_and_whitespace_insensitive(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Promise")
    assert mod.get_cached_translation("  la promesa  ") == "The Promise"
    assert mod.get_cached_translation("LA PROMESA") == "The Promise"


def test_add_and_persist_across_sessions(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Promise")

    # Otra "sesion" (cache reiniciada) debe leer lo mismo desde disco
    mod._reset_cache_for_tests()
    assert mod.get_cached_translation("La Promesa") == "The Promise"


def test_add_overwrites_existing_translation(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Wrong Title")
    mod.add_comic_title_translation("La Promesa", "The Promise")
    assert mod.get_cached_translation("La Promesa") == "The Promise"


def test_add_ignores_blank_local_title_or_translation(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    result = mod.add_comic_title_translation("", "The Promise")
    assert result == {}
    result = mod.add_comic_title_translation("La Promesa", "")
    assert result == {}


def test_remove_comic_title_translation(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Promise")
    mod.remove_comic_title_translation("La Promesa")
    assert mod.get_cached_translation("La Promesa") is None


def test_remove_comic_title_translation_is_case_insensitive(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Promise")
    mod.remove_comic_title_translation("LA PROMESA")
    assert mod.get_cached_translation("La Promesa") is None


def test_remove_nonexistent_translation_is_a_noop(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_comic_title_translation("La Promesa", "The Promise")
    result = mod.remove_comic_title_translation("No Existe")
    assert result == {"la promesa": "The Promise"}
