# Rotation survivorship stress test (reported only)

**Reported only: a survivorship stress test of the Rotation rule. The Rotation strategy, its stored design result and its PASS verdict are unchanged.**

Pre-registered in `PREREGISTRATION_ROTATION_STRESS.md`; run once on design data. Same rule and configuration (`f3de8ea9c08bc780`), same window (Mondays from 2021-06-28 to 2025-10-28), same costs.

## Universe

Requested (18): `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `ADAUSDT`, `DOGEUSDT`, `XRPUSDT`, `DOTUSDT`, `UNIUSDT`, `BCHUSDT`, `LTCUSDT`, `LINKUSDT`, `MATICUSDT`, `THETAUSDT`, `XLMUSDT`, `ICPUSDT`, `LUNAUSDT`, `FTTUSDT`.

Available on Binance (18): `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `ADAUSDT`, `DOGEUSDT`, `XRPUSDT`, `DOTUSDT`, `UNIUSDT`, `BCHUSDT`, `LTCUSDT`, `LINKUSDT`, `MATICUSDT`, `THETAUSDT`, `XLMUSDT`, `ICPUSDT`, `LUNAUSDT`, `FTTUSDT`.

Not available (0): none.

History ends inside the window (first gap > 7 days, delisting or end of data; later rows under the same ticker are a different asset and are not used): `MATICUSDT` 2024-09-10, `LUNAUSDT` 2022-05-13, `FTTUSDT` 2022-11-15.

## Result next to the original

| | Original (BTC, ETH, SOL, BNB) | Wider universe | BTC buy & hold |
|---|---|---|---|
| Net return | +3193.17% | +259.02% | +224.36% |
| Max drawdown | 59.70% | 76.49% | 76.63% |
| Return / max drawdown | 53.49 | 3.39 | 2.93 |
| Switches | 91 | 107 | – |
| Beats random weekly rotations | 97.7% | 84.1% | – |
| Verdict | PASS (stands) | FAIL if it were judged (it is not) | – |

Weeks held in the wider universe: SOL 32, cash 30, XRP 21, LUNA 18, LINK 15, BCH 15, BNB 13, DOGE 11, LTC 10, XLM 10, MATIC 9, ICP 9, UNI 8, THETA 7, ETH 7, BTC 6, ADA 4, FTT 2.

## Criteria applied to the wider universe (reported only)

| # | Rule | Passed |
|---|---|---|
| C1 | return / max drawdown >= BTC buy & hold's (same window) | yes |
| C2 | max drawdown <= BTC buy & hold's | yes |
| C3 | beats >= 90% of 1,000 random weekly rotations among the same assets | no |

Limitations: a static list fixed at the start (later top-20 entrants are not added); one price source (Binance spot USDT pairs); ticker migrations (MATIC to POL) are not followed; delisting is a sale at the last close, which may be better than what was actually possible.
