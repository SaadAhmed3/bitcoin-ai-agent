"""Strategy Lab engines (research only). See base.py for the shared interface and result schema."""
from services.strategies import dca

# Implemented engines: module per strategy id. Each module provides STRATEGY_ID, VERSION, DEFAULTS,
# PREREGISTRATION (file name), validate_config(), evaluate() and backtest().
ENGINES = {"dca": dca}
