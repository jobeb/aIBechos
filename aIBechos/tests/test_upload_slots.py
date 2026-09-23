import threading
import time

from core.upload_slots import UploadSlotManager


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def test_acquire_release_within_limit_does_not_block():
    mgr = UploadSlotManager(_FakeConfig({"ftp_parallel": 2}))
    assert mgr.acquire() is True
    assert mgr.acquire() is True   # 2 cupos, ambos libres todavia
    mgr.release()
    mgr.release()


def test_second_acquire_blocks_until_release_when_limit_is_one():
    mgr = UploadSlotManager(_FakeConfig({"ftp_parallel": 1}))
    assert mgr.acquire() is True

    order = []

    def waiter():
        mgr.acquire()
        order.append("acquired-second")

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.2)
    assert not order, "el segundo acquire no deberia haber conseguido cupo todavia"

    mgr.release()
    t.join(timeout=2)
    assert order == ["acquired-second"], "deberia haber conseguido cupo tras el release"


def test_acquire_returns_false_when_cancelled_while_waiting():
    mgr = UploadSlotManager(_FakeConfig({"ftp_parallel": 1}))
    assert mgr.acquire() is True   # ocupar el unico cupo

    cancel_event = threading.Event()
    result = {}

    def waiter():
        result["ok"] = mgr.acquire(cancel_event=cancel_event)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.1)
    cancel_event.set()
    t.join(timeout=2)
    assert result["ok"] is False


def test_limit_changes_are_read_live():
    config = _FakeConfig({"ftp_parallel": 1})
    mgr = UploadSlotManager(config)
    assert mgr.acquire() is True

    config.d["ftp_parallel"] = 2   # cambio en caliente, como al guardar Ajustes
    assert mgr.acquire() is True, "deberia poder conseguir un segundo cupo tras subir el limite"
    mgr.release()
    mgr.release()


def test_limit_is_clamped_between_1_and_5():
    assert UploadSlotManager(_FakeConfig({"ftp_parallel": 0}))._max_slots() == 1
    assert UploadSlotManager(_FakeConfig({"ftp_parallel": 99}))._max_slots() == 5
    assert UploadSlotManager(_FakeConfig({"ftp_parallel": "no-numero"}))._max_slots() == 1
