#!/usr/bin/env python3
"""Streamlit dashboard for browsing backtest results (read-only)."""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

SOURCE_DIRS = {"db": "Databento", "alpaca": "Alpaca", "tv": "TradingView"}

# Reverse map for display → key
SOURCE_KEY = {v: k for k, v in SOURCE_DIRS.items()}


def _fmt_date_folder(folder_name: str) -> str:
    """Convert compound folder name to readable date range.

    Handles both old ('February-16-2026') and new compound names
    ('February-01-2026_to_February-28-2026_run-March-22-2026_tag').
    """
    parts = folder_name.split("_to_")
    try:
        start_dt = datetime.strptime(parts[0], "%B-%d-%Y")
        if len(parts) > 1:
            # Extract end date (before '_run-' suffix)
            end_segment = parts[1].split("_run-")[0]
            end_dt = datetime.strptime(end_segment, "%B-%d-%Y")
            return f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d, %Y')}"
        return start_dt.strftime("%B %d, %Y")
    except ValueError:
        return folder_name


# ---------------------------------------------------------------------------
# Helpers — file discovery
# ---------------------------------------------------------------------------

def _build_mode_entry(mode_dir: Path) -> dict:
    """Build file references for a single mode/timeframe directory."""
    def _f(name: str) -> Path | None:
        p = mode_dir / name
        return p if p.exists() else None

    return {
        "report": _f("report.md"),
        "tradelog": _f("backtest.csv"),
        "config": _f("config.yaml"),
        "equity_data": _f("equity_data.csv"),
        "price_data": _f("price_data.csv"),
        "charts": {
            "equity_curve": _f("equity_curve.png"),
            "drawdown": _f("drawdown.png"),
            "signals": _f("signals.png"),
        },
    }


@st.cache_data
def discover_runs() -> list[dict]:
    """Scan results/{source}/{run_date}/{date_folder}/{mode}/{timeframe}/ directory tree.

    New layout (current): results/db/YYYY-MM-DD/<range_tag>/options/5min/
    Legacy layout (pre-migration): results/db/<range_run_tag>/options/5min/

    Returns list of run dicts, one per (source, run_date, date_folder). Each run
    has a 'modes' dict: {mode: {timeframe: {files...}}}
    """
    runs = []

    for src_key, src_label in SOURCE_DIRS.items():
        src_dir = RESULTS_DIR / src_key
        if not src_dir.is_dir():
            continue

        for child in sorted(src_dir.iterdir(), reverse=True):
            if not child.is_dir():
                continue

            # Detect new layout: child is a YYYY-MM-DD run-date folder
            try:
                run_date = datetime.strptime(child.name, "%Y-%m-%d")
                run_date_str = child.name
                backtest_dirs = sorted(child.iterdir(), reverse=True)
            except ValueError:
                # Legacy layout: child is directly the backtest range folder
                run_date = None
                run_date_str = "legacy"
                backtest_dirs = [child]

            for date_dir in backtest_dirs:
                if not date_dir.is_dir():
                    continue
                # Validate it looks like a backtest range folder.
                # Extract the first date-like token (%B-%d-%Y) from the folder name,
                # ignoring any leading prefix such as "good_".
                raw_first = date_dir.name.split("_to_")[0] if "_to_" in date_dir.name else date_dir.name
                month_match = re.search(
                    r"(?:^|[_-])([A-Z][a-z]+-\d{2}-\d{4})(?:$|[_-])", raw_first
                )
                if not month_match:
                    # Also try matching directly (legacy single-date folders like "February-16-2026")
                    try:
                        datetime.strptime(raw_first, "%B-%d-%Y")
                    except ValueError:
                        continue
                else:
                    try:
                        datetime.strptime(month_match.group(1), "%B-%d-%Y")
                    except ValueError:
                        continue

                modes = {}
                for mode_dir in sorted(date_dir.iterdir()):
                    if not mode_dir.is_dir():
                        continue
                    mode_name = mode_dir.name  # e.g. "options", "equities"
                    for tf_dir in sorted(mode_dir.iterdir()):
                        if not tf_dir.is_dir():
                            continue
                        tf_name = tf_dir.name  # e.g. "5min"
                        entry = _build_mode_entry(tf_dir)
                        if any(v for k, v in entry.items() if k != "charts") or \
                           any(v for v in entry["charts"].values()):
                            modes.setdefault(mode_name, {})[tf_name] = entry

                if modes:
                    runs.append({
                        "source": src_key,
                        "source_label": src_label,
                        "run_date": run_date_str,
                        "date_folder": date_dir.name,
                        "date_display": _fmt_date_folder(date_dir.name),
                        "modes": modes,
                    })

    return runs


