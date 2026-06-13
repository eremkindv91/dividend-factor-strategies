"""
Константы из notebooks/03_ml_dividend_forecast.ipynb (раздел CONFIGURATION).
Значения скопированы дословно — не менять, иначе разойдётся с ВКР.
"""

SEED = 42
CV_START_YEAR = 2016
YIELD_CAP = 100.0

# ── Гиперпараметры (как в методологии ВКР) ──
XGB_ITER = 600
XGB_LR = 0.04
XGB_DEPTH = 6

# ── Имена колонок (рынок РФ) ──
RF_DPS_COL = "dps_rub"
RF_FLAG_COL = "div_paid_flag"
RF_PRICE_COL = "price_end"

# ── Категориальные признаки РФ ──
RF_CAT_COLS = ["sector", "listing_level"]

# ── Защита от утечки: всегда исключаемые из признаков колонки ──
EXCLUDE_ALWAYS = {
    "ticker", "year", "country", "currency",
    "div_paid_flag", "div_yield_pct", "dps_rub", "div_per_share",
    "payout_ratio_pct", "paid_next", "dps_next", "y_next_pct",
    "label_known_paid", "label_known_yield",
    "fwd_ret_cal_year_pct",
    "buyback_yield_pct", "total_shareholder_yield_pct",
    "oil_div_payout_pct",
    "sector_group", "post_2022",
}
EXCLUDE_PREFIXES = ("fwd_", "target_", "label_", "_tmp_")

# Лучшая модель РФ по walk-forward AUC (results_snapshot.json: best_model_rf="xgboost",
# auc_rf≈0.904). На ней строится иллюстративный per-ticker SHAP.
BEST_MODEL_RF = "xgboost"
