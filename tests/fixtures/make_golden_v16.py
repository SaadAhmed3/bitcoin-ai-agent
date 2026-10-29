"""Records the signal-1.6 replay + simulation on deterministic fixtures (run once, BEFORE Phase 6.2 changes).

tests/test_entry_modes.py checks that entry_mode="standard" in later engines reproduces this exactly.
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_rows  # noqa: E402
from services.backtesting import BacktestConfig, _frame, replay, simulate  # noqa: E402
from services.signal_engine import ENGINE_VERSION, EngineConfig  # noqa: E402

END = 1_760_000_000_000
SEEDS = (1, 8)


def snapshot(seed: int) -> dict:
    c1d = make_rows(400, "1d", seed=seed, end_ms=END)
    c4h = make_rows(700, "4h", seed=seed, end_ms=END)
    c1h = make_rows(2800, "1h", seed=seed, end_ms=END)
    cfg = BacktestConfig(window=150)
    bars, decs = replay(_frame(c1h), _frame(c4h), _frame(c1d), EngineConfig(), {"d": EngineConfig().weights}, cfg, 300)
    trades, _ = simulate(_frame(c4h), bars, decs["d"], EngineConfig(), cfg)
    return {"bars": bars,
            "decisions": [[d.setup, d.tradable, d.score, d.stop, d.target, d.raw_setup, d.downgrade, d.rr_t1,
                           sorted(d.gate_keys)] for d in decs["d"]],
            "trades": [asdict(t) for t in trades]}


if __name__ == "__main__":
    out = {"engine": ENGINE_VERSION, "seeds": {str(s): snapshot(s) for s in SEEDS}}
    Path(__file__).with_name("golden_signal16_standard.json").write_text(json.dumps(out, separators=(",", ":")))
    print(out["engine"], {s: (len(v["decisions"]), len(v["trades"])) for s, v in out["seeds"].items()})
