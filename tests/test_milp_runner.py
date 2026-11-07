"""services/milp_runner: MILP solves from many short-lived threads must all finish (scipy's
HiGHS hung when called from several threads; see the module docstring)."""
import os
import sys
import threading

import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import milp_runner  # noqa: E402


def _knapsack(seed):
    from scipy.optimize import Bounds, LinearConstraint
    rng = np.random.default_rng(seed)
    n = 120
    a = np.vstack([rng.random(n), np.ones(n)])
    return milp_runner.milp(-rng.random(n), constraints=LinearConstraint(a, -np.inf, [20, 15]),
                            integrality=np.ones(n), bounds=Bounds(0, 1), options={"time_limit": 5})


def test_solves_from_many_threads_all_finish():
    results, threads = [], []
    for rnd in range(15):
        batch = [threading.Thread(target=lambda s=rnd * 3 + k: results.append(_knapsack(s)), daemon=True)
                 for k in range(3)]
        for t in batch:
            t.start()
        threads += batch
        for t in batch:
            t.join(timeout=30)
            assert not t.is_alive(), f"a solve hung in round {rnd}"
    assert len(results) == 45 and all(r.success for r in results)


def test_same_answer_as_scipy():
    from scipy.optimize import Bounds, LinearConstraint, milp
    c = np.array([-3.0, -2.0, -4.0])
    args = dict(constraints=LinearConstraint(np.array([[1.0, 1.0, 2.0]]), -np.inf, 2.0),
                integrality=np.ones(3), bounds=Bounds(0, 1))
    ours = milp_runner.milp(c, **args)
    assert np.allclose(ours.x, milp(c, **args).x) and ours.fun == -5.0
