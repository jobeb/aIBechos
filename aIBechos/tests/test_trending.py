from core.trending import (trending_score, format_trending_score, explain_trending_score,
                            DEFAULT_HALF_LIFE_DAYS)

DAY = 86400
NOW = 1_800_000_000.0   # cualquier epoch fijo, solo para restar contra el


def test_never_played_is_zero():
    assert trending_score(0, None, NOW) == 0.0
    assert trending_score(5, None, NOW) == 0.0   # play_count sin last_played_ts no cuenta
    assert trending_score(0, NOW - DAY, NOW) == 0.0   # last_played sin reproducciones no cuenta


def test_played_recently_scores_higher_than_played_long_ago_with_same_count():
    recent = trending_score(3, NOW - DAY, NOW)
    long_ago = trending_score(3, NOW - 90 * DAY, NOW)
    assert recent > long_ago


def test_recent_single_view_can_outscore_old_frequent_views():
    """El caso que motivó la fórmula: visto ayer una vez debe puntuar más
    que visto muchas veces pero hace mucho tiempo (aquí, 200 días -- más de
    6 vidas medias, así que el decaimiento ya domina sobre el recuento)."""
    recent_once = trending_score(1, NOW - DAY, NOW)
    old_frequent = trending_score(20, NOW - 200 * DAY, NOW)
    assert recent_once > old_frequent


def test_score_halves_after_one_half_life():
    base = trending_score(4, NOW, NOW)
    after_half_life = trending_score(4, NOW - DEFAULT_HALF_LIFE_DAYS * DAY, NOW)
    assert after_half_life == base / 2


def test_more_plays_scores_higher_at_same_recency():
    few = trending_score(1, NOW - DAY, NOW)
    many = trending_score(10, NOW - DAY, NOW)
    assert many > few


def test_future_last_played_is_clamped_not_negative():
    # Reloj/zona horaria desajustados -- no debe dar una puntuacion mayor
    # que "visto ahora mismo" ni fallar con dias negativos.
    future = trending_score(2, NOW + DAY, NOW)
    now_exact = trending_score(2, NOW, NOW)
    assert future == now_exact


def test_format_trending_score_hides_zero():
    assert format_trending_score(0.0) == ""


def test_format_trending_score_shows_one_decimal():
    assert format_trending_score(12.34) == "🔥 12.3"


def test_explain_never_played():
    text = explain_trending_score(0, None, NOW)
    assert "Nunca vista" in text


def test_explain_includes_score_play_count_and_half_life():
    text = explain_trending_score(15, NOW - 8 * DAY, NOW)
    assert "15 veces" in text
    assert "hace 8 días" in text
    assert "30 días" in text   # DEFAULT_HALF_LIFE_DAYS
    score = trending_score(15, NOW - 8 * DAY, NOW)
    assert f"{score:.1f}" in text


def test_explain_singular_play_count():
    text = explain_trending_score(1, NOW - 5 * DAY, NOW)
    assert "1 vez" in text
    assert "1 veces" not in text


def test_explain_today_and_yesterday_wording():
    assert "hoy" in explain_trending_score(2, NOW, NOW)
    assert "ayer" in explain_trending_score(2, NOW - DAY, NOW)