# ---------------------------------------------------------------------------
# Helpers — parsing
# ---------------------------------------------------------------------------

def _parse_numeric(s: str) -> float | None:
    """Strip $, %, commas and convert to float."""
    if not s or s.strip() in ("N/A", "—", ""):
        return None
    cleaned = s.strip().replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


@st.cache_data
def parse_report_md(path: str) -> dict:
    """Parse a report markdown into {metadata: {}, metrics: {}, config: {}, raw: str}."""
    text = Path(path).read_text()
    result = {"metadata": {}, "metrics": {}, "config_table": {}, "exit_reasons": {}, "raw": text}

    # Parse **Key:** value metadata lines (colon may be inside or outside the **)
    for m in re.finditer(r"\*\*(.+?):\*\*\s*(.+)", text):
        result["metadata"][m.group(1).strip()] = m.group(2).strip()

    # Parse metric table
    in_perf = False
    in_exit = False
    in_config = False
    for line in text.splitlines():
        if "Performance Summary" in line:
            in_perf, in_exit, in_config = True, False, False
            continue
        if "Exit Reasons" in line:
            in_perf, in_exit, in_config = False, True, False
            continue
        if "Strategy Config" in line:
            in_perf, in_exit, in_config = False, False, True
            continue
        if line.startswith("## ") and "Charts" not in line:
            in_perf, in_exit, in_config = False, False, False

        if not line.startswith("|") or line.startswith("|--") or line.startswith("|-"):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) < 2:
            continue
        # Skip header rows
        if parts[0] in ("Metric", "Reason", "Parameter"):
            continue
        if parts[1] in ("Value", "Count"):
            continue

        if in_perf:
            val = _parse_numeric(parts[1])
            # Store with both label and a normalized key
            key = parts[0].split("(")[0].strip().lower().replace(" ", "_")
            result["metrics"][key] = {"label": parts[0], "value": val, "raw": parts[1]}
        elif in_exit:
            result["exit_reasons"][parts[0]] = int(parts[1]) if parts[1].isdigit() else parts[1]
        elif in_config:
            result["config_table"][parts[0]] = parts[1]

    return result


@st.cache_data
def load_tradelog(path: str) -> pd.DataFrame:
    """Load a trade log CSV."""
    df = pd.read_csv(path)
    if "entry_time" in df.columns:
        df["entry_time"] = pd.to_datetime(df["entry_time"])
    if "exit_time" in df.columns:
        df["exit_time"] = pd.to_datetime(df["exit_time"])
    return df


@st.cache_data
def load_equity_data(path: str) -> pd.DataFrame:
    """Load equity curve CSV (timestamp index, equity, cash columns)."""
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    return df


@st.cache_data
def load_price_data(path: str) -> pd.DataFrame:
    """Load price data CSV (timestamp index, close column)."""
    df = pd.read_csv(path, parse_dates=[0], index_col=0)
    return df


@st.cache_data
def load_config_yaml(path: str) -> dict:
    """Load a config YAML snapshot."""
    with open(path) as f:
        return yaml.safe_load(f)


@st.cache_data
def parse_comparison_md(path: str) -> list[dict]:
    """Parse armed_mode_comparison.md into list of {source, df} dicts."""
    text = Path(path).read_text()
    sections = []
    current_source = None
    in_code = False
    code_lines = []

    for line in text.splitlines():
        if line.startswith("### ") and "Data" in line:
            current_source = line.replace("###", "").replace("Data", "").strip()
            continue
        if line.strip() == "```" and current_source:
            if in_code:
                # End of code block — parse
                df = _parse_box_table(code_lines)
                if df is not None:
                    sections.append({"source": current_source, "df": df})
                code_lines = []
                in_code = False
                current_source = None
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)

    return sections


