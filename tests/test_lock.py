import os
import time

from desk.lock import cycle_lock


def test_second_holder_is_refused_and_lock_released(tmp_path):
    path = tmp_path / "desk.lock"
    with cycle_lock(path) as first:
        assert first
        with cycle_lock(path) as second:
            assert not second
        assert path.exists()
    assert not path.exists()


def test_stale_lock_is_taken_over(tmp_path):
    path = tmp_path / "desk.lock"
    path.write_text("123")
    old = time.time() - 3600
    os.utime(path, (old, old))
    with cycle_lock(path, stale_after_seconds=1800) as acquired:
        assert acquired
