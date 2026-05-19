import pandas as pd
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — saves files without opening windows
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from src.analysis.metrics import compute_drawdown_pct


def plot_equity_curve(equity_df: pd.DataFrame, title: str = "Equity Curve", save_path: str = None):
    """Plot the portfolio equity curve."""
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(equity_df.index, equity_df["equity"], linewidth=1, color="steelblue")
    ax.set_title(title)
    ax.set_ylabel("Portfolio Value ($)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150)
    plt.close(fig)


def plot_drawdown(equity_df: pd.DataFrame, save_path: str = None):
    """Plot the drawdown chart."""
    equity = equity_df["equity"]
    drawdown = compute_drawdown_pct(equity) * 100

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(drawdown.index, drawdown, 0, color="salmon", alpha=0.5)
    ax.plot(drawdown.index, drawdown, color="red", linewidth=0.8)
    ax.set_title("Drawdown")
    ax.set_ylabel("Drawdown (%)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150)
    plt.close(fig)


def plot_signals_on_price(
    price_df: pd.DataFrame, trade_log: pd.DataFrame, save_path: str = None
):
    """Overlay entry/exit markers on a price chart."""
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(price_df.index, price_df["close"], linewidth=0.6, color="gray", label="SYMBOL Close")

    if not trade_log.empty:
        longs = trade_log[trade_log["direction"] == "long"]
        shorts = trade_log[trade_log["direction"] == "short"]

        # Entries
        ax.scatter(longs["entry_time"], longs["entry_price"],
                   marker="^", color="green", s=40, zorder=5, label="Long Entry")
        ax.scatter(shorts["entry_time"], shorts["entry_price"],
                   marker="v", color="red", s=40, zorder=5, label="Short Entry")

        # Exits
        ax.scatter(trade_log["exit_time"], trade_log["exit_price"],
                   marker="x", color="black", s=30, zorder=5, label="Exit")

    ax.set_title("Price with Trade Signals")
    ax.set_ylabel("Price ($)")
    ax.legend(loc="upper left", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150)
    plt.close(fig)
