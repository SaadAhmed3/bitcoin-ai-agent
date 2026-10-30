"""Risk calculator and paper trading tests (no network)."""
import pytest
from conftest import FIXED_NOW

from services.paper_trading import PaperTrading
from services.risk_manager import RiskSettings, loss_streak, position_size

NOCOST = RiskSettings(fee_pct=0, slippage_pct=0)


# ------------------------------------------------------------------ position sizing
def test_size_risks_the_chosen_percent():
    r = position_size(10_000, 100, 95, RiskSettings(risk_pct=1, max_exposure_pct=100, fee_pct=0, slippage_pct=0))
    assert r["max_loss_at_stop"] == pytest.approx(100.0)   # 1% of 10,000
    assert r["quantity_btc"] == pytest.approx(20.0) and r["notional"] == pytest.approx(2000.0)


def test_costs_reduce_size():
    plain = position_size(10_000, 100, 95, RiskSettings(max_exposure_pct=100, fee_pct=0, slippage_pct=0))
    costly = position_size(10_000, 100, 95, RiskSettings(max_exposure_pct=100, fee_pct=0.5, slippage_pct=0.5))
    assert costly["quantity_btc"] < plain["quantity_btc"]
    assert costly["max_loss_at_stop"] == pytest.approx(100.0, abs=0.01)   # still risks exactly 1% incl. costs


def test_exposure_cap_limits_size():
    r = position_size(10_000, 100, 99.5, RiskSettings(risk_pct=2, max_exposure_pct=50, fee_pct=0, slippage_pct=0))
    assert r["capped_by_exposure"] and r["notional"] == pytest.approx(5000.0)
    assert r["max_loss_at_stop"] < 200


def test_size_never_increases_after_losses():
    base = position_size(10_000, 100, 95, RiskSettings(max_exposure_pct=100))
    after = position_size(10_000, 100, 95, RiskSettings(max_exposure_pct=100), recent_pnls=[10, -5, -7])
    assert after["quantity_btc"] < base["quantity_btc"] and after["loss_streak"] == 2
    one = position_size(10_000, 100, 95, RiskSettings(max_exposure_pct=100), recent_pnls=[-5])
    assert one["quantity_btc"] == pytest.approx(base["quantity_btc"])   # never larger


def test_risk_ceiling_and_validation():
    # Batch 1 (0.9.2): a risk % above the ceiling is REJECTED instead of silently capped (owner's requirement:
    # risk % must lie in (0, max_risk_pct]). The ceiling itself is still the most a trade can risk.
    r = position_size(10_000, 100, 95, RiskSettings(risk_pct=2, max_risk_pct=2, max_exposure_pct=100, fee_pct=0, slippage_pct=0))
    assert r["risk_pct_used"] == 2 and r["max_loss_at_stop"] == pytest.approx(200)
    for risk in (5, 2.01, 0, -1):
        with pytest.raises(ValueError, match="risk_pct"):
            RiskSettings(risk_pct=risk, max_risk_pct=2).validate()
    for bad in ((10_000, 100, 105), (0, 100, 95), (10_000, -1, 95)):
        with pytest.raises(ValueError):
            position_size(*bad, RiskSettings())
    with pytest.raises(ValueError, match="no leverage"):
        RiskSettings.from_dict({"max_exposure_pct": 150})
    with pytest.raises(ValueError):
        RiskSettings.from_dict({"magic": 1})


def test_warnings():
    r = position_size(10_000, 100, 99.8, RiskSettings(), atr=2.0)
    joined = " ".join(r["warnings"])
    assert "0.5%" in joined and "ATR" in joined and "not guaranteed" in joined


def test_loss_streak():
    assert loss_streak([5, -1, -2]) == 2 and loss_streak([-1, 3]) == 0 and loss_streak([]) == 0


# ------------------------------------------------------------------ paper trading
def P(price, stale=False):
    return {"price": price, "is_stale": stale, "status": "stale" if stale else "fresh"}


@pytest.fixture
def paper(db):
    t = {"now": FIXED_NOW}
    pt = PaperTrading(db, clock=lambda: t["now"])
    pt.reset(10_000)
    pt.update_settings({"fee_pct": 0.1, "slippage_pct": 0.0, "max_exposure_pct": 100})
    return pt, t


def test_buy_and_sell_round_trip_with_fees(paper):
    pt, _ = paper
    r = pt.buy(P(100.0), 1000)
    assert r["simulated"] and r["message"].startswith("SIMULATED BUY")
    st = pt.state(P(100.0))
    assert st["cash"] == pytest.approx(10_000 - 1000 - 1.0) and st["btc"] == pytest.approx(10.0)
    s = pt.sell(P(110.0))
    # 10 BTC * 110 = 1100, sell fee 1.1; cost basis 1001 -> P/L 97.9
    assert s["realized_pnl"] == pytest.approx(1100 - 1.1 - 1001, abs=0.01)
    st = pt.state(P(110.0))
    assert st["btc"] == 0 and st["equity"] == pytest.approx(10_000 + 97.9, abs=0.01)
    assert all(o["simulated"] == 1 for o in st["orders"]) and st["label"].startswith("SIMULATED")


