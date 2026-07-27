# Sector Feature Validation Report

Run date: `2026-07-27`

Data cutoff: `2026-07-24`

Model used for grouped feature ablation: fixed Ridge (`alpha=10`), with imputer and scaler fitted inside each training fold. Production champion remains ElasticNet.

| Pack | Spearman IC change | Hit-rate change | After-cost gate | Final status |
|---|---:|---:|---|---|
| Oil and gas | +0.008954 | +0.013240 | FAIL | RESEARCH_ONLY |
| Steel and ferrous metals | -0.040264 | -0.026481 | FAIL | RESEARCH_ONLY |
| Banks and financials | +0.006678 | +0.000697 | FAIL | RESEARCH_ONLY |
| Real-estate developers | +0.002087 | -0.000697 | FAIL | RESEARCH_ONLY |

Fixed promotion threshold: Spearman IC improvement at least `0.01`, no hit-rate deterioration, positive top-bottom spread, identical test rows and folds, plus improved after-cost cumulative excess return and non-worse after-cost Sharpe.

## Result

No sector pack was promoted. `approved_feature_columns` is empty and the production model still uses the 25 Stage 1 features. This is the intended safety behavior; thresholds were not weakened after seeing the result.

Base production OOS metrics remain:

- 24 periods;
- cumulative return after costs: `-0.8606%`;
- MCFTR cumulative return: `-6.0248%`;
- cumulative excess return: `+5.1642%`;
- Sharpe after costs: `0.081335`;
- max drawdown: `-25.6425%`;
- average turnover: `40%`.

The weak absolute result remains visible and must not be marketed as evidence of investable alpha.
