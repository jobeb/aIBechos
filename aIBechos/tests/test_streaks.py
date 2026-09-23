import datetime

from core.streaks import compute_streaks, top_streaks

TODAY = datetime.date(2026, 7, 20)


def _entry(person, days_ago, status="ok", kind="subida"):
    ts = datetime.datetime.combine(TODAY - datetime.timedelta(days=days_ago), datetime.time(12, 0)).timestamp()
    return {"person": person, "ts": ts, "status": status, "kind": kind}


def test_streak_of_one_when_uploaded_only_today():
    entries = [_entry("Jose", 0)]
    result = compute_streaks(entries, today=TODAY)
    assert result["jose"] == {"display_name": "Jose", "streak_days": 1}


def test_streak_counts_consecutive_days_ending_today():
    entries = [_entry("Jose", 0), _entry("Jose", 1), _entry("Jose", 2)]
    result = compute_streaks(entries, today=TODAY)
    assert result["jose"]["streak_days"] == 3


def test_streak_stays_alive_if_nothing_uploaded_today_but_uploaded_yesterday():
    entries = [_entry("Jose", 1), _entry("Jose", 2)]
    result = compute_streaks(entries, today=TODAY)
    assert result["jose"]["streak_days"] == 2


def test_streak_breaks_after_a_full_day_with_no_uploads():
    entries = [_entry("Jose", 2), _entry("Jose", 3)]   # nada hoy ni ayer
    result = compute_streaks(entries, today=TODAY)
    assert "jose" not in result


def test_streak_stops_counting_at_the_first_gap():
    # Subió hoy, ayer, y hace 3 dias -- pero NO hace 2 dias -- la racha
    # es de 2 (hoy+ayer), no sigue "saltando" el hueco.
    entries = [_entry("Jose", 0), _entry("Jose", 1), _entry("Jose", 3)]
    result = compute_streaks(entries, today=TODAY)
    assert result["jose"]["streak_days"] == 2


def test_streak_ignores_non_upload_entries():
    entries = [_entry("Jose", 0, kind="borrado")]
    assert compute_streaks(entries, today=TODAY) == {}


def test_streak_ignores_failed_uploads():
    entries = [_entry("Jose", 0, status="error")]
    assert compute_streaks(entries, today=TODAY) == {}


def test_streak_groups_different_capitalizations_together():
    entries = [_entry("Jose", 0), _entry("jose", 1), _entry("  JOSE  ", 2)]
    result = compute_streaks(entries, today=TODAY)
    assert len(result) == 1
    assert result["jose"]["streak_days"] == 3


def test_streak_display_name_keeps_a_seen_spelling():
    entries = [_entry("José", 0)]
    result = compute_streaks(entries, today=TODAY)
    assert result["jose"]["display_name"] == "José"


def test_streak_keeps_people_independent():
    entries = [_entry("Jose", 0), _entry("Jose", 1), _entry("Ana", 0)]
    result = compute_streaks(entries, today=TODAY)
    assert result["jose"]["streak_days"] == 2
    assert result["ana"]["streak_days"] == 1


def test_compute_streaks_empty_entries():
    assert compute_streaks([], today=TODAY) == {}


def test_top_streaks_sorted_descending_and_limited():
    entries = [
        _entry("Jose", 0),
        _entry("Ana", 0), _entry("Ana", 1), _entry("Ana", 2),
        _entry("Maria", 0), _entry("Maria", 1),
    ]
    top = top_streaks(compute_streaks(entries, today=TODAY), limit=2)
    assert [e["display_name"] for e in top] == ["Ana", "Maria"]


def test_top_streaks_empty_data():
    assert top_streaks({}) == []
