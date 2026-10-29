"""Score study (research script): the holdout is never touched, and the bucket table accounts for every bar."""
import numpy as np
from conftest import make_rows

from scripts.score_study import BUCKETS, bucket_of, bucket_table, forward_returns
from services.backtesting import HOLDOUT_START, _frame


def test_forward_returns_never_reach_the_holdout():
    c4h = _frame(make_rows(300, "4h", seed=2, end_ms=HOLDOUT_START + 40 * 4 * 3_600_000))
    ct = c4h["close_time"].to_numpy()
    bars = list(range(0, len(c4h) - 1))
    keep, rets = forward_returns(c4h, bars, horizon=18, cutoff=HOLDOUT_START)
    assert len(keep) == len(rets) > 0
    assert all(ct[bars[k] + 18] < HOLDOUT_START and ct[bars[k]] < HOLDOUT_START for k in keep)
    assert len(keep) < len(bars)                                   # bars near and after the cut-off were dropped
    o, c = c4h["open"].to_numpy(), c4h["close"].to_numpy()
    k0 = keep[0]
    assert rets[0] == c[bars[k0] + 18] / o[bars[k0] + 1] - 1       # close 18 bars after the next open (entry bar counted)


def test_bucket_counts_sum_to_bar_count():
    rng = np.random.default_rng(1)
    scores = np.concatenate([rng.uniform(-0.7, 0.7, 500), [-0.25, -0.10, 0.10, 0.25, 0.40]])
    rets = rng.normal(0, 0.02, len(scores))
    table = bucket_table(scores, rets)
    assert [r["bucket"] for r in table] == list(BUCKETS)
    assert sum(r["bars"] for r in table) == len(scores)
    assert [bucket_of(x) for x in (-0.25, -0.10, 0.10, 0.25, 0.40)] == [0, 1, 3, 4, 5]
