"""Cancelar en Descargas cancela también la solicitud web enlazada.

Real (Animales 2026): quitada la descarga en aMule a mano, la solicitud
quedó en "downloading" al 67% (la web lo seguía enseñando) y el worker
la iba a relanzar. _download_request_for_amule localiza la solicitud
mía por hash (o por nombre si no guarda hashes) para cancelarla.
"""

import json
from types import SimpleNamespace

from core.app_download_requests import DownloadRequestsMixin as App


class _Ftp:
    def __init__(self, payload):
        self.payload = payload

    def download_bytes(self, path):
        return self.payload

    def disconnect(self):
        pass


def _app(data):
    app = SimpleNamespace()
    app._download_requests_user = lambda: "Jose"
    app._download_requests_remote_path = lambda: "/x/solicitudes.json"
    app._download_requests_ftp = (
        lambda: _Ftp(json.dumps(data).encode("utf-8")))
    return app


MINE = {"tmdb_id": 1, "media_type": "movie", "title": "Animales",
        "year": "2026", "status": "downloading", "claimed_by": "Jose",
        "dl_percent": 67, "amule_hashes": ["ABC123"]}
MINE_OLD = {"tmdb_id": 2, "media_type": "movie", "title": "Obsession",
            "year": "2026", "status": "downloading", "claimed_by": "Jose"}
OTHER = {"tmdb_id": 3, "media_type": "movie", "title": "Dune",
         "year": "2024", "status": "downloading", "claimed_by": "Victor",
         "amule_hashes": ["ABC123"]}
DONE = dict(MINE, status="done")


def test_encuentra_la_mia_por_hash():
    app = _app({"r1": MINE, "r2": OTHER, "r3": DONE})
    rid, desc = App._download_request_for_amule(app, "abc123", "x.mkv")
    assert rid == "r1"
    assert "Animales" in desc


def test_ignora_otro_equipo_finalizadas_y_vacio():
    app = _app({"r2": OTHER, "r3": DONE})
    assert App._download_request_for_amule(app, "abc123", "x.mkv") == ("", "")
    assert App._download_request_for_amule(app, "", "") == ("", "")
    assert App._download_request_for_amule(app, "", "Dune (2024).mkv") == ("", "")


def test_por_nombre_si_no_guarda_hashes():
    app = _app({"r2": MINE_OLD})
    rid, _desc = App._download_request_for_amule(
        app, "zzz", "Obsession (2026) 1080p Castellano.mkv")
    assert rid == "r2"
    # Con hashes que no casan no vale el nombre (otra descarga).
    app = _app({"r2": dict(MINE_OLD, amule_hashes=["qqq"])})
    assert App._download_request_for_amule(
        app, "zzz", "Obsession (2026) 1080p Castellano.mkv") == ("", "")


def test_sin_red_no_encuentra():
    app = SimpleNamespace()
    app._download_requests_user = lambda: "Jose"
    app._download_requests_remote_path = lambda: "/x/solicitudes.json"
    app._download_requests_ftp = lambda: None
    assert App._download_request_for_amule(app, "abc", "x") == ("", "")