def _parse_box_table(lines: list[str]) -> pd.DataFrame | None:
    """Parse box-drawing ASCII table lines into a DataFrame."""
    data_rows = []
    for line in lines:
        # Skip separator rows (containing ─ or ┌ ├ └)
        if any(c in line for c in "─┌├└┬┼┴┐┤┘"):
            if "│" not in line.replace("─", "").replace("┌", "").replace("├", "").replace("└", ""):
                continue
            # It's a separator — skip
            if line.strip().startswith(("┌", "├", "└")):
                continue
        if "│" in line:
            cells = [c.strip() for c in line.split("│")[1:-1]]
            if cells:
                data_rows.append(cells)

    if len(data_rows) < 2:
        return None

    header = data_rows[0]
    rows = data_rows[1:]
    df = pd.DataFrame(rows, columns=header)
    if "Metric" in df.columns:
        df = df.set_index("Metric")
    return df


# ---------------------------------------------------------------------------
# Helpers — run data access
# ---------------------------------------------------------------------------

def _get_available_modes(run: dict) -> list[str]:
    """Return list of mode names available in a run (e.g. ['equities', 'options'])."""
    return sorted(run["modes"].keys())


def _get_first_timeframe(run: dict, mode: str) -> str | None:
    """Return the first available timeframe for a given mode."""
    tfs = run["modes"].get(mode, {})
    return next(iter(tfs), None)


def _get_mode_files(run: dict, mode: str, timeframe: str | None = None) -> dict | None:
    """Get file references for a specific mode+timeframe in a run."""
    mode_tfs = run["modes"].get(mode)
    if not mode_tfs:
        return None
    if timeframe is None:
        timeframe = next(iter(mode_tfs), None)
    if timeframe is None:
        return None
    return mode_tfs.get(timeframe)


def _get_data_range_from_files(files: dict) -> str:
    """Extract human-readable data range from report metadata."""
    if not files or not files.get("report") or not files["report"].exists():
        return ""
    report = parse_report_md(str(files["report"]))
    raw = report["metadata"].get("Data Range", "")
    if not raw:
        return ""
    parts = raw.split(" to ")
    try:
        start = pd.Timestamp(parts[0]).tz_convert("America/New_York").strftime("%b %d, %Y")
        end = pd.Timestamp(parts[1]).strftime("%b %d, %Y")
        return f"{start} — {end} (EST)"
    except (IndexError, ValueError, TypeError):
        return raw


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def sidebar():
    st.sidebar.title("Backtest Dashboard")
    runs = discover_runs()

    if not runs:
        st.sidebar.warning("No results found in results/")
        return None, None

    # Source selector
    available_sources = sorted(set(r["source_label"] for r in runs))
    selected_source = st.sidebar.selectbox(
        "Data Source",
        available_sources,
        index=available_sources.index("Databento") if "Databento" in available_sources else 0,
    )

    # Run Date selector — one entry per date (no trade mode here)
    source_runs = [r for r in runs if r["source_label"] == selected_source]

    if not source_runs:
        st.sidebar.warning(f"No {selected_source} results found")
        return None, None

    run_idx = st.sidebar.selectbox(
        "Run Date", range(len(source_runs)), index=0,
        format_func=lambda i: source_runs[i]["date_display"],
    )
    run = source_runs[run_idx]

    # Page nav
    page = st.sidebar.radio(
        "View",
        ["Overview", "Trade Explorer", "Comparison", "Cross-Run"],
    )

    return run, page


# ---------------------------------------------------------------------------
# View 1: Overview
# ---------------------------------------------------------------------------

