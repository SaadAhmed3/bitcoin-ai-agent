"""Phase 4 research script: pre-declared shadow weights, filter-only variants, event study on design data only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.backtesting import HOLDOUT_START, Decision

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ra():
    spec = importlib.util.spec_from_file_location("research_advanced", ROOT / "scripts" / "research_advanced.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_weights_and_horizons_are_the_pre_registered_ones(ra):
    assert ra.VARIANTS == {
        "A": {},
        "B": {"regime": 0.15},
        "C": {"liquidity_sweep": 0.15},
        "D": {"volume_profile": 0.15},
        "E": {"regime": 0.10, "liquidity_sweep": 0.10, "volume_profile": 0.10},
    }
    assert ra.HORIZONS == (6, 18)
    text = (ROOT / "PREREGISTRATION_ADVANCED.md").read_text(encoding="utf-8")
    for line in ("B: regime 0.15", "C: liquidity_sweep 0.15", "D: volume_profile 0.15",
                 "E: regime 0.10, liquidity_sweep 0.10, volume_profile 0.10", "+6 and +18"):
        assert line in text


def test_combined_score(ra):
    comps = {"regime": 1.0, "liquidity_sweep": -1.0, "volume_profile": 0.5}
    assert ra.combined(0.4, comps, {}) == 0.4
    assert ra.combined(0.4, comps, {"regime": 0.15}) == pytest.approx(0.85 * 0.4 + 0.15)
    assert ra.combined(0.4, comps, ra.VARIANTS["E"]) == pytest.approx(0.7 * 0.4 + 0.1 * (1 - 1 + 0.5))


def test_filter_only_removes_trades_never_adds(ra):
    decs = [Decision(setup="BUY SETUP", tradable=True, score=0.30, stop=1, target=2),
            Decision(setup="BUY SETUP", tradable=True, score=0.30, stop=1, target=2),
            Decision(setup="HOLD", tradable=False, score=0.10, stop=None, target=None)]
    comps = [{"regime": -1.0, "liquidity_sweep": 0, "volume_profile": 0},
             {"regime": 1.0, "liquidity_sweep": 0, "volume_profile": 0},
             {"regime": 1.0, "liquidity_sweep": 1, "volume_profile": 0.5}]       # very bullish, but no engine plan
    out = ra.filter_decisions(decs, comps, {"regime": 0.15}, buy_threshold=0.25)
    assert [d.tradable for d in out] == [False, True, False]
    assert [d.score for d in out] == [d.score for d in decs]                    # the engine's own record is kept


def test_event_study_rows_and_bootstrap_are_deterministic(ra):
    rng = np.random.default_rng(1)
    rets = rng.normal(0, 0.02, 500)
    mask = np.zeros(500, bool)
    mask[::7] = True
    a = ra.event_row(rets, mask, block=6)
    assert a == ra.event_row(rets, mask, block=6)
    assert a["n"] == int(mask.sum()) and a["mean_pct"] == pytest.approx(rets[mask].mean() * 100, abs=1e-3)
    assert a["ci95_pct"][0] <= a["mean_pct"] <= a["ci95_pct"][1]
    assert a["vs_all_pct"] == pytest.approx((rets[mask].mean() - rets.mean()) * 100, abs=1e-3)
    assert ra.event_row(rets, np.zeros(500, bool), block=6)["n"] == 0


def test_forward_returns_never_reach_the_holdout(ra):
    bar = 4 * 3_600_000
    n = 40
    t0 = HOLDOUT_START - 30 * bar
    c4h = pd.DataFrame({"open_time": [t0 + i * bar for i in range(n)], "close": np.linspace(100, 140, n),
                        "close_time": [t0 + (i + 1) * bar - 1 for i in range(n)]})
    r = ra.forward_returns(c4h, horizon=6)
    ok = ~np.isnan(r)
    last = int(np.flatnonzero(ok)[-1])
    assert c4h["close_time"].iloc[last + 6] < HOLDOUT_START                       # the +6 bar closed before it
    assert np.isnan(r[last + 1:]).all()
    assert r[0] == pytest.approx(c4h["close"].iloc[6] / c4h["close"].iloc[0] - 1)


# ---------------------------------------------------------------- the study is run once: a rerun never overwrites it
@pytest.fixture
def offline(ra, tmp_path, monkeypatch):
    """main() with a temporary data folder and a stub study (no database, no real computation)."""
    calls = []
    monkeypatch.setattr(ra, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ra, "load_rows", lambda: {"1h": [], "4h": [], "1d": []})
    monkeypatch.setattr(ra, "study", lambda *a, **k: calls.append(1) or {"run": len(calls)})
    monkeypatch.setattr(ra, "markdown", lambda r: f"# run {r['run']}\n")
    monkeypatch.setattr(ra, "utc_stamp", lambda: "20261031T150000Z")
    return tmp_path, calls


def test_first_run_writes_the_original_files(ra, offline):
    tmp, calls = offline
    assert ra.main([]) == 0
    assert json.loads((tmp / "research_advanced_design.json").read_text()) == {"run": 1}
    assert (tmp / "research_advanced_design.md").read_text(encoding="utf-8") == "# run 1\n"


def test_refuses_to_run_again_without_confirm_rerun(ra, offline, capsys):
    tmp, calls = offline
    (tmp / "research_advanced_design.json").write_text('{"original": true}')
    (tmp / "research_advanced_design.md").write_text("original")
    assert ra.main([]) == 2
    assert calls == []                                                   # nothing was computed
    assert "--confirm-rerun" in capsys.readouterr().out
    assert (tmp / "research_advanced_design.json").read_text() == '{"original": true}'
    assert sorted(p.name for p in tmp.iterdir()) == ["research_advanced_design.json", "research_advanced_design.md"]


def test_confirmed_rerun_saves_new_timestamped_files_and_keeps_the_original(ra, offline):
    tmp, calls = offline
    (tmp / "research_advanced_design.json").write_text('{"original": true}')
    (tmp / "research_advanced_design.md").write_text("original")
    assert ra.main(["--confirm-rerun"]) == 0
    assert ra.main(["--confirm-rerun"]) == 0                            # same second: still a new file
    assert (tmp / "research_advanced_design.json").read_text() == '{"original": true}'
    assert (tmp / "research_advanced_design.md").read_text() == "original"
    assert json.loads((tmp / "research_advanced_design_rerun-20261031T150000Z.json").read_text()) == {"run": 1}
    assert json.loads((tmp / "research_advanced_design_rerun-20261031T150000Z-2.json").read_text()) == {"run": 2}
    assert (tmp / "research_advanced_design_rerun-20261031T150000Z-2.md").read_text(encoding="utf-8") == "# run 2\n"


def test_a_lone_markdown_original_also_blocks_a_rerun(ra, offline):
    tmp, calls = offline
    (tmp / "research_advanced_design.md").write_text("original")
    assert ra.main([]) == 2 and calls == []
