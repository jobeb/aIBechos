from core.slim_candidates import (
    desired_mb_to_bytes, get_desired_bytes, resolve_target_size,
    is_slim_candidate, find_slim_candidates, group_by_season,
    DEFAULT_SLIM_RATIO,
)
from core.download_quality import best_result
from core.amule_client import AmuleSearchResult as R

MB = 1024 * 1024


def _r(number=1, name="Serie 1x01", size="500 MB", sources=5, complete=True):
    return R(number=number, name=name, size_human=size, sources=sources,
             complete=complete)


# --- desired_mb_to_bytes -------------------------------------------------

def test_desired_mb_to_bytes_convierte_mb():
    assert desired_mb_to_bytes(400) == 400 * MB
    assert desired_mb_to_bytes("750.5") == int(750.5 * MB)


def test_desired_mb_to_bytes_tolera_bytes_por_magnitud():
    assert desired_mb_to_bytes(400 * MB) == 400 * MB


def test_desired_mb_to_bytes_invalido():
    assert desired_mb_to_bytes(0) is None
    assert desired_mb_to_bytes(-3) is None
    assert desired_mb_to_bytes("no-numero") is None


# --- get_desired_bytes ---------------------------------------------------

def test_desired_por_nombre_normalizado():
    # "Los Simpson" casa con la clave normalizada (sin artículo, sin acentos).
    assert get_desired_bytes({"simpson": 400}, "Los Simpson") == 400 * MB
    assert get_desired_bytes({}, "Los Simpson") is None
    assert get_desired_bytes({"simpson": 400}, "") is None


# --- resolve_target_size -------------------------------------------------

def test_desired_manda_sobre_mediana():
    sizes = [400 * MB] * 6
    target, src = resolve_target_size(sizes, "Serie 1x01 720p.mkv",
                                      desired_bytes=300 * MB)
    assert target == 300 * MB
    assert src == "desired"


def test_mediana_como_objetivo():
    sizes = [400 * MB] * 6
    target, src = resolve_target_size(sizes, "Serie 1x01 720p.mkv")
    assert src == "typical"
    assert abs(target - 400 * MB) < 5 * MB


def test_todo_pesado_usa_techo_resolucion():
    # Serie entera en REMUX 1080p de ~3 GB: la mediana (3 GB) supera el
    # techo del rango de capítulo 1080p (2 GB) -> no se usa la mediana.
    sizes = [3000 * MB] * 6
    target, src = resolve_target_size(sizes, "Serie 1x01 1080p.mkv")
    assert src == "resolution_cap"
    assert target == 2 * 1024 ** 3


def test_pelicula_sin_mediana_usa_techo():
    target, src = resolve_target_size([], "Peli.2021.1080p.mkv", is_movie=True)
    assert src == "resolution_cap"
    assert target == 6 * 1024 ** 3


# --- is_slim_candidate ---------------------------------------------------

def test_candidato_por_ratio_y_ahorro():
    assert is_slim_candidate(1500 * MB, 400 * MB, ratio=2.0)
    assert not is_slim_candidate(700 * MB, 400 * MB, ratio=2.0)


def test_ahorro_minimo_evita_ruido():
    # Ratio 2x cumplido pero ahorro < 100 MB -> no se propone.
    assert not is_slim_candidate(90 * MB, 40 * MB, ratio=2.0)


def test_ratio_invalido_no_rompe():
    assert not is_slim_candidate(0, 400 * MB)
    assert not is_slim_candidate(1500 * MB, None)


# --- find_slim_candidates ------------------------------------------------

def test_encuentra_solo_el_pesado():
    files = [(f"Serie 1x0{i} 720p.mkv", 400 * MB) for i in range(1, 6)]
    files.append(("Serie 1x06 1080p.mkv", 1500 * MB))
    cands = find_slim_candidates(files, "Serie", ratio=2.0)
    assert [c["name"] for c in cands] == ["Serie 1x06 1080p.mkv"]
    assert cands[0]["ratio"] > 2.0
    assert cands[0]["saving"] > 0


def test_ignora_no_video():
    files = [("Serie 1x01.nfo", 5 * MB), ("cover.jpg", 2 * MB),
             ("Serie 1x01 720p.mkv", 400 * MB)]
    assert find_slim_candidates(files, "Serie") == []


def test_desired_bajo_detecta_mas():
    files = [(f"Serie 1x0{i} 720p.mkv", 800 * MB) for i in range(1, 5)]
    # Sin deseado la mediana (~800 MB) no ve outliers...
    assert find_slim_candidates(files, "Serie", ratio=2.0) == []
    # ...pero con deseado de 300 MB, todos son adelgazables.
    cands = find_slim_candidates(files, "Serie", desired_bytes=300 * MB, ratio=2.0)
    assert len(cands) == 4


def test_group_by_season():
    cands = [{"name": "A 1x01.mkv", "season": 1, "size": 3},
             {"name": "A 2x01.mkv", "season": 2, "size": 2},
             {"name": "A x.mkv", "season": None, "size": 1}]
    g = group_by_season(cands)
    assert len(g[1]) == 1 and len(g[2]) == 1 and len(g[0]) == 1


# --- best_result con max_size ("Adelgazar") -------------------------------

def test_best_result_max_size_descarta_lo_pesado():
    ligero = _r(1, "Serie 1x05 720p Castellano.mkv", size="400 MB",
                sources=10, complete=True)
    pesado = _r(2, "Serie 1x05 1080p Castellano.mkv", size="2 GB",
                sources=50, complete=True)
    # Sin techo gana el pesado (más fuentes/calidad)...
    assert best_result([ligero, pesado], "Serie 1x05").number == 2
    # ...con techo del 85% de 1500 MB (~1275 MB) solo vale el ligero.
    best = best_result([ligero, pesado], "Serie 1x05",
                       typical_size=400 * MB, max_size=int(1500 * MB * 0.85))
    assert best is not None
    assert best.number == 1


def test_best_result_max_size_sin_ligero_devuelve_none():
    pesado = _r(1, "Serie 1x05 1080p Castellano.mkv", size="2 GB",
                sources=50, complete=True)
    assert best_result([pesado], "Serie 1x05",
                       max_size=int(1500 * MB * 0.85)) is None


def test_best_result_sin_max_size_igual_que_antes():
    a = _r(1, "Serie 1x05 720p Castellano.mkv", size="400 MB", sources=10, complete=True)
    b = _r(2, "Serie 1x05 720p Castellano.mkv", size="450 MB", sources=3, complete=True)
    assert best_result([a, b], "Serie 1x05").number == 1