def view_overview(run: dict):
    st.header(f"Overview — {run['source_label']} ({run['date_display']})")

    available_modes = _get_available_modes(run)
    if not available_modes:
        st.info("No results found for this run.")
        return

    # Mode radio — only show if multiple modes exist
    if len(available_modes) > 1:
        selected_mode = st.radio("Trade Mode", available_modes, horizontal=True)
    else:
        selected_mode = available_modes[0]

    tf = _get_first_timeframe(run, selected_mode)
    files = _get_mode_files(run, selected_mode, tf)
    if not files:
        st.info(f"No files found for {selected_mode}.")
        return

    date_range = _get_data_range_from_files(files)
    if date_range:
        st.caption(date_range)

    if not files["report"] or not files["report"].exists():
        st.info("No report file found for this mode.")
        return

    report = parse_report_md(str(files["report"]))

    # Metadata bar
    meta = report["metadata"]
    if meta:
        cols = st.columns(len(meta))
        for col, (k, v) in zip(cols, meta.items()):
            col.markdown(f"**{k}:** {v}")

    # Metric cards — 2 rows × 4 cols
    card_keys = [
        ("total_p&l", "Total P&L"),
        ("total_return", "Total Return"),
        ("win_rate", "Win Rate"),
        ("sharpe_ratio", "Sharpe"),
        ("total_trades", "Trades"),
        ("profit_factor", "Profit Factor"),
        ("max_drawdown", "Max Drawdown"),
        ("sortino_ratio", "Sortino"),
    ]

    metrics = report["metrics"]
    row1 = st.columns(4)
    row2 = st.columns(4)
    all_cols = row1 + row2

    for col, (key, label) in zip(all_cols, card_keys):
        m = metrics.get(key)
        if m:
            col.metric(label, m["raw"])
        else:
            col.metric(label, "—")

    # Chart tabs — interactive plotly when CSV data available, PNG fallback
    has_equity_csv = files.get("equity_data") and files["equity_data"].exists()
    has_price_csv = files.get("price_data") and files["price_data"].exists()
    charts = files["charts"]

    if has_equity_csv or has_price_csv or any(v for v in charts.values()):
        tabs = st.tabs(["Equity Curve", "Drawdown", "Signals"])

        with tabs[0]:
            if has_equity_csv:
                eq_df = load_equity_data(str(files["equity_data"]))
                fig = px.line(eq_df, y="equity", title="Equity Curve",
                              labels={"timestamp": "", "equity": "Portfolio Value ($)"},
                              color_discrete_sequence=["steelblue"])
                fig.update_layout(hovermode="x unified", showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            elif charts.get("equity_curve") and charts["equity_curve"].exists():
                st.image(str(charts["equity_curve"]), use_container_width=True)
            else:
                st.info("No equity curve data available.")

        with tabs[1]:
            if has_equity_csv:
                eq_df = load_equity_data(str(files["equity_data"]))
                peak = eq_df["equity"].cummax()
                drawdown_pct = (eq_df["equity"] - peak) / peak * 100
                dd_df = pd.DataFrame({"drawdown": drawdown_pct}, index=eq_df.index)
                fig = px.area(dd_df, y="drawdown", title="Drawdown",
                              labels={"timestamp": "", "drawdown": "Drawdown (%)"},
                              color_discrete_sequence=["salmon"])
                fig.update_layout(hovermode="x unified", showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            elif charts.get("drawdown") and charts["drawdown"].exists():
                st.image(str(charts["drawdown"]), use_container_width=True)
            else:
                st.info("No drawdown data available.")

        with tabs[2]:
            if has_price_csv and files.get("tradelog") and files["tradelog"].exists():
                price_df = load_price_data(str(files["price_data"]))
                trades_df = load_tradelog(str(files["tradelog"]))
                fig = px.line(price_df, y="close", title="Signals on Price",
                              labels={"timestamp": "", "close": "Price ($)"},
                              color_discrete_sequence=["steelblue"])
                fig.update_layout(hovermode="x unified", showlegend=True)
                # Overlay entry/exit markers from trade log
                if not trades_df.empty and "entry_time" in trades_df.columns:
                    if "direction" in trades_df.columns:
                        for direction, color, symbol, name in [
                            (1, "green", "triangle-up", "Long Entry"),
                            (-1, "red", "triangle-down", "Short Entry"),
                        ]:
                            entries = trades_df[trades_df["direction"] == direction]
                            if not entries.empty and "entry_price" in entries.columns:
                                fig.add_scatter(
                                    x=entries["entry_time"], y=entries["entry_price"],
                                    mode="markers", name=name,
                                    marker=dict(color=color, symbol=symbol, size=10),
                                )
                    else:
                        # No direction column — plot all entries as neutral markers
                        if "entry_price" in trades_df.columns:
                            fig.add_scatter(
                                x=trades_df["entry_time"], y=trades_df["entry_price"],
                                mode="markers", name="Entry",
                                marker=dict(color="blue", symbol="circle", size=10),
                            )
                    # Exit markers
                    if "exit_time" in trades_df.columns and "exit_price" in trades_df.columns:
                        exits = trades_df.dropna(subset=["exit_time", "exit_price"])
                        if not exits.empty:
                            fig.add_scatter(
                                x=exits["exit_time"], y=exits["exit_price"],
                                mode="markers", name="Exit",
                                marker=dict(color="black", symbol="x", size=8),
                            )
                st.plotly_chart(fig, use_container_width=True)
            elif charts.get("signals") and charts["signals"].exists():
                st.image(str(charts["signals"]), use_container_width=True)
            else:
                st.info("No signals data available.")

    # Config snapshot
    if files["config"] and files["config"].exists():
        with st.expander("Strategy Config (YAML snapshot at run time)"):
            config = load_config_yaml(str(files["config"]))
            st.code(yaml.dump(config, default_flow_style=False, sort_keys=False), language="yaml")
    elif report["config_table"]:
        with st.expander("Strategy Config (from report)"):
            config_df = pd.DataFrame(
                list(report["config_table"].items()),
                columns=["Parameter", "Value"],
            )
            st.dataframe(config_df, hide_index=True, use_container_width=True)

    # Full report
    with st.expander("Full Report (Markdown)"):
        st.markdown(report["raw"])


# ---------------------------------------------------------------------------
# View 2: Trade Explorer
# ---------------------------------------------------------------------------

def view_trade_explorer(run: dict):
    st.header(f"Trade Explorer — {run['source_label']} ({run['date_display']})")

    # Merge trade logs from all mode/timeframe subdirs
    frames = []
    for mode_name, tfs in run["modes"].items():
        for tf_name, files in tfs.items():
            if files.get("tradelog") and files["tradelog"].exists():
                tl = load_tradelog(str(files["tradelog"]))
                if not tl.empty:
                    frames.append(tl)

    if not frames:
        st.info("No trade log CSV found for this run.")
        return

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        st.info("Trade log is empty (no trades).")
        return

    # Show data range from first available report
    for mode_name, tfs in run["modes"].items():
        for tf_name, files in tfs.items():
            dr = _get_data_range_from_files(files)
            if dr:
                st.caption(dr)
                break
        else:
            continue
        break

    # Filters
    filter_cols = st.columns(3)
    filtered = df.copy()

    if "direction" in df.columns:
        directions = df["direction"].dropna().unique().tolist()
        sel_dir = filter_cols[0].multiselect("Direction", directions, default=directions)
        filtered = filtered[filtered["direction"].isin(sel_dir)]

    if "exit_reason" in df.columns:
        reasons = df["exit_reason"].dropna().unique().tolist()
        sel_reason = filter_cols[1].multiselect("Exit Reason", reasons, default=reasons)
        filtered = filtered[filtered["exit_reason"].isin(sel_reason)]

    if "trade_mode" in df.columns:
        modes = sorted(df["trade_mode"].dropna().unique().tolist())
        if len(modes) > 1:
            sel_mode = filter_cols[2].radio("Trade Mode", modes, horizontal=True)
            filtered = filtered[filtered["trade_mode"] == sel_mode]

    # Summary stats
    if not filtered.empty and "pnl" in filtered.columns:
        wins = filtered[filtered["pnl"] > 0]
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Filtered Trades", len(filtered))
        s2.metric("Win Rate", f"{len(wins) / len(filtered) * 100:.1f}%" if len(filtered) > 0 else "—")
        s3.metric("Avg P&L", f"${filtered['pnl'].mean():,.2f}")
        s4.metric("Best / Worst", f"${filtered['pnl'].max():,.2f} / ${filtered['pnl'].min():,.2f}")

    # Options detail section — show contract info when options trades present
    has_options = "option_type" in filtered.columns and filtered["option_type"].notna().any()
    if has_options:
        opts = filtered[filtered["option_type"].notna()]
        st.subheader("Options Contracts")
        oc1, oc2, oc3, oc4 = st.columns(4)
        calls = opts[opts["option_type"] == "C"]
        puts = opts[opts["option_type"] == "P"]
        oc1.metric("Calls / Puts", f"{len(calls)} / {len(puts)}")
        if "strike" in opts.columns:
            oc2.metric("Strike Range", f"${opts['strike'].min():.0f} – ${opts['strike'].max():.0f}")
        if "delta" in opts.columns and opts["delta"].notna().any():
            oc3.metric("Avg |Delta| at Entry", f"{opts['delta'].abs().mean():.3f}")
        if "expiry" in opts.columns and opts["expiry"].notna().any():
            # Compute DTE from entry_time to expiry
            expiries = pd.to_datetime(opts["expiry"])
            entries = pd.to_datetime(opts["entry_time"])
            dtes = (expiries - entries).dt.days
            oc4.metric("Avg DTE at Entry", f"{dtes.mean():.1f}d")

        # Per-contract breakdown
        with st.expander("Contract Breakdown"):
            contract_cols = ["entry_time", "direction", "option_type", "strike", "expiry",
                             "entry_price", "exit_price", "pnl", "pnl_pct",
                             "delta", "gamma", "theta", "vega", "exit_reason"]
            avail = [c for c in contract_cols if c in opts.columns]
            contract_df = opts[avail].copy()
            if "expiry" in contract_df.columns:
                contract_df["expiry"] = pd.to_datetime(contract_df["expiry"]).dt.strftime("%Y-%m-%d")
            st.dataframe(contract_df, hide_index=True, use_container_width=True)

    # Table — hide all-NaN columns (options columns when equities-only)
    display_df = filtered.dropna(axis=1, how="all")

    column_config = {}
    if "pnl" in display_df.columns:
        column_config["pnl"] = st.column_config.NumberColumn("P&L ($)", format="$%.2f")
    if "pnl_pct" in display_df.columns:
        column_config["pnl_pct"] = st.column_config.NumberColumn("P&L %", format="%.2f%%")
    if "entry_price" in display_df.columns:
        column_config["entry_price"] = st.column_config.NumberColumn("Entry $", format="$%.2f")
    if "exit_price" in display_df.columns:
        column_config["exit_price"] = st.column_config.NumberColumn("Exit $", format="$%.2f")
    if "strike" in display_df.columns:
        column_config["strike"] = st.column_config.NumberColumn("Strike", format="$%.0f")
    if "delta" in display_df.columns:
        column_config["delta"] = st.column_config.NumberColumn("Delta", format="%.4f")
    if "gamma" in display_df.columns:
        column_config["gamma"] = st.column_config.NumberColumn("Gamma", format="%.4f")
    if "theta" in display_df.columns:
        column_config["theta"] = st.column_config.NumberColumn("Theta", format="%.4f")
    if "vega" in display_df.columns:
        column_config["vega"] = st.column_config.NumberColumn("Vega", format="%.4f")

    st.dataframe(display_df, column_config=column_config, hide_index=True, use_container_width=True)

    # Charts
    if "pnl" in filtered.columns and len(filtered) > 0:
        chart_cols = st.columns(2)

        with chart_cols[0]:
            color_col = "direction" if "direction" in filtered.columns else None
            fig = px.histogram(
                filtered, x="pnl", color=color_col,
                title="P&L Distribution",
                labels={"pnl": "P&L ($)"},
                nbins=min(30, max(5, len(filtered) // 2)),
            )
            st.plotly_chart(fig, use_container_width=True)

        with chart_cols[1]:
            cum_pnl = filtered["pnl"].cumsum().reset_index(drop=True)
            fig = px.line(
                x=range(1, len(cum_pnl) + 1), y=cum_pnl,
                title="Cumulative P&L",
                labels={"x": "Trade #", "y": "Cumulative P&L ($)"},
            )
            st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# View 3: Comparison
# ---------------------------------------------------------------------------

def _extract_comparison_date_range(text: str) -> str:
    """Extract date range from comparison MD, e.g. '(2025-11-10 to 2026-02-13)'."""
    m = re.search(r"\((\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\)", text)
    if not m:
        return ""
    try:
        start = pd.Timestamp(m.group(1)).strftime("%b %d, %Y")
        end = pd.Timestamp(m.group(2)).strftime("%b %d, %Y")
        return f"{start} — {end}"
    except ValueError:
        return f"{m.group(1)} to {m.group(2)}"


def view_comparison(run: dict):
    st.header("Armed Mode Comparison")

    # Look for comparison files
    comparison_paths = [
        RESULTS_DIR / "others" / "armed_mode_comparison.md",
        RESULTS_DIR / "armed_mode_comparison.md",
    ]

    comp_path = None
    for p in comparison_paths:
        if p.exists():
            comp_path = p
            break

    if not comp_path:
        st.info(
            "No `armed_mode_comparison.md` found. "
            "Run `python scripts_py/armed_mode_comparison.py` to generate one."
        )
        return

    text = comp_path.read_text()
    date_range = _extract_comparison_date_range(text)
    if date_range:
        st.caption(f"Backtest period: {date_range}")

    sections = parse_comparison_md(str(comp_path))
    if not sections:
        st.warning("Could not parse comparison tables from the file.")
        with st.expander("Raw file"):
            st.markdown(text)
        return

    for section in sections:
        st.subheader(f"{section['source']} Data")
        st.dataframe(section["df"], use_container_width=True)

    # Key findings — display the text after the tables
    findings_match = re.search(r"(## Key Findings.*)", text, re.DOTALL)
    if findings_match:
        st.markdown("---")
        st.markdown(findings_match.group(1))


# ---------------------------------------------------------------------------
# View 4: Cross-Run
# ---------------------------------------------------------------------------

@st.cache_data
def collect_all_metrics() -> pd.DataFrame:
    """Collect metrics from all report files across all sources and dates."""
    runs = discover_runs()
    rows = []
    for run in runs:
        for mode_name, tfs in run["modes"].items():
            for tf_name, files in tfs.items():
                if not files.get("report") or not files["report"].exists():
                    continue
                report = parse_report_md(str(files["report"]))
                # Build human-readable data range from metadata
                raw_range = report["metadata"].get("Data Range", "")
                data_range = ""
                if raw_range:
                    parts = raw_range.split(" to ")
                    try:
                        s = pd.Timestamp(parts[0]).strftime("%b %d, %Y")
                        e = pd.Timestamp(parts[1]).strftime("%b %d, %Y")
                        data_range = f"{s} — {e}"
                    except (IndexError, ValueError):
                        data_range = raw_range
                row = {
                    "Run Date": run["date_display"],
                    "Source": run["source_label"],
                    "Mode": mode_name,
                    "Data Range": data_range,
                }
                for m in report["metrics"].values():
                    if m["value"] is not None:
                        row[m["label"]] = m["value"]
                rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def view_cross_run():
    st.header("Cross-Run Comparison")

    df = collect_all_metrics()
    if df.empty:
        st.info("No report files found across runs.")
        return

    # Metric columns (exclude identifying columns)
    id_cols = ("Run Date", "Source", "Mode", "Data Range")
    metric_cols = [c for c in df.columns if c not in id_cols]
    if not metric_cols:
        st.info("No numeric metrics found in reports.")
        return

    selected_metric = st.selectbox("Metric", metric_cols, index=0)

    plot_df = df[["Run Date", "Source", "Mode", "Data Range", selected_metric]].dropna(subset=[selected_metric])
    if plot_df.empty:
        st.info(f"No data for {selected_metric}")
        return

    # Color by Source + Mode combination
    plot_df["Label"] = plot_df["Source"] + " — " + plot_df["Mode"]

    fig = px.bar(
        plot_df.sort_values("Run Date"),
        x="Run Date",
        y=selected_metric,
        color="Label",
        barmode="group",
        title=f"{selected_metric} by Run Date",
        hover_data=["Data Range"],
    )
    st.plotly_chart(fig, use_container_width=True)

    # Summary table
    st.subheader("All Runs")
    display = df[list(id_cols) + metric_cols].sort_values(
        ["Run Date", "Source", "Mode"], ascending=[False, True, True],
    )
    st.dataframe(display, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="Backtest Dashboard", layout="wide")
    run, page = sidebar()

    if run is None:
        st.title("Backtest Dashboard")
        st.info(
            "No results found. Run a backtest first:\n\n"
            "```bash\n./run_backtest_db.sh\n```"
        )
        return

    if page == "Overview":
        view_overview(run)
    elif page == "Trade Explorer":
        view_trade_explorer(run)
    elif page == "Comparison":
        view_comparison(run)
    elif page == "Cross-Run":
        view_cross_run()


if __name__ == "__main__":
    main()
