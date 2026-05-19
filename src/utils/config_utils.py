import logging

logger = logging.getLogger(__name__)

def validate_config(config: dict) -> None:
    """Validate that the configuration contains all required keys.
    
    Raises ValueError if critical keys are missing.
    """
    if "strategy" not in config:
        raise ValueError("Config missing 'strategy' section")
    if "exits" not in config:
        raise ValueError("Config missing 'exits' section")
    
    # Check for critical sizing/exit params
    exits = config["exits"]
    if "profit_target_pct" not in exits:
        raise ValueError("Config missing 'exits.profit_target_pct'")
    if "stop_loss_pct" not in exits:
        raise ValueError("Config missing 'exits.stop_loss_pct'")
        
    strat = config["strategy"]
    trade_mode = strat.get("trade_mode")
    if not trade_mode:
        raise ValueError("Config missing 'strategy.trade_mode'")
        
    if trade_mode == "options":
        if "options" not in config:
            raise ValueError("trade_mode is 'options' but 'options' section is missing")
        import os
        if not os.getenv("DATA_BENTO_PW") and not os.getenv("DATABENTO_API_KEY"):
            raise ValueError("Missing Databento API key. Please set DATA_BENTO_PW or DATABENTO_API_KEY")
