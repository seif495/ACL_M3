"""Run every scipy MILP solve on one long-lived thread.

scipy's HiGHS solver hangs when it is called from more than one thread: two solves at once, or
even one at a time from threads that come and go (each request runs in a FastAPI threadpool
worker, and those workers are recycled). Measured on 2026-11-07 with scipy 1.17.1: a loop of
plain `milp` calls, each on a fresh thread, hung within 5 rounds even behind a lock; the same
calls handed to one persistent thread ran 180 times without a hang. A hung solve would also hold
its caller's slot (my_team.plan_team, team_builder.build) forever, so every later request would
get a 503.

So `milp()` here has scipy's signature and blocks its caller while the solver thread runs it.
Solves queue one at a time, each still bounded by its own `time_limit`, and the callers'
semaphores still decide how many may wait.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None


def _solver() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="milp")
        return _executor


def milp(*args: Any, **kwargs: Any) -> Any:
    """scipy.optimize.milp, run on the solver thread."""
    from scipy.optimize import milp as scipy_milp

    if threading.current_thread().name.startswith("milp"):    # already on the solver thread
        return scipy_milp(*args, **kwargs)
    return _solver().submit(scipy_milp, *args, **kwargs).result()
