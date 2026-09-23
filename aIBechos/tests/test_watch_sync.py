from core.watch_sync import WatchedItem, SyncAction, diff_watched_items, summarize_actions


def test_watched_only_on_plex_syncs_to_jellyfin():
    item = WatchedItem(media_type="movie", tmdb_id=1, name="Peli",
                        plex_watched=True, jellyfin_watched=False, jellyfin_ref="jf1")
    actions = diff_watched_items([item])
    assert len(actions) == 1
    assert actions[0].target == "jellyfin"
    assert actions[0].item is item


def test_watched_only_on_jellyfin_syncs_to_plex():
    item = WatchedItem(media_type="episode", tmdb_id=2, name="Serie",
                        season=1, episode=3, plex_watched=False, jellyfin_watched=True, plex_ref="p1")
    actions = diff_watched_items([item])
    assert len(actions) == 1
    assert actions[0].target == "plex"


def test_watched_on_both_produces_no_action():
    item = WatchedItem(media_type="movie", tmdb_id=1, name="Peli",
                        plex_watched=True, jellyfin_watched=True)
    assert diff_watched_items([item]) == []


def test_unwatched_on_both_produces_no_action():
    item = WatchedItem(media_type="movie", tmdb_id=1, name="Peli",
                        plex_watched=False, jellyfin_watched=False)
    assert diff_watched_items([item]) == []


def test_never_produces_an_unmark_action():
    # No existe ningun "target"/estado que represente desmarcar -- el propio
    # SyncAction solo puede significar "marcar como visto en target".
    items = [
        WatchedItem(media_type="movie", tmdb_id=1, name="A", plex_watched=True, jellyfin_watched=False,
                    jellyfin_ref="jf1"),
        WatchedItem(media_type="movie", tmdb_id=2, name="B", plex_watched=False, jellyfin_watched=True,
                    plex_ref="p2"),
    ]
    actions = diff_watched_items(items)
    assert len(actions) == 2
    assert all(a.target in ("plex", "jellyfin") for a in actions)
    assert all(a.item.plex_watched or a.item.jellyfin_watched for a in actions)


def test_excludes_action_when_target_platform_has_no_ref():
    # Visto en Jellyfin, pero el episodio no existe en Plex (bibliotecas
    # desincronizadas) -- plex_ref=None significa que no hay nada a lo
    # que escribir, no debe generar una accion "fallida" sin sentido.
    item = WatchedItem(media_type="episode", tmdb_id=1, name="Serie", season=1, episode=1,
                        plex_watched=False, jellyfin_watched=True, plex_ref=None, jellyfin_ref="jf1")
    assert diff_watched_items([item]) == []


def test_includes_action_when_target_platform_has_a_ref():
    item = WatchedItem(media_type="episode", tmdb_id=1, name="Serie", season=1, episode=1,
                        plex_watched=False, jellyfin_watched=True, plex_ref="plex1", jellyfin_ref="jf1")
    actions = diff_watched_items([item])
    assert len(actions) == 1
    assert actions[0].target == "plex"


def test_items_without_tmdb_id_are_excluded():
    item = WatchedItem(media_type="movie", tmdb_id=None, name="Sin emparejar",
                        plex_watched=True, jellyfin_watched=False)
    assert diff_watched_items([item]) == []


def test_mixed_batch_of_movies_and_episodes():
    items = [
        WatchedItem(media_type="movie", tmdb_id=1, name="Peli vista en Plex",
                    plex_watched=True, jellyfin_watched=False, jellyfin_ref="jf1"),
        WatchedItem(media_type="episode", tmdb_id=2, name="Serie", season=1, episode=1,
                    plex_watched=False, jellyfin_watched=True, plex_ref="p2"),
        WatchedItem(media_type="episode", tmdb_id=2, name="Serie", season=1, episode=2,
                    plex_watched=True, jellyfin_watched=True),
    ]
    actions = diff_watched_items(items)
    assert len(actions) == 2
    targets = {(a.target, a.item.media_type) for a in actions}
    assert targets == {("jellyfin", "movie"), ("plex", "episode")}


def test_summarize_actions_counts_by_platform_and_type():
    actions = [
        SyncAction(target="jellyfin", item=WatchedItem(media_type="movie", tmdb_id=1, name="A")),
        SyncAction(target="jellyfin", item=WatchedItem(media_type="movie", tmdb_id=2, name="B")),
        SyncAction(target="jellyfin", item=WatchedItem(media_type="episode", tmdb_id=3, name="C")),
        SyncAction(target="plex", item=WatchedItem(media_type="episode", tmdb_id=4, name="D")),
    ]
    summary = summarize_actions(actions)
    assert summary == {
        "plex": {"movies": 0, "episodes": 1},
        "jellyfin": {"movies": 2, "episodes": 1},
    }


def test_summarize_actions_empty_list():
    assert summarize_actions([]) == {
        "plex": {"movies": 0, "episodes": 0},
        "jellyfin": {"movies": 0, "episodes": 0},
    }
