import pytest
from datetime import datetime

from src.options.position import Position
from src.backtest.portfolio import Portfolio


@pytest.fixture
def config():
    return {
        "costs": {
            "commission_per_contract": 0.65,
            "slippage_pct": 0.1,          # equities only: % of share price
            "slippage_per_contract": 0.10, # options only: flat $ per contract (half bid-ask spread)
        },
        "exits": {
            "profit_target_pct": 20.0,
            "stop_loss_pct": 50.0,
            "eod_close": True,
            "opposite_signal": True,
        },
        "position": {"contracts_per_trade": 1, "max_concurrent_positions": 1},
    }


class TestPosition:
    def test_equity_pnl(self):
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pos.update_price(405.0)
        assert pos.unrealized_pnl() == 500.0  # 5 * 100

    def test_option_pnl(self):
        pos = Position(
            direction=1, entry_price=3.00, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="options", option_type="C", strike=400, expiry=datetime(2023, 1, 10)
        )
        pos.update_price(4.50)
        # (4.50 - 3.00) * 1 * 100 * 1 = 150
        assert pos.unrealized_pnl() == 150.0

    def test_pnl_pct(self):
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities"
        )
        pos.update_price(420.0)
        assert pos.pnl_pct() == pytest.approx(5.0)

    def test_pnl_pct_short_equity(self):
        """Short equity profits when price falls — direction=-1 flips the sign."""
        pos = Position(
            direction=-1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities"
        )
        pos.update_price(380.0)
        # pnl_pct = ((380-400)/400)*100 * -1 = (-5%) * -1 = +5%
        assert pos.pnl_pct() == pytest.approx(5.0)

    def test_pnl_pct_short_equity_loss(self):
        """Short equity loses when price rises."""
        pos = Position(
            direction=-1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities"
        )
        pos.update_price(420.0)
        # pnl_pct = ((420-400)/400)*100 * -1 = (+5%) * -1 = -5%
        assert pos.pnl_pct() == pytest.approx(-5.0)

