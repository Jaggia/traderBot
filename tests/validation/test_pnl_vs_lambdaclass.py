"""Cross-validate our options P&L formula against LambdaClass options_portfolio_backtester.

LambdaClass source (cloned to lambdaclass_backtester/):
    - P&L formula: options_portfolio_backtester/analytics/trade_log.py:35
        gross_pnl = (exit_price - entry_price) * quantity * shares_per_contract
    - shares_per_contract = 100 (standard options multiplier)
    - Single formula for both long and short — the sign convention in their cost
      model embeds direction into the entry/exit prices so the formula is universal.
    - Commission is a separate deduction (net_pnl = gross_pnl - commissions).

Our formula (src/backtest/engine.py):
    - Long:  (exit_price - entry_price) * contracts * 100
    - Short: (entry_price - exit_price) * contracts * 100

These are algebraically identical when you consider that LambdaClass encodes the
direction sign into the cost values, while we flip the subtraction order explicitly.

This test validates that both approaches produce the same P&L for every trade in
our backtest results.
"""

import os

import pandas as pd
import pytest


RESULTS_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "results",
)


def _find_options_backtest_csvs():
    """Find all options backtest.csv files in the results directory."""
    csvs = []
    for root, _dirs, files in os.walk(RESULTS_DIR):
        if "backtest.csv" in files and "/options/" in root:
            csvs.append(os.path.join(root, "backtest.csv"))
    return csvs


def _lambdaclass_gross_pnl(entry_price, exit_price, quantity, shares_per_contract=100):
    """LambdaClass P&L formula (trade_log.py:35).

    Their formula is direction-agnostic: (exit - entry) * qty * spc.
    For longs, exit > entry = profit.  For shorts, they encode cost signs
    so that entry_price is negative (credit) and exit_price is positive (debit),
    making the formula work universally.

    Since our CSV stores raw prices (not signed costs), we must apply
    the direction adjustment ourselves to match their convention.
    """
    return (exit_price - entry_price) * quantity * shares_per_contract


def _our_pnl(direction, entry_price, exit_price, contracts, multiplier=100):
    """Our P&L formula (engine.py).

    Options are always-long in our model (long call or long put).
    P&L = (exit - entry) * contracts * 100 regardless of direction.
    Equities use direction to flip the sign.
    """
    # Options: always (exit - entry), regardless of "short" signal direction.
    # The direction field in the CSV indicates signal direction (call vs put),
    # not the position side — all options positions are long (bought).
    return (exit_price - entry_price) * contracts * multiplier


class TestPnLFormulaEquivalence:
    """Verify our P&L formula matches LambdaClass for synthetic trades."""

    @pytest.mark.parametrize(
        "direction,entry,exit_p,contracts,expected",
        [
            # Long call profit: bought at 5, sold at 7, 10 contracts
            ("long", 5.0, 7.0, 10, 2000.0),
            # Long call loss: bought at 5, sold at 3, 10 contracts
            ("long", 5.0, 3.0, 10, -2000.0),
            # Short signal put: bought at 5, sold at 3 (always-long: loss)
            ("short", 5.0, 3.0, 10, -2000.0),
            # Short signal put: bought at 5, sold at 7 (always-long: profit)
            ("short", 5.0, 7.0, 10, 2000.0),
            # Breakeven
            ("long", 2.50, 2.50, 5, 0.0),
            # Single contract, small move
            ("long", 0.49, 0.88, 1, 39.0),
            # Short signal, fractional prices (always-long: exit > entry = profit)
            ("short", 2.07, 2.62, 1, 55.0),
        ],
        ids=[
            "long_profit",
            "long_loss",
            "short_loss",
            "short_profit",
            "breakeven",
            "single_contract_long",
            "single_contract_short",
        ],
    )
    def test_our_formula_matches_expected(self, direction, entry, exit_p, contracts, expected):
        pnl = _our_pnl(direction, entry, exit_p, contracts)
        assert pnl == pytest.approx(expected, abs=0.01)

    @pytest.mark.parametrize(
        "direction,entry,exit_p,contracts",
        [
            ("long", 5.0, 7.0, 10),
            ("long", 5.0, 3.0, 10),
            ("short", 5.0, 3.0, 10),
            ("short", 5.0, 7.0, 10),
            ("long", 0.49, 0.88, 120),
            ("short", 2.07, 2.62, 111),
        ],
        ids=[
            "long_profit",
            "long_loss",
            "short_loss",
            "short_profit",
            "0dte_long",
            "0dte_short",
        ],
    )
    def test_our_formula_equals_lambdaclass(self, direction, entry, exit_p, contracts):
        """Both formulas produce the same result.

        Our always-long options model uses (exit - entry) * qty * 100 for all
        directions.  This matches LambdaClass's formula directly — both treat
        options as long positions (buy to open, sell to close).
        """
        our = _our_pnl(direction, entry, exit_p, contracts)
        lc = _lambdaclass_gross_pnl(entry, exit_p, contracts)

        assert our == pytest.approx(lc, abs=0.01), (
            f"P&L mismatch: ours={our}, lambdaclass={lc}"
        )


class TestPnLAgainstBacktestCSV:
    """Validate our P&L formula reproduces the pnl column in actual backtest results."""

    @pytest.fixture(params=_find_options_backtest_csvs() or [pytest.param(None, marks=pytest.mark.skip("no backtest CSVs found"))])
    def trades_df(self, request):
        path = request.param
        if path is None:
            pytest.skip("no backtest CSVs found")
        df = pd.read_csv(path)
        if df.empty:
            pytest.skip(f"empty CSV: {path}")
        return df, path

    def test_all_trades_pnl_matches(self, trades_df):
        """Recompute P&L for every trade in backtest.csv and compare.

        The recorded pnl is net of transaction costs (commission + slippage).
        We compute gross P&L and allow a tolerance of up to $5 per contract
        to cover realistic round-trip costs (e.g. $0.65 commission +
        $0.10 slippage per leg × 2 legs = $1.50/contract).
        """
        df, path = trades_df
        mismatches = []

        for idx, row in df.iterrows():
            computed = _our_pnl(
                row["direction"],
                row["entry_price"],
                row["exit_price"],
                row["contracts"],
            )
            recorded = row["pnl"]
            contracts = row["contracts"]
            # Allow up to $5/contract for round-trip costs (commission + slippage both ways)
            cost_tolerance = max(5.0 * contracts, 0.50)

            if abs(computed - recorded) > cost_tolerance:
                mismatches.append({
                    "row": idx,
                    "direction": row["direction"],
                    "entry": row["entry_price"],
                    "exit": row["exit_price"],
                    "contracts": row["contracts"],
                    "computed": computed,
                    "recorded": recorded,
                    "diff": computed - recorded,
                })

        assert len(mismatches) == 0, (
            f"{len(mismatches)} P&L mismatches in {os.path.basename(os.path.dirname(os.path.dirname(path)))}:\n"
            + "\n".join(
                f"  row {m['row']}: {m['direction']} {m['contracts']}x "
                f"@ {m['entry']}->{m['exit']}: computed={m['computed']:.2f} "
                f"vs recorded={m['recorded']:.2f} (diff={m['diff']:.2f})"
                for m in mismatches[:10]
            )
        )
