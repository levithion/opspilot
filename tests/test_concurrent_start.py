"""Regression: MCP clients (e.g. Claude Desktop) may launch two server copies at once on a fresh data dir."""

import multiprocessing as mp
import time

from opspilot.db import Database
from opspilot.seed import seed_demo_data


def _start(path: str, start_at: float, q) -> None:
    while time.time() < start_at:
        pass
    try:
        db = Database(path)
        seed_demo_data(db)
        q.put("ok")
    except Exception as exc:  # report, do not raise inside the child
        q.put(f"{type(exc).__name__}: {exc}")


def test_concurrent_first_start_does_not_lock_or_double_seed(tmp_path):
    path = str(tmp_path / "shared.db")
    q = mp.Queue()
    start_at = time.time() + 1.0
    procs = [mp.Process(target=_start, args=(path, start_at, q)) for _ in range(6)]
    for p in procs:
        p.start()
    results = [q.get(timeout=60) for _ in procs]
    for p in procs:
        p.join()
    assert results == ["ok"] * 6, results
    db = Database(path)
    assert db.scalar("SELECT COUNT(*) FROM users") == 6  # seeded exactly once