class TestPortfolio:
    def test_open_and_close_equity(self, config):
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pf.open_position(pos)
        # notional=40_000, txn_cost=0.65*100 + 400*0.001*100 = 65+40 = 105
        assert pf.cash == pytest.approx(59_895.0, abs=0.01)
        assert len(pf.positions) == 1

        pf.close_position(pos, exit_price=410.0, exit_time=datetime(2023, 1, 3, 12, 0), reason="profit_target")
        assert len(pf.positions) == 0
        assert len(pf.closed_trades) == 1
        assert pf.closed_trades[0]["exit_reason"] == "profit_target"

    def test_can_open_respects_max(self, config):
        pf = Portfolio(initial_cash=100_000, config=config)
        assert pf.can_open()
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities"
        )
        pf.open_position(pos)
        assert not pf.can_open()  # max_concurrent_positions = 1

    def test_mark_to_market(self, config):
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pf.open_position(pos)
        pos.update_price(410.0)
        pf.mark_to_market(datetime(2023, 1, 3, 10, 5))
        assert len(pf.equity_curve) == 1
        # cash after open: 100_000 - (400*100 + 65 + 40) = 59_895
        # position value at 410: 410*100 = 41_000
        # equity = 59_895 + 41_000 = 100_895
        assert pf.equity_curve[0]["equity"] == pytest.approx(100_895.0, abs=0.01)

    def test_close_position_pnl_correctness(self, config):
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pf.open_position(pos)
        pf.close_position(pos, exit_price=410.0, exit_time=datetime(2023, 1, 3, 12, 0), reason="profit_target")
        # pnl = (410-400)*100 - entry_cost - exit_cost
        # entry_cost = 0.65*100 + 400*0.001*100 = 65 + 40 = 105
        # exit_cost  = 0.65*100 + 410*0.001*100 = 65 + 41 = 106
        # pnl = 1000 - 105 - 106 = 789
        assert pf.closed_trades[0]["pnl"] == pytest.approx(789.0, abs=0.01)

    def test_options_position_cash_flow(self, config):
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="options", option_type="C", strike=400,
            expiry=datetime(2023, 1, 10)
        )
        pf.open_position(pos)
        # entry: notional=5*1*100=500
        # txn_cost = commission + slippage_per_contract (options mode)
        #          = 0.65*1 + 0.10*1 = 0.75
        # cash after open = 100_000 - 500.75
        cash_after_open = pf.cash
        assert cash_after_open == pytest.approx(100_000 - 500.75, abs=0.01)
        pf.close_position(pos, exit_price=7.0, exit_time=datetime(2023, 1, 3, 12, 0), reason="profit_target")
        # exit: notional=7*1*100=700
        # txn_cost = 0.65*1 + 0.10*1 = 0.75 (same flat cost, price-independent)
        # cash after close = cash_after_open + 700 - 0.75
        assert pf.cash == pytest.approx(cash_after_open + 700 - 0.75, abs=0.01)
        # pnl = (exit_notional - entry_notional) - (entry_cost + exit_cost)
        #      = (700 - 500) - (0.75 + 0.75) = 200 - 1.50 = 198.50
        assert pf.closed_trades[0]["pnl"] == pytest.approx(198.50, abs=0.01)

    def test_short_equity_open_cash_flow(self, config):
        """Short open receives notional cash (borrowed share sale) minus costs."""
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=-1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pf.open_position(pos)
        # short open: cash += notional - costs
        # notional = 400*100 = 40_000
        # txn_cost = 0.65*100 + 400*0.001*100 = 65 + 40 = 105
        # cash = 100_000 + 40_000 - 105 = 139_895
        assert pf.cash == pytest.approx(139_895.0, abs=0.01)

    def test_short_equity_close_pnl(self, config):
        """Short position profits when price falls; P&L accounts for both-way costs."""
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=-1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pf.open_position(pos)
        pf.close_position(pos, exit_price=390.0, exit_time=datetime(2023, 1, 3, 12, 0), reason="profit_target")
        # gross profit = (400-390)*100 = 1_000
        # entry_cost = 0.65*100 + 400*0.001*100 = 65 + 40 = 105
        # exit_cost  = 0.65*100 + 390*0.001*100 = 65 + 39 = 104
        # pnl = 1_000 - 105 - 104 = 791
        assert pf.closed_trades[0]["pnl"] == pytest.approx(791.0, abs=0.01)
        assert pf.closed_trades[0]["direction"] == "short"

    def test_short_equity_loss_when_price_rises(self, config):
        """Short position loses when price rises."""
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=-1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=100, trade_mode="equities"
        )
        pf.open_position(pos)
        pf.close_position(pos, exit_price=410.0, exit_time=datetime(2023, 1, 3, 12, 0), reason="stop_loss")
        # gross loss = (400-410)*100 = -1_000
        # costs = 105 + 106 = 211
        # pnl = -1_000 - 211 = -1_211
        assert pf.closed_trades[0]["pnl"] == pytest.approx(-1_211.0, abs=0.01)

    def test_transaction_costs_deducted(self, config):
        pf = Portfolio(initial_cash=100_000, config=config)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities"
        )
        pf.open_position(pos)
        # Cash should be less than initial - entry_price (due to costs)
        expected_cost = pos.contracts * (400.0 + 0.65 + 400.0 * 0.001)  # price + commission + slippage, scaled by contracts
        assert pf.cash == pytest.approx(100_000 - expected_cost, abs=0.01)


