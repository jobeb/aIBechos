"""Adelgazar películas en Liberar espacio, también las sueltas (sin carpeta
propia: "Peli (2020).mkv" con su .nfo y carteles en la raíz de la
categoría), y el emparejamiento de la ligera con el gordo."""
from types import SimpleNamespace

from core import slim_pending
from core.app_cleanup_core import CleanupCoreMixin as Cleanup
from core.cleanup_candidates import CleanupItem, group_loose_files_by_name

GB = 1024 ** 3


def _loose_item(**kw):
    files = [("La Odisea (2026).mkv", 9 * GB), ("La Odisea (2026).nfo", 500),
             ("La Odisea (2026)-poster.jpg", 100_000)]
    g = group_loose_files_by_name(files)["La Odisea (2026)"]
    base = dict(tmdb_id=1234, name="La Odisea (2026)", media_type="movie",
                ftp_path="/datos/peliculas/La Odisea (2026)", category_name="Películas",
                size_bytes=g["size_bytes"],
                loose_file_paths=[f"/datos/peliculas/{n}" for n in g["file_names"]],
                loose_files=g["files"])
    base.update(kw)
    return CleanupItem(**base)


def _host():
    return SimpleNamespace(
        config_data={"slim_desired_sizes": {}},
        _cleanup_trees={}, _cleanup_ep_cache={}, _cleanup_ep_cache_ttl=600,
        _cleanup_slim_ratio=lambda: 2.0,
        _cleanup_desired_bytes=lambda name: None)


def test_grupo_suelto_trae_tamano_por_archivo():
    g = group_loose_files_by_name([("A (2020).mkv", 5), ("A (2020).nfo", 1)])["A (2020)"]
    assert sorted(map(tuple, g["files"])) == [("A (2020).mkv", 5), ("A (2020).nfo", 1)]


def test_pelicula_suelta_solo_su_video_y_en_la_raiz():
    files = Cleanup._cleanup_ep_files(_host(), _loose_item())
    assert files == [("La Odisea (2026).mkv", 9 * GB, "/datos/peliculas")]
    # Análisis antiguo (sin tamaños por archivo): no se puede adelgazar todavía.
    assert Cleanup._cleanup_ep_files(_host(), _loose_item(loose_files=None)) is None


def test_pelicula_suelta_gorda_es_adelgazable_con_su_carpeta_real():
    h = _host()
    it = _loose_item()
    cands, target = Cleanup._slim_candidates_for_item(h, it, Cleanup._cleanup_ep_files(h, it))
    assert [d["name"] for d in cands] == ["La Odisea (2026).mkv"]
    assert cands[0]["folder"] == "/datos/peliculas", "no la ruta del grupo, que no es carpeta"
    assert target == 2 * GB   # sin resolución en el nombre: techo de 2 GB
    # Una de 3 GB no llega a 2× el objetivo.
    ligera = _loose_item(loose_files=[["La Odisea (2026).mkv", 3 * GB]])
    cands, _t = Cleanup._slim_candidates_for_item(h, ligera, Cleanup._cleanup_ep_files(h, ligera))
    assert cands == []


def test_solo_adelgazables_y_vista_plana_incluyen_peliculas_sueltas():
    h = _host()
    h._cleanup_trees = {"/datos/series/X": [("X 1x01.mkv", GB)]}   # hay análisis
    h._cleanup_files_by_owner = lambda items: Cleanup._cleanup_files_by_owner(h, items)
    h._cleanup_ep_files = lambda it: Cleanup._cleanup_ep_files(h, it)
    h._slim_candidates_for_item = lambda it, f: Cleanup._slim_candidates_for_item(h, it, f)
    gorda, ligera = _loose_item(), _loose_item(loose_files=[["La Odisea (2026).mkv", GB]])
    assert Cleanup._filter_slim_only(h, [gorda, ligera]) == [gorda]
    rows = Cleanup._build_cleanup_flat_rows(h, [gorda])
    assert [r["name"] for r in rows] == ["La Odisea (2026).mkv"]
    assert rows[0]["folder"] == "/datos/peliculas"


def test_registro_del_gordo_sin_anio_en_la_clave_y_con_tmdb(monkeypatch):
    got = []
    monkeypatch.setattr(slim_pending, "record", lambda e: got.append(e) or e)
    h = SimpleNamespace(config_data={"app_user_name": "Jose"})
    Cleanup._record_slim_replacement(h, _loose_item(), "La Odisea (2026).mkv", 9 * GB,
                                     "/datos/peliculas", "La Odisea 2026")
    e = got[0]
    assert (e["key_norm"], e["year"], e["tmdb_id"]) == ("odisea", "2026", 1234)
    assert e["heavy_remote_file"] == "/datos/peliculas/La Odisea (2026).mkv"
    # La ligera que llega (título TMDB) casa con él; por TMDB aunque el
    # nombre del servidor sea otro.
    assert slim_pending.find_match([e], "movie", "La Odisea", year="2026",
                                   local_size=3 * GB, tmdb_id=1234) is e
    otro = dict(e, key_norm="the beast")
    assert slim_pending.find_match([otro], "movie", "Beast (La bestia)", year="2026",
                                   local_size=3 * GB, tmdb_id=1234) is otro
    assert slim_pending.find_match([e], "movie", "La Odisea", year="2026",
                                   local_size=3 * GB, tmdb_id=999) is None, "otro TMDB no casa"
