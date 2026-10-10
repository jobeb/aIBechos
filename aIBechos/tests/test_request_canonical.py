"""Título canónico de solicitudes (_download_request_canonical): con tmdb_id
se pide LA ficha, no una búsqueda por texto (TMDB ordena por popularidad
y "Piper" devolvía "La partitura", descargando la película equivocada)."""

from types import SimpleNamespace

from core.app_download_requests import DownloadRequestsMixin as App


class _Tmdb:
    def __init__(self):
        self.calls = []

    def search_multi(self, title, prefer_type=""):
        self.calls.append(("search", title))
        return [{"id": 845643, "media_type": "movie", "title": "La partitura",
                 "release_date": "2023-01-01"}]

    def get_movie_details(self, tid):
        self.calls.append(("movie_details", tid))
        assert tid == 399106
        return {"title": "Piper", "release_date": "2016-06-17"}

    def get_tv_details(self, tid):
        self.calls.append(("tv_details", tid))
        return {"name": "Serie", "first_air_date": "2020-01-01"}

    def build_media_info(self, r):
        from core.api_client import MediaInfo
        return MediaInfo(tmdb_id=r["id"], media_type="movie", title=r["title"],
                         original_title=r["title"], year=(r.get("release_date") or "")[:4])


def _app():
    return SimpleNamespace(tmdb=_Tmdb())


def test_con_tmdb_id_pide_la_ficha():
    app = _app()
    entry = {"tmdb_id": 399106, "media_type": "movie", "title": "Piper", "year": "2016"}
    assert App._download_request_canonical(app, entry) == ("Piper", "2016")
    kinds = [c[0] for c in app.tmdb.calls]
    assert "movie_details" in kinds and "search" not in kinds


def test_sin_tmdb_id_busca_por_texto():
    app = _app()
    entry = {"media_type": "movie", "title": "Piper", "year": "2016"}
    assert App._download_request_canonical(app, entry) == ("La partitura", "2023")


def test_si_detalles_falla_cae_a_busqueda():
    class _Bad(_Tmdb):
        def get_movie_details(self, tid):
            raise RuntimeError("caído")
    app = SimpleNamespace(tmdb=_Bad())
    entry = {"tmdb_id": 399106, "media_type": "movie", "title": "Piper", "year": "2016"}
    assert App._download_request_canonical(app, entry) == ("La partitura", "2023")


def test_sin_titulo_no_toca_red():
    app = _app()
    assert App._download_request_canonical(app, {"title": "", "year": ""}) == ("", "")
    assert app.tmdb.calls == []