def test_buy_blocked_when_stale_or_no_cash_or_exposure(paper, db):
    pt, _ = paper
    with pytest.raises(ValueError, match="stale"):
        pt.buy(P(100.0, stale=True), 100)
    with pytest.raises(ValueError, match="cash"):
        pt.buy(P(100.0), 50_000)
    pt.update_settings({"max_exposure_pct": 20})
    with pytest.raises(ValueError, match="exposure"):
        pt.buy(P(100.0), 3000)


def test_no_short_selling(paper):
    pt, _ = paper
    with pytest.raises(ValueError, match="no short"):
        pt.sell(P(100.0))


def test_stop_and_target_checks(paper):
    pt, _ = paper
    pt.buy(P(100.0), 1000, stop=95.0, target=120.0)
    assert pt.check_levels(P(100.0)) is None
    r = pt.check_levels(P(94.0))
    assert r and "stop hit" in pt.state(P(94.0))["orders"][0]["reason"]
    pt.buy(P(100.0), 1000, stop=95.0, target=120.0)
    pt.check_levels(P(121.0))
    assert "target hit" in pt.state(P(121.0))["orders"][0]["reason"]


def _concurrently(pt, fn, n=2):
    """Run fn() in n threads that all read the account before any of them writes (two tabs polling at once)."""
    import threading
    barrier = threading.Barrier(n)
    original = pt._account

    def synced():
        acc = original()
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass                       # a correct implementation may serialise the threads: don't deadlock
        return acc

    pt._account = synced
    results, errors = [], []

    def run():
        try:
            results.append(fn())
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    pt._account = original
    return results, errors


def test_concurrent_stop_checks_sell_once(paper):
    """Regression (B1): two simultaneous stop checks recorded two SELLs for one position."""
    pt, _ = paper
    pt.buy(P(100.0), 4000, stop=95.0)               # 40 BTC at 100, fee 4 -> cost basis 100.1
    results, errors = _concurrently(pt, lambda: pt.check_levels(P(90.0)))
    st = pt.state(P(90.0))
    sells = [o for o in st["orders"] if o["side"] == "SELL"]
    assert len(sells) == 1 and not errors
    assert sells[0]["qty"] == pytest.approx(40.0)
    # (90 - 100.1) * 40 - sell fee 3.6 = -407.6
    assert sells[0]["realized_pnl"] == pytest.approx(-407.6, abs=0.01)
    assert st["btc"] == 0 and st["cash"] == pytest.approx(10_000 - 4004 + 3600 - 3.6, abs=0.01)
    assert st["loss_streak"] == 1
    assert sum(1 for r in results if r) == 1         # exactly one caller reports the exit


def test_concurrent_manual_sells_sell_once(paper):
    pt, _ = paper
    pt.buy(P(100.0), 4000)
    results, errors = _concurrently(pt, lambda: pt.sell(P(90.0)))
    sells = [o for o in pt.state(P(90.0))["orders"] if o["side"] == "SELL"]
    assert len(sells) == 1 and len(results) == 1
    assert len(errors) == 1 and "no short" in errors[0].lower()


def test_invalid_stop_target(paper):
    pt, _ = paper
    with pytest.raises(ValueError, match="Stop"):
        pt.buy(P(100.0), 100, stop=101.0)
    with pytest.raises(ValueError, match="Target"):
        pt.buy(P(100.0), 100, target=99.0)


def test_daily_loss_limit_blocks_new_buys(paper):
    pt, _ = paper
    pt.update_settings({"daily_loss_limit_pct": 1.0})
    pt.buy(P(100.0), 5000)
    pt.sell(P(96.0))   # lose ~4% of 5000 = ~200 = ~2% of the account
    assert pt.state(P(96.0))["daily_limit_reached"]
    with pytest.raises(ValueError, match="Daily loss limit"):
        pt.buy(P(96.0), 100)


def test_reset_and_db_rejects_non_simulated_orders(paper, db):
    import sqlite3
    pt, _ = paper
    pt.buy(P(100.0), 100)
    pt.reset(5000)
    st = pt.state(P(100.0))
    assert st["cash"] == 5000 and st["orders"] == []
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute("INSERT INTO paper_orders (created_at, side, qty, price, fee, reason, simulated) "
                         "VALUES (1,'BUY',1,1,0,'x',0)")



def test_avg_fill_and_cost_basis_are_separate(paper):
    pt, _ = paper
    pt.buy(P(100.0), 1000)
    st = pt.state(P(100.0))
    assert st["avg_fill"] == pytest.approx(100.0)               # fill price (no slippage in this fixture)
    assert st["cost_basis"] == pytest.approx(100.1)             # includes the 0.1% buy fee


def test_migration_adds_column_to_existing_database(tmp_path):
    import sqlite3
    from database import Database
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE paper_account (id INTEGER PRIMARY KEY CHECK (id = 1), created_at INTEGER NOT NULL,
                    start_balance REAL NOT NULL, cash REAL NOT NULL, btc REAL NOT NULL, avg_entry REAL, stop REAL,
                    target REAL, settings TEXT NOT NULL)""")
    conn.execute("INSERT INTO paper_account VALUES (1, 1, 5000, 4000, 0.01, 100000, NULL, NULL, '{}')")
    conn.commit(); conn.close()
    db = Database(str(path)); db.init(); db.init()   # idempotent
    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(paper_account)")}
        row = c.execute("SELECT cash, avg_fill FROM paper_account").fetchone()
    assert "avg_fill" in cols and row["cash"] == 4000 and row["avg_fill"] is None
