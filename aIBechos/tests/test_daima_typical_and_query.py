"""Dragon Ball Daima 1x10: la búsqueda llevaba "castellano" (Kad exige
todas las palabras: 1 resultado contra 14) y el típico salía de la carpeta
"Dragon Ball." (193 MB) en vez de la de Daima (~500 MB), así que el 1x10 en
castellano de 675 MB quedaba por debajo del umbral."""

from core import amule_download as ad
from core.amule_search import build_amule_query, build_amule_season_query

MB = 1024 * 1024


def test_la_busqueda_no_lleva_castellano():
    assert build_amule_query("Dragon Ball Daima", 1, 10, prefers_castellano=True) == "Dragon Ball Daima 1x10"
    assert build_amule_season_query("Dragon Ball Daima", 1, prefers_castellano=True) == "Dragon Ball Daima 1x"


class _FakeFtp:
    ROOT = "/series"
    TREE = {
        "/series": ["Dragon Ball.", "Dragon Ball Daima", "Dragon Ball Z."],
        "/series/Dragon Ball.": ["Temporada 01"],
        "/series/Dragon Ball Daima": ["Temporada 01"],
    }
    FILES = {
        "/series/Dragon Ball./Temporada 01": [(f"Dragon Ball 1x{i:02d}.avi", 190 * MB) for i in range(1, 30)],
        "/series/Dragon Ball Daima/Temporada 01": [(f"Dragon Ball Daima 1x{i:02d}.mp4", 500 * MB) for i in range(1, 10)],
    }

    def connect(self, *a, **k):
        pass

    def is_connected(self):
        return True

    def disconnect(self):
        pass

    def list_dirs(self, path):
        return list(self.TREE.get(path.rstrip("/"), []))

    def list_files_with_sizes(self, path):
        return list(self.FILES.get(path.rstrip("/"), []))

    def build_remote_path(self, tmpl, serie, season, year, kind):
        return tmpl.replace("{serie}", serie) + "Temporada 01"


def test_tipico_de_la_carpeta_exacta_no_de_otra_entrega(monkeypatch):
    monkeypatch.setattr(ad, "make_client", lambda proto: _FakeFtp())
    monkeypatch.setattr(ad, "known_series_names_from_cache", lambda: {"Dragon Ball Daima"})
    cfg = {"ftp_protocol": "sftp", "ftp_categories": {"tv": [{"root": "/series", "template": "{serie}/"}]}}
    typ = ad.typical_size_for_series(cfg, "Dragon Ball Daima", 1, cache={})
    assert typ is not None and 450 * MB <= typ <= 550 * MB


def _res(name, size="480.0 MB", sources=5):
    from core.amule_client import AmuleSearchResult
    return AmuleSearchResult(0, name, size, sources)


def test_con_plantilla_se_puntua_con_el_nombre_real():
    rs = [_res("DragonBall Daima 1x10(Castellano).mp4"), _res("Dragon Ball Daima 01x10 Oceano.mkv")]
    assert ad._effective_score_query("Daima 1x10", "Dragon Ball Daima 1x10", rs) == "Dragon Ball Daima 1x10"
    # Resultados con el nombre de la plantilla (Tensura para Slime): se puntúa con la buscada.
    rs2 = [_res("Tensura 2x03 castellano.mkv")]
    assert ad._effective_score_query("Tensura 2x03", "Slime 2x03", rs2) == "Tensura 2x03"
    assert ad._effective_score_query("Daima 1x10", None, rs) == "Daima 1x10"


class _FakeEc:
    def __init__(self, results):
        self.results, self.downloaded = results, []

    def connect(self):
        pass

    def close(self):
        pass

    def iter_search(self, query, **k):
        yield list(self.results)

    def download(self, r):
        self.downloaded.append(r)
        return True, ""


def test_auto_download_con_plantilla_elige_el_castellano(monkeypatch):
    import threading
    cast = _res("DragonBall Daima 1x10(Castellano).mp4", "675.0 MB")
    jap = _res("Dragon Ball Daima 1x10 Oceano WEB-DL 1080p x264 [ja].mkv", "1.35 GB", 1)
    ec = _FakeEc([jap, cast])
    monkeypatch.setattr(ad, "ec_client_from_config", lambda cfg: ec)
    monkeypatch.setattr(ad, "_already_completed", lambda ec, best: False)
    ok, why, _h, name = ad.auto_download({}, threading.Lock(), "Daima 1x10", typical_size=480 * MB,
                                         require_castellano=True, score_query="Dragon Ball Daima 1x10")
    assert ok, why
    assert name == cast.name
