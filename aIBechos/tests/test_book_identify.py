from unittest.mock import MagicMock

import core.learned_comic_titles as lct
from core.book_identify import identify_book_or_comic
from core.api_client import MediaInfo


def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(lct, "app_data_dir", lambda: tmp_path)
    lct._reset_cache_for_tests()


def test_no_title_detected_returns_error():
    result = identify_book_or_comic(
        {"title": ""}, is_comic=False,
        comicvine_client=MagicMock(), book_client=MagicMock(),
        ai_fallback_enabled=False, ai_api_key="")
    assert result.error == "No se pudo detectar el nombre"
    assert result.media_info is None


def test_book_success_uses_google_books(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    book_client = MagicMock()
    book_client.search_volumes.return_value = [{"volumeInfo": {"title": "El Nombre del Viento"}}]
    book_client.build_book_info.return_value = MediaInfo(
        tmdb_id="x", media_type="libro", title="El Nombre del Viento",
        original_title="El Nombre del Viento", year="2007", genre_ids=["ebook"])

    result = identify_book_or_comic(
        {"title": "El Nombre Del Viento"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client,
        ai_fallback_enabled=False, ai_api_key="")

    assert result.error is None
    assert result.media_info.title == "El Nombre del Viento"
    assert result.confidence > 0
    book_client.search_volumes.assert_called_once_with("El Nombre Del Viento")


def test_book_no_results_returns_error(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    book_client = MagicMock()
    book_client.search_volumes.return_value = []

    result = identify_book_or_comic(
        {"title": "Algo Raro"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client,
        ai_fallback_enabled=False, ai_api_key="")

    assert result.error == "Sin resultados en Google Books"


def test_comic_success_uses_comicvine(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    comicvine = MagicMock()
    comicvine.search_volumes.return_value = [{"volume": {"name": "The Promise"}, "id": 1}]
    comicvine.build_comic_info.return_value = MediaInfo(
        tmdb_id=1, media_type="libro", title="The Promise",
        original_title="The Promise", year="2012", genre_ids=["comic"], episode=1)

    result = identify_book_or_comic(
        {"title": "The Promise", "episode": 1}, is_comic=True,
        comicvine_client=comicvine, book_client=MagicMock(),
        ai_fallback_enabled=False, ai_api_key="")

    assert result.error is None
    assert result.media_info.title == "The Promise"
    assert result.translated_via_ai is False


def test_comic_no_results_tries_ai_translation_when_enabled(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    comicvine = MagicMock()
    comicvine.search_volumes.side_effect = [[], [{"volume": {"name": "The Promise"}, "id": 1}]]
    comicvine.build_comic_info.return_value = MediaInfo(
        tmdb_id=1, media_type="libro", title="The Promise",
        original_title="The Promise", year="2012", genre_ids=["comic"])

    monkeypatch.setattr(
        "core.ai_title_fallback.guess_original_comic_title_via_ai",
        lambda local_title, api_key: "The Promise")

    result = identify_book_or_comic(
        {"title": "La Promesa"}, is_comic=True,
        comicvine_client=comicvine, book_client=MagicMock(),
        ai_fallback_enabled=True, ai_api_key="fake-key")

    assert result.error is None
    assert result.translated_via_ai is True
    assert result.used_query == "The Promise"
    assert comicvine.search_volumes.call_count == 2
    # La traducción que sí funcionó queda cacheada para la próxima vez
    assert lct.get_cached_translation("La Promesa") == "The Promise"


def test_comic_no_results_skips_ai_when_disabled(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    comicvine = MagicMock()
    comicvine.search_volumes.return_value = []

    called = []
    monkeypatch.setattr(
        "core.ai_title_fallback.guess_original_comic_title_via_ai",
        lambda *a, **kw: called.append(1) or "The Promise")

    result = identify_book_or_comic(
        {"title": "La Promesa"}, is_comic=True,
        comicvine_client=comicvine, book_client=MagicMock(),
        ai_fallback_enabled=False, ai_api_key="fake-key")

    assert result.error == "Sin resultados en ComicVine"
    assert called == []


def test_comic_uses_cached_translation_without_calling_ai(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    lct.add_comic_title_translation("La Promesa", "The Promise")

    comicvine = MagicMock()
    comicvine.search_volumes.return_value = [{"volume": {"name": "The Promise"}, "id": 1}]
    comicvine.build_comic_info.return_value = MediaInfo(
        tmdb_id=1, media_type="libro", title="The Promise",
        original_title="The Promise", year="2012", genre_ids=["comic"])

    called = []
    monkeypatch.setattr(
        "core.ai_title_fallback.guess_original_comic_title_via_ai",
        lambda *a, **kw: called.append(1))

    result = identify_book_or_comic(
        {"title": "La Promesa"}, is_comic=True,
        comicvine_client=comicvine, book_client=MagicMock(),
        ai_fallback_enabled=True, ai_api_key="fake-key")

    assert result.error is None
    assert result.translated_via_ai is False
    comicvine.search_volumes.assert_called_once_with("The Promise")
    assert called == []


def test_search_error_is_reported(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    comicvine = MagicMock()
    comicvine.search_volumes.side_effect = RuntimeError("boom")

    result = identify_book_or_comic(
        {"title": "Algo"}, is_comic=True,
        comicvine_client=comicvine, book_client=MagicMock(),
        ai_fallback_enabled=False, ai_api_key="")

    assert result.error == "Error de ComicVine: boom"


# ── OpenLibrary como proveedor principal de libros (Google Books de apoyo) ──

def _make_openlibrary(title="El Nombre del Viento"):
    ol = MagicMock()
    ol.search_volumes.return_value = [{"title": title, "key": "/works/OL1W"}]
    ol.build_book_info.return_value = MediaInfo(
        tmdb_id="/works/OL1W", media_type="libro", title=title,
        original_title=title, year="2007", genre_ids=["ebook"])
    return ol


def test_book_prefers_openlibrary_and_does_not_call_google_books():
    openlibrary = _make_openlibrary()
    book_client = MagicMock()

    result = identify_book_or_comic(
        {"title": "El Nombre Del Viento"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client, openlibrary_client=openlibrary)

    assert result.error is None
    assert result.provider == "openlibrary"
    assert result.media_info.title == "El Nombre del Viento"
    openlibrary.search_volumes.assert_called_once_with("El Nombre Del Viento")
    book_client.search_volumes.assert_not_called()


def test_book_falls_back_to_google_books_when_openlibrary_empty(tmp_path, monkeypatch):
    _isolated_cache(monkeypatch, tmp_path)
    openlibrary = MagicMock()
    openlibrary.search_volumes.return_value = []
    book_client = MagicMock()
    book_client.search_volumes.return_value = [{"volumeInfo": {"title": "El Nombre del Viento"}}]
    book_client.build_book_info.return_value = MediaInfo(
        tmdb_id="x", media_type="libro", title="El Nombre del Viento",
        original_title="El Nombre del Viento", year="2007", genre_ids=["ebook"])

    result = identify_book_or_comic(
        {"title": "El Nombre Del Viento"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client, openlibrary_client=openlibrary)

    assert result.error is None
    assert result.provider == "google_books"
    book_client.search_volumes.assert_called_once_with("El Nombre Del Viento")


def test_book_falls_back_to_google_books_when_openlibrary_raises():
    openlibrary = MagicMock()
    openlibrary.search_volumes.side_effect = ConnectionError("sin red")
    book_client = MagicMock()
    book_client.search_volumes.return_value = [{"volumeInfo": {"title": "El Nombre del Viento"}}]
    book_client.build_book_info.return_value = MediaInfo(
        tmdb_id="x", media_type="libro", title="El Nombre del Viento",
        original_title="El Nombre del Viento", year="2007", genre_ids=["ebook"])

    result = identify_book_or_comic(
        {"title": "El Nombre Del Viento"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client, openlibrary_client=openlibrary)

    assert result.error is None
    assert result.provider == "google_books"


def test_book_reports_error_when_both_providers_fail():
    openlibrary = MagicMock()
    openlibrary.search_volumes.side_effect = ConnectionError("sin red openlibrary")
    book_client = MagicMock()
    book_client.search_volumes.side_effect = RuntimeError("boom google")

    result = identify_book_or_comic(
        {"title": "Algo"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client, openlibrary_client=openlibrary)

    assert "OpenLibrary" in result.error
    assert "Google Books" in result.error


def test_book_reports_generic_error_when_both_providers_empty():
    openlibrary = MagicMock()
    openlibrary.search_volumes.return_value = []
    book_client = MagicMock()
    book_client.search_volumes.return_value = []

    result = identify_book_or_comic(
        {"title": "Algo"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client, openlibrary_client=openlibrary)

    assert result.error == "Sin resultados en OpenLibrary ni en Google Books"


def test_book_without_openlibrary_client_skips_straight_to_google_books():
    # openlibrary_client=None (valor por defecto) -- no rompe a llamadores
    # que todavía no lo pasan.
    book_client = MagicMock()
    book_client.search_volumes.return_value = [{"volumeInfo": {"title": "El Nombre del Viento"}}]
    book_client.build_book_info.return_value = MediaInfo(
        tmdb_id="x", media_type="libro", title="El Nombre del Viento",
        original_title="El Nombre del Viento", year="2007", genre_ids=["ebook"])

    result = identify_book_or_comic(
        {"title": "El Nombre Del Viento"}, is_comic=False,
        comicvine_client=MagicMock(), book_client=book_client)

    assert result.error is None
    assert result.provider == "google_books"
