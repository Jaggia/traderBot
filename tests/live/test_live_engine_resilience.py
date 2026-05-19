"""
Resilience tests for LiveEngine — validates behavior under broker failure.

Ensures that the engine does not enter a 'zombie' state (position open in memory 
but closed or unknown at broker) when API calls fail.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
import pandas as pd

from src.live.live_engine import LiveEngine
from src.options.position import Position

class FailingTrader:
    """Simulates a broker that fails on specific calls."""
    def __init__(self):
        self.sell_should_fail = False
        self.buy_count = 0
        self.sell_count = 0

    def buy_option(self, symbol, qty):
        self.buy_count += 1
        return f"order_{self.buy_count}"

    def sell_option(self, symbol, qty):
        self.sell_count += 1
        if self.sell_should_fail:
            raise RuntimeError("Broker connection lost during sell")
        return f"order_sell_{self.sell_count}"

    def get_order_status(self, order_id):
        return "filled"

    def get_option_mid_price(self, symbol):
        return 5.0

    def get_option_positions(self):
        return []

    def cancel_all_orders(self):
        pass

class TestLiveEngineResilience(unittest.TestCase):
    def setUp(self):
        self.config = {
            "strategy": {"trade_mode": "options", "signal_system": "smi_wr"},
            "exits": {
                "profit_target_pct": 20.0,
                "stop_loss_pct": 20.0,
                "eod_close": True,
                "opposite_signal": True,
                "eod_cutoff_time": "15:55"
            },
            "position": {"contracts_per_trade": 1},
            "costs": {}
        }
        self.warmup_df = pd.DataFrame() # not used for direct _close calls
        self.trader = FailingTrader()
        
        # Patch create_strategy to avoid indicator computation overhead
        with patch("src.live.live_engine.create_strategy"):
            self.engine = LiveEngine(self.config, self.warmup_df, self.trader)

    def test_close_leaves_position_open_if_sell_fails(self):
        """
        C-5 / Bug 10 Fix:
        If sell_option() raises an exception, the engine MUST NOT clear
        self._position so that the position is not 'orphaned' and lost from tracking.
        """
        # 1. Setup an open position
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime.now(),
            contracts=1, trade_mode="options", option_type="C",
            strike=400.0, expiry=datetime(2026, 12, 31),
            raw_symbol="SYMBOL261231C00400000", current_price=5.0
        )
        self.engine._position = pos
        self.engine._order_id = "init_order"
        
        # 2. Force the next sell to fail
        self.trader.sell_should_fail = True
        
        # 3. Trigger close
        ts = pd.Timestamp.now()
        self.engine._close(0, ts, "test_failure")
        
        # 4. Verify outcomes
        self.assertEqual(self.trader.sell_count, 1, "Sell was attempted")
        self.assertIsNotNone(self.engine._position, "Position was left open despite failure")
        self.assertIsNotNone(self.engine._order_id, "Order ID was kept")
        self.assertTrue(self.engine._sell_failed, "Internal flag _sell_failed was set for operator info")
        
        # 5. Verify trade was not logged as closed
        closed = self.engine._closed_trades
        self.assertEqual(len(closed), 0, "Trade record should not be created for failed sells")

    def test_force_close_respects_lock_and_leaves_state(self):
        """Verify manual force_close (Ctrl+C) also follows safety path leaving position open if sell fails."""
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime.now(),
            contracts=1, trade_mode="options", option_type="C",
            strike=400.0, expiry=datetime(2026, 12, 31),
            raw_symbol="SYMBOL261231C00400000", current_price=5.0
        )
        self.engine._position = pos
        
        self.trader.sell_should_fail = True
        self.engine.force_close("manual_stop")
        
        self.assertIsNotNone(self.engine._position)
        self.assertTrue(self.engine._sell_failed)

if __name__ == "__main__":
    unittest.main()
