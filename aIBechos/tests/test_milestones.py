from core.milestones import milestone_for, next_milestone

_GB = 1024 ** 3


def test_milestone_for_none_below_first_tier():
    assert milestone_for(5 * _GB) is None


def test_milestone_for_exact_threshold_counts():
    assert milestone_for(10 * _GB) == ("🥉", "10 GB")


def test_milestone_for_returns_highest_tier_reached():
    assert milestone_for(120 * _GB) == ("🥇", "100 GB")


def test_milestone_for_top_tier():
    assert milestone_for(2 * 1024 * _GB) == ("💎", "1 TB")


def test_milestone_for_zero_bytes():
    assert milestone_for(0) is None


def test_next_milestone_from_zero():
    bytes_needed, label = next_milestone(0)
    assert bytes_needed == 10 * _GB
    assert label == "10 GB"


def test_next_milestone_partway_to_a_tier():
    bytes_needed, label = next_milestone(60 * _GB)
    assert bytes_needed == 40 * _GB
    assert label == "100 GB"


def test_next_milestone_none_when_at_max_tier():
    assert next_milestone(1024 * _GB) is None
    assert next_milestone(2000 * _GB) is None


def test_milestone_and_next_milestone_are_consistent_across_range():
    # Para cualquier total, o bien no hay milestone_for (y next_milestone
    # apunta al primero), o el siguiente hito es estrictamente mayor que
    # el ya alcanzado -- nunca deberian solaparse ni dejar un hueco.
    for total in (0, 1, 9 * _GB, 10 * _GB, 11 * _GB, 99 * _GB, 100 * _GB,
                  499 * _GB, 500 * _GB, 999 * _GB, 1024 * _GB, 5000 * _GB):
        reached = milestone_for(total)
        upcoming = next_milestone(total)
        if reached is None:
            assert upcoming is not None
        if upcoming is not None:
            remaining, _label = upcoming
            assert remaining > 0
