import core.learned_terms as lt


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "app_data_dir", lambda: tmp_path)
    lt._reset_cache_for_tests()
    return lt


def test_load_returns_empty_when_no_file(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    assert mod.load_learned_terms() == []


def test_add_and_persist(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    result = mod.add_learned_terms(["DSNYP", "Multi"])
    assert result == ["DSNYP", "Multi"]

    # Otra "sesion" (cache reiniciada) debe leer lo mismo desde disco
    mod._reset_cache_for_tests()
    assert mod.load_learned_terms() == ["DSNYP", "Multi"]


def test_add_deduplicates_case_insensitive(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_learned_terms(["DSNYP"])
    result = mod.add_learned_terms(["dsnyp", "Nuevo"])
    assert result == ["DSNYP", "Nuevo"]


def test_add_filters_stray_file_extensions(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    result = mod.add_learned_terms(["DSNYP", ".mkv", ".mp4", ""])
    assert result == ["DSNYP"]


def test_add_ignores_blank_tokens(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    result = mod.add_learned_terms(["  ", "Real"])
    assert result == ["Real"]


def test_add_rejects_tokens_shorter_than_three_chars(tmp_path, monkeypatch):
    # Bug real: la IA aprendió "LA" como termino basura -- al usarse como
    # \b(LA)\b insensible a mayúsculas, truncaba el título de CUALQUIER
    # archivo futuro con esa palabra suelta (p.ej. "Avatar LA Busqueda"
    # quedaba en solo "Avatar"). Un token de 1-2 letras es demasiado
    # genérico para aprenderse sin revisión humana.
    mod = _isolated(monkeypatch, tmp_path)
    result = mod.add_learned_terms(["LA", "X", "AC3", "DSNYP"])
    assert result == ["AC3", "DSNYP"]


def test_remove_learned_term(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_learned_terms(["DSNYP", "Multi", "RUIDO"])
    result = mod.remove_learned_term("Multi")
    assert result == ["DSNYP", "RUIDO"]

    mod._reset_cache_for_tests()
    assert mod.load_learned_terms() == ["DSNYP", "RUIDO"]


def test_remove_learned_term_case_insensitive(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_learned_terms(["DSNYP"])
    result = mod.remove_learned_term("dsnyp")
    assert result == []


def test_remove_nonexistent_term_is_a_noop(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_learned_terms(["DSNYP"])
    result = mod.remove_learned_term("NoExiste")
    assert result == ["DSNYP"]


def test_set_learned_terms_replaces_everything(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_learned_terms(["Viejo1", "Viejo2"])
    result = mod.set_learned_terms(["Nuevo1", "Nuevo2"])
    assert result == ["Nuevo1", "Nuevo2"]

    mod._reset_cache_for_tests()
    assert mod.load_learned_terms() == ["Nuevo1", "Nuevo2"]


def test_set_learned_terms_deduplicates_and_ignores_blanks(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    result = mod.set_learned_terms(["Termino", "termino", "  ", "Otro"])
    assert result == ["Termino", "Otro"]


def test_set_learned_terms_empty_list_clears_everything(tmp_path, monkeypatch):
    mod = _isolated(monkeypatch, tmp_path)
    mod.add_learned_terms(["Algo"])
    result = mod.set_learned_terms([])
    assert result == []
