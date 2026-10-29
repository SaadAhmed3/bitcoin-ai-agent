# Score study - DESIGN period only (research)

Decisions 2022-10-30 -> 2025-10-25 (6,550 bars; 17 dropped near the cut-off; last forward bar closes 2025-10-28, before the holdout start 2025-10-29).

Decision at each closed 4h bar (default weights, standard mode). Forward return = close 18 bars after the next open (counting the entry bar) / next open - 1, before costs; 'beats costs' = above the 0.30% round trip.

All-bar baseline: mean 0.563%, median 0.285%, 54.2% positive, 49.8% beat costs.

| Score bucket | Bars | Mean % | Median % | % positive | % beat costs | Non-overlap bars | Non-overlap mean % |
|---|---|---|---|---|---|---|---|
| <= -0.25 | 612 | 1.07 | 0.66 | 61.1 | 55.9 | 36 | 0.525 |
| (-0.25, -0.10] | 1146 | 0.426 | 0.425 | 57.5 | 51.9 | 65 | 0.512 |
| (-0.10, 0.10) | 1647 | 0.305 | 0.096 | 51.3 | 47.2 | 90 | 0.027 |
| [0.10, 0.25) | 1569 | 0.622 | 0.332 | 54.4 | 50.6 | 85 | 0.823 |
| [0.25, 0.40) | 1209 | 0.516 | 0.032 | 50.3 | 45.9 | 69 | 0.771 |
| >= 0.40 | 367 | 1.206 | 0.805 | 57.2 | 53.4 | 19 | 1.368 |

| Spearman vs forward return | All bars | Non-overlapping |
|---|---|---|
| score | -0.0179 | 0.0336 |
| trend | -0.0524 | -0.0296 |
| momentum | 0.0195 | 0.0698 |
| volume | 0.0181 | 0.1427 |
| structure | 0.0044 | 0.0401 |
| overextension | -0.0356 | -0.0707 |

Block bootstrap (1000 resamples, block 18, seed 7), 95% CI:
- top bucket (>= 0.40) mean: [0.2465, 2.0999] %
- top bucket minus all-bar mean: [-0.2494, 1.4743] percentage points
- score Spearman: [-0.0926, 0.0508]

Research only: no rule was changed because of this study.
