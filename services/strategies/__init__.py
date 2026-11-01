"""Strategy Lab engines (research only). See base.py for the shared interface and result schema."""
from services.strategies import breakout, dca, trend
from services.strategies import range as range_   # the module is named after its strategy id ("range")

# Implemented engines: module per strategy id. Each module provides STRATEGY_ID, VERSION, DEFAULTS, FIELDS,
# PREREGISTRATION, EVAL_TIMEFRAMES / BACKTEST_TIMEFRAMES / FORWARD_TIMEFRAME, validate_config(), evaluate_frames(),
# backtest(), markdown() and the forward-test hooks forward_step(), forward_state(), forward_summary().
ENGINES = {"dca": dca, "trend": trend, "range": range_, "breakout": breakout}
