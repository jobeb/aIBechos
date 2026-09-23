"""El "Descargar log completo" debe llevarse TODOS los logs, incluidas
rotaciones y los añadidos sin dar de alta (ver core/log_export.py)."""

from core.log_export import collect_log_files


def _touch(d, *names):
    for n in names:
        (d / n).write_text("x", encoding="utf-8")


def test_collect_includes_rotations_and_all_known_logs(tmp_path):
    _touch(tmp_path, "app.log", "app.log.1", "app.log.2", "ai_fallback.log",
           "auto_watcher.log", "auto_watcher.log.1", "media_refresh.log",
           "update_check.log", "active_poll.log")
    names = [p.name for p in collect_log_files(tmp_path)]
    assert names == sorted(["app.log", "app.log.1", "app.log.2", "ai_fallback.log",
                            "auto_watcher.log", "auto_watcher.log.1", "media_refresh.log",
                            "update_check.log", "active_poll.log"])


def test_collect_ignores_non_logs_and_subdirs(tmp_path):
    _touch(tmp_path, "session.json", "config.json")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.log").write_text("x", encoding="utf-8")
    assert collect_log_files(tmp_path) == []


def test_collect_missing_dir_returns_empty(tmp_path):
    assert collect_log_files(tmp_path / "noexiste") == []
