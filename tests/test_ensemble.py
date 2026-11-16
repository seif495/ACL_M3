"""The production points model's ensemble (ml/points_model.py): the averaged prediction and SHAP
values, the members' tree count, and saving and loading the member files."""
import json
import os

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from ml import points_model as pm

COLS = ["a", "b", "c"]


def frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(rng.random((n, 3)), columns=COLS)
    df["total_points"] = 3 * df["a"] - df["b"] + rng.normal(0, 0.2, n)
    return df


def test_the_ensemble_averages_predictions_and_shap_values():
    df = frame()
    ens = pm.fit_ensemble(df, 30, cols=COLS, seeds=(0, 1, 2))
    assert len(ens) == 3
    d = xgb.DMatrix(df[COLS])
    each = [b.predict(d) for b in ens.boosters]
    assert not np.allclose(each[0], each[1])                    # subsampling differs by seed
    assert ens.predict(d) == pytest.approx(np.mean(each, axis=0))
    contribs = ens.predict(d, pred_contribs=True)
    assert contribs.sum(axis=1) == pytest.approx(ens.predict(d), abs=1e-4)
    assert np.ptp(contribs[:, -1]) < 1e-6                       # one base value for every row


@pytest.mark.parametrize("best, rows, train_rows, expected", [
    (120, 1, 1, 60),             # half the early-stopped count
    (121, 1, 1, 60),
    (100, 13, 10, 65),           # scaled by the refit's extra rows
    (1, 1, 1, 1),                # at least one tree
    (20000, 1, 1, pm.MAX_TREES),
])
def test_ensemble_trees(best, rows, train_rows, expected):
    assert pm.ensemble_trees(best, rows, train_rows) == expected


def test_save_and_load_an_ensemble(tmp_path):
    df = frame()
    ens = pm.fit_ensemble(df, 20, cols=COLS, seeds=(0, 1, 2))
    model_path, meta_path = tmp_path / "points_v4.json", tmp_path / "points_v4_meta.json"
    pm.save_model(ens, {"version": "v4"}, str(model_path), str(meta_path), features=COLS)
    assert [os.path.basename(p) for p in pm.member_paths(str(model_path), 3)] == [
        "points_v4.json", "points_v4_m1.json", "points_v4_m2.json"]
    assert all((tmp_path / f).exists() for f in ("points_v4_m1.json", "points_v4_m2.json"))
    meta = json.loads(meta_path.read_text())
    assert meta["members"] == 3 and meta["features"] == COLS
    loaded, meta = pm.load_model(str(model_path), str(meta_path))
    assert isinstance(loaded, pm.Ensemble) and len(loaded) == 3
    assert pm.predict(loaded, meta, df) == pytest.approx(pm.predict(ens, meta, df), abs=1e-6)


def test_single_models_save_and_load_as_before(tmp_path):
    df = frame()
    model = pm.fit(df, None, n_estimators=20, cols=COLS)
    model_path, meta_path = tmp_path / "points_v4.json", tmp_path / "points_v4_meta.json"
    pm.save_model(model, {"version": "v4"}, str(model_path), str(meta_path), features=COLS)
    loaded, meta = pm.load_model(str(model_path), str(meta_path))
    assert isinstance(loaded, xgb.Booster) and meta["members"] == 1
    meta_path.write_text(json.dumps({"version": "v4", "features": COLS}))    # a file from before ensembles
    loaded, meta = pm.load_model(str(model_path), str(meta_path))
    assert isinstance(loaded, xgb.Booster)
    assert pm.predict(loaded, meta, df) == pytest.approx(model.predict(df[COLS]), abs=1e-6)
