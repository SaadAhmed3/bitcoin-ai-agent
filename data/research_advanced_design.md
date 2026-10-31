# Advanced analysis on design data (design-data evidence only)

Pre-registered in `PREREGISTRATION_ADVANCED.md` before this run. Engine signal-1.8 unchanged. Decisions 2022-11-01 11:59 UTC to 2025-10-28 19:59 UTC (6,555 closed 4h bars), all before the holdout start 2025-10-29 00:00 UTC. Run once; nothing tuned or picked.

## Part A: variants (filter only)

| Variant | Weights | Trades | Win rate | Net return | Max DD | Profit factor | Avg trade | False-signal rate |
|---|---|---|---|---|---|---|---|---|
| A | engine only | 5 | 80.0% | 9.08% | 2.62% | 8.73 | 1.774% | 20.0% |
| B | regime 0.15 | 4 | 75.0% | 8.43% | 2.62% | 8.213 | 2.07% | 25.0% |
| C | liquidity_sweep 0.15 | 4 | 75.0% | 8.43% | 2.62% | 8.213 | 2.07% | 25.0% |
| D | volume_profile 0.15 | 5 | 80.0% | 9.08% | 2.62% | 8.73 | 1.774% | 20.0% |
| E | regime 0.10, liquidity_sweep 0.10, volume_profile 0.10 | 5 | 80.0% | 9.08% | 2.62% | 8.73 | 1.774% | 20.0% |

### Walk-forward windows (return % / trades)

| Window | A | B | C | D | E |
|---|---|---|---|---|---|
| 2022-11-01 to 2023-05-02 | 4.01% / 1 | 4.01% / 1 | 4.01% / 1 | 4.01% / 1 | 4.01% / 1 |
| 2023-05-02 to 2023-10-31 | 4.91% / 2 | 4.29% / 1 | 4.29% / 1 | 4.91% / 2 | 4.91% / 2 |
| 2023-10-31 to 2024-04-30 | 1.12% / 1 | 1.12% / 1 | 1.12% / 1 | 1.12% / 1 | 1.12% / 1 |
| 2024-04-30 to 2024-10-29 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 |
| 2024-10-30 to 2025-04-30 | -1.15% / 1 | -1.15% / 1 | -1.15% / 1 | -1.15% / 1 | -1.15% / 1 |
| 2025-04-30 to 2025-10-28 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 |

### Variant A trades by regime at the decision bar

| Regime | Trades | Wins | Sum of returns |
|---|---|---|---|
| LOW VOLATILITY | 2 | 1 | 2.866% |
| TRANSITIONAL / UNCERTAIN | 1 | 1 | 0.593% |
| TRENDING BULLISH | 2 | 2 | 5.413% |

Regime labels over the design bars: TRENDING BULLISH 1,315, TRENDING BEARISH 743, SIDEWAYS / RANGING 1,025, HIGH VOLATILITY 811, LOW VOLATILITY 970, TRANSITIONAL / UNCERTAIN 1,691. Confirmed sweeps at design bars: buy-side 152, sell-side 157.

## Part B: event study (forward return before costs, block-bootstrap 95% CI of the group mean)

### +6 bars (unconditional mean 0.187%)

| Group | n | Mean | vs all bars | 95% CI | Positive |
|---|---|---|---|---|---|
| ALL BARS | 6550 | 0.187% | 0.0% | [0.078%, 0.3102%] | 52.8% |
| TRENDING BULLISH | 1315 | 0.1406% | -0.0464% | [-0.1066%, 0.3938%] | 51.7% |
| TRENDING BEARISH | 743 | 0.1648% | -0.0222% | [-0.2134%, 0.5598%] | 54.9% |
| SIDEWAYS / RANGING | 1025 | 0.1027% | -0.0843% | [-0.1128%, 0.3062%] | 52.1% |
| HIGH VOLATILITY | 811 | 0.4318% | 0.2448% | [0.0204%, 0.8352%] | 56.8% |
| LOW VOLATILITY | 970 | 0.1526% | -0.0344% | [-0.0955%, 0.4108%] | 50.0% |
| TRANSITIONAL / UNCERTAIN | 1686 | 0.1863% | -0.0007% | [0.0083%, 0.3752%] | 52.8% |
| CONFIRMED SWEEP: buy-side | 152 | -0.0988% | -0.2858% | [-0.441%, 0.2641%] | 46.7% |
| CONFIRMED SWEEP: sell-side | 157 | 0.0583% | -0.1287% | [-0.2953%, 0.4002%] | 56.7% |

### +18 bars (unconditional mean 0.5662%)

| Group | n | Mean | vs all bars | 95% CI | Positive |
|---|---|---|---|---|---|
| ALL BARS | 6538 | 0.5662% | 0.0% | [0.1911%, 0.9192%] | 54.3% |
| TRENDING BULLISH | 1315 | 0.0976% | -0.4686% | [-0.6807%, 0.834%] | 49.5% |
| TRENDING BEARISH | 743 | 0.8573% | 0.2911% | [0.0599%, 1.6542%] | 62.7% |
| SIDEWAYS / RANGING | 1023 | 0.53% | -0.0362% | [-0.1161%, 1.1964%] | 54.0% |
| HIGH VOLATILITY | 811 | 1.1767% | 0.6105% | [0.1402%, 2.2348%] | 59.2% |
| LOW VOLATILITY | 968 | 0.9914% | 0.4252% | [0.2289%, 1.7349%] | 57.0% |
| TRANSITIONAL / UNCERTAIN | 1678 | 0.2862% | -0.28% | [-0.2417%, 0.772%] | 50.5% |
| CONFIRMED SWEEP: buy-side | 152 | 0.2853% | -0.2809% | [-0.4843%, 1.0477%] | 50.7% |
| CONFIRMED SWEEP: sell-side | 157 | 0.293% | -0.2732% | [-0.4373%, 1.0234%] | 52.9% |

## Reading (fixed in advance)

Design-data evidence only. Nothing here changes the live signal, its weights, thresholds or NO TRADE gates. With about five engine trades, variants A-E cannot be told apart statistically. An event-study group whose CI excludes the unconditional mean is at most a candidate for a separate, pre-registered forward test; the app stores the shadow outputs live from 2026-10-31 for that purpose.