class TestOptionsSlippageCostModel:
    """Pin the correct slippage semantics for options vs equities.

    slippage_pct   — percentage of premium, applied to EQUITIES only (models bid-ask as % of share price)
    slippage_per_contract — flat dollar amount per contract, applied to OPTIONS only (models half bid-ask spread)

    The two parameters must NOT both apply to the same trade; they are mode-specific alternatives.
    """

    def test_options_use_only_slippage_per_contract(self):
        """Options cost = commission + slippage_per_contract only.

        If slippage_pct were also applied, the cost would be higher. This test
        verifies that slippage_pct is ignored for options trades.

        Setup: price=5.0, 1 contract, slippage_pct=1.0, slippage_per_contract=0.10
          commission          = 0.65 * 1 = 0.65
          slippage_per_contract (options only) = 0.10 * 1 = 0.10
          total txn_cost      = 0.75
          notional            = 5.0 * 1 * 100 = 500.00
          cash after open     = 100_000 - (500 + 0.75) = 99_499.25

        If slippage_pct were also applied (bug):
          slippage_pct_amount = 5.0 * 0.01 * 1 * 100 = 5.00   ← must NOT appear
          cash after open     = 100_000 - (500 + 0.65 + 5.00 + 0.10) = 99_494.25
        """
        cfg = {
            "costs": {
                "commission_per_contract": 0.65,
                "slippage_pct": 1.0,          # non-zero; must NOT apply to options
                "slippage_per_contract": 0.10, # the options slippage model
            },
        }
        pf = Portfolio(initial_cash=100_000, config=cfg)
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="options", option_type="C", strike=400,
            expiry=datetime(2023, 1, 10),
        )
        pf.open_position(pos)
        # commission=0.65, slippage_per_contract=0.10, total cost=0.75
        # cash = 100_000 - 500 - 0.75 = 99_499.25
        assert pf.cash == pytest.approx(99_499.25, abs=0.01), (
            "Options slippage must use only slippage_per_contract, not slippage_pct"
        )

    def test_equities_use_only_slippage_pct(self):
        """Equities cost = commission + slippage_pct only.

        slippage_per_contract must NOT apply to equities trades.

        Setup: price=400.0, 1 contract, slippage_pct=0.1, slippage_per_contract=0.10
          commission          = 0.65 * 1 = 0.65
          slippage_pct (equities only) = 400.0 * 0.001 * 1 = 0.40
          total txn_cost      = 1.05
          notional            = 400.0 * 1 = 400.00
          cash after open     = 100_000 - (400 + 1.05) = 99_598.95

        If slippage_per_contract were also applied (bug):
          slippage_flat = 0.10 * 1 = 0.10   ← must NOT appear
          cash after open = 100_000 - (400 + 1.15) = 99_598.85
        """
        cfg = {
            "costs": {
                "commission_per_contract": 0.65,
                "slippage_pct": 0.1,           # equities slippage model
                "slippage_per_contract": 0.10,  # non-zero; must NOT apply to equities
            },
        }
        pf = Portfolio(initial_cash=100_000, config=cfg)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities",
        )
        pf.open_position(pos)
        # commission=0.65, slippage_pct=400*0.001=0.40, total cost=1.05
        # cash = 100_000 - 400 - 1.05 = 99_598.95
        assert pf.cash == pytest.approx(99_598.95, abs=0.01), (
            "Equity slippage must use only slippage_pct, not slippage_per_contract"
        )

    def test_options_transaction_cost_two_contracts(self):
        """slippage_per_contract scales linearly with contract count for options."""
        cfg = {
            "costs": {
                "commission_per_contract": 0.65,
                "slippage_pct": 1.0,           # must be ignored for options
                "slippage_per_contract": 0.10,
            },
        }
        pf = Portfolio(initial_cash=100_000, config=cfg)
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=2, trade_mode="options", option_type="C", strike=400,
            expiry=datetime(2023, 1, 10),
        )
        pf.open_position(pos)
        # notional = 5.0 * 2 * 100 = 1000
        # commission = 0.65 * 2 = 1.30
        # slippage_per_contract = 0.10 * 2 = 0.20
        # total cost = 1.50
        # cash = 100_000 - 1000 - 1.50 = 98_998.50
        assert pf.cash == pytest.approx(98_998.50, abs=0.01)

    def test_options_slippage_extreme_pct_ignored(self):
        """slippage_pct=99.0 must not affect options — only slippage_per_contract applies.

        If slippage_pct were also applied: cost += 5.0*0.99*1*100 = 495 → cash = 99_004.25
        With only slippage_per_contract: cost = 0.65 + 0.05 = 0.70 → cash = 99_499.30
        """
        cfg = {
            "costs": {
                "commission_per_contract": 0.65,
                "slippage_pct": 99.0,            # extreme — must NOT apply to options
                "slippage_per_contract": 0.05,
            },
        }
        pf = Portfolio(initial_cash=100_000, config=cfg)
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="options", option_type="C", strike=400,
            expiry=datetime(2023, 1, 10),
        )
        pf.open_position(pos)
        # notional = 5.0 * 1 * 100 = 500
        # commission = 0.65
        # slippage_per_contract = 0.05
        # total cost = 0.70
        # cash = 100_000 - 500 - 0.70 = 99_499.30
        assert pf.cash == pytest.approx(99_499.30, abs=0.01), (
            "slippage_pct=99.0 must not affect options pricing"
        )


