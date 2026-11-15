# scripts/

One-off tools that are not part of the running app. Run them from the repo root as modules,
so project imports (`config`, `graph`, …) and data paths (`data/…`) resolve:

```bash
.venv/Scripts/python.exe -m scripts.checks.check_neo4j
.venv/Scripts/python.exe -m scripts.efl.efl_post_processing
```

| Folder | Contents |
|---|---|
| `checks/` | Database and API checks, plus old ad-hoc `test_*.py` scripts (the real suite is `tests/`) |
| `data/` | Fixture and season data preparation (fetch, scrape, fix team names, process); `fetch_manager_changes.py` rebuilds `data/manager_changes.csv` from Wikipedia; `fetch_transfermarkt_non_pl.py` rebuilds `data/non_pl_matches.csv` and `data/non_pl_appearances.csv` (cup and European matches, backtest only) from the CC0 transfermarkt-datasets dump; `fetch_odds.py` downloads football-data.co.uk's Premier League odds (season files plus the upcoming `fixtures.csv`; `--no-fixtures` skips it) into gitignored `ODDS_DIR`, default `data/odds/` (inputs of the v4 points model and the backtest, run by the ML workflows; not for redistribution, so never committed); `fetch_player_codes.py` rebuilds `data/player_codes.csv` (each season's FPL element mapped to the player's stable `code`, from vaastav's `players_raw.csv`; v4 keys players by it); `fetch_fpl_snapshots.py` rebuilds the committed `data/fpl_snapshots.csv` (each player's FPL status, chance of playing and set-piece orders at every commit of vaastav's `players_raw.csv`, read by `ml/snapshots.py`; v4's availability features and the backtest) |
| `efl/` | Championship (EFL) data ingestion and validation for promoted teams |
| `embeddings/` | `build_embeddings_from_csv.py` rebuilds `embeddings/prebuilt/` (run by the `build-embeddings` workflow) |
| `analysis/` | LLM evaluation (writes `docs/reports/evaluation_results/`), prediction plots, `ml_backtest.py`, the points-model backtest run by the **ML backtest** workflow, `xpts.py` (expected points from realized xG, xA, xG conceded and minutes, which the backtest also scores picks on), `availability_calibration.py` (how much of a flagged player's points FPL's chance of playing leaves, from `data/fpl_snapshots.csv`), `horizon_availability.py` (the same for the gameweeks after the flagged one: the fitted factors behind the multi-gameweek projections), `manager_replay.py` (the ML manager's planner played through past seasons with projections rebuilt at every deadline, run by the **Manager replay** workflow), and `shadow_ledger.py`, the live (v4) model's saved squad picks against the shadow (v3) model's, gameweek by gameweek (on the VM, mount `scripts/` into a one-off backend container; see `deploy/README.md`) |
| `site/` | `node scripts/site/render.mjs` regenerates the OG image and favicon set in `public/` (headless Edge/Chrome + Pillow); `node scripts/site/screenshots.mjs [url]` recaptures the README screenshots in `docs/screenshots/` from the live site |
| top level | Model training helpers and promoted-team deployment |

Many of these were written for earlier stages of the project and may need updates before
they run against the current graph.
