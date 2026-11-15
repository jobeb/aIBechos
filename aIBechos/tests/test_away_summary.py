from core.away_summary import AwaySummary


def test_resumen_y_se_vacia():
    s = AwaySummary()
    assert s.take() == ("", False)
    for _ in range(12):
        s.record("uploaded", "x")
    s.record("error", "Serie 1x05.mkv")
    s.record("renamed", "no cuenta")
    text, errs = s.take()
    assert text == "Mientras no mirabas: 12 subidos, 1 con error (Serie 1x05.mkv)"
    assert errs is True
    assert s.take() == ("", False)


def test_muchos_errores_se_abrevian():
    s = AwaySummary()
    for i in range(5):
        s.record("error", f"f{i}")
    s.record("skip")
    text, _ = s.take()
    assert "1 omitido, 5 con error (f0, f1, f2, …)" in text