class TestPortfolioInsufficientFunds:
    """M-2 fix: open_position raises ValueError when cash is insufficient."""

    def test_long_equity_raises_when_cash_too_low(self, config):
        """Long equity: not enough cash → ValueError raised, cash unchanged."""
        # notional = 400*10 = 4_000; costs = 0.65*10 + 400*0.001*10 = 6.5+4 = 10.5
        # required = 4_010.5 — set cash to 4_000 (below required)
        pf = Portfolio(initial_cash=4_000.0, config=config)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=10, trade_mode="equities"
        )
        cash_before = pf.cash
        with pytest.raises(ValueError, match="Insufficient funds"):
            pf.open_position(pos)
        # Cash must not have been modified
        assert pf.cash == cash_before
        assert len(pf.positions) == 0

    def test_options_raises_when_cash_too_low(self, config):
        """Options: not enough cash → ValueError raised, cash unchanged."""
        # notional = 5*1*100 = 500
        # costs = commission + slippage_per_contract = 0.65 + 0.10 = 0.75
        # required = 500.75 — set cash to 400 (below required)
        pf = Portfolio(initial_cash=400.0, config=config)
        pos = Position(
            direction=1, entry_price=5.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="options", option_type="C", strike=400,
            expiry=datetime(2023, 1, 10)
        )
        cash_before = pf.cash
        with pytest.raises(ValueError, match="Insufficient funds"):
            pf.open_position(pos)
        assert pf.cash == cash_before
        assert len(pf.positions) == 0

    def test_short_equity_raises_when_cash_too_low(self, config):
        """Short equity also requires cash >= notional + costs (no margin)."""
        pf = Portfolio(initial_cash=1.0, config=config)
        pos = Position(
            direction=-1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=10, trade_mode="equities"
        )
        cash_before = pf.cash
        with pytest.raises(ValueError, match="Insufficient funds"):
            pf.open_position(pos)
        assert pf.cash == cash_before
        assert len(pf.positions) == 0

    def test_long_equity_succeeds_with_exact_funds(self, config):
        """Long equity succeeds when cash equals required amount exactly."""
        # notional=400, costs=0.65+0.4=1.05, required=401.05 for 1 contract
        pf = Portfolio(initial_cash=401.05, config=config)
        pos = Position(
            direction=1, entry_price=400.0, entry_time=datetime(2023, 1, 3, 10, 0),
            contracts=1, trade_mode="equities"
        )
        pf.open_position(pos)
        assert len(pf.positions) == 1
        assert pf.cash == pytest.approx(0.0, abs=0.01)
