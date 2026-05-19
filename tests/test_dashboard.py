"""Tests for dashboard discover_runs() folder parsing logic."""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_discover_runs():
    """Import the pure logic of discover_runs, bypassing @st.cache_data."""
    import importlib
    import types

    # Patch streamlit so the module loads without a running Streamlit server
    fake_st = types.ModuleType("streamlit")
    fake_st.cache_data = lambda fn: fn  # strip the decorator
    sys.modules.setdefault("streamlit", fake_st)

    # Re-import dashboard with the stub in place
    if "scripts_py.dashboard" in sys.modules:
        del sys.modules["scripts_py.dashboard"]

    import scripts_py.dashboard as dash
    return dash.discover_runs


# ---------------------------------------------------------------------------
# Folder name patterns that discover_runs() must accept
# ---------------------------------------------------------------------------

VALID_FOLDER_NAMES = [
    # New layout — no run suffix (base_runner.py current format)
    "February-10-2026_to_February-13-2026_smi-then-wr",
    # New layout — EMA tag (as observed in results/db/)
    "January-02-2026_to_April-02-2026_EMA233_OFF2_1OTM",
    # Legacy with good_ prefix (observed in results/tv/)
    "good_December-08-2025_to_March-13-2026_run-March-13-2026",
    # Legacy compound with run suffix (observed in results/tv/)
    "December-15-2025_to_March-20-2026_run-March-21-2026_WR_AR1_VP1_SYN20_1OTM",
    # Old single-date legacy (observed in results/alpaca/, results/tv/)
    "February-16-2026",
]


def _make_mock_run_tree(tmp_path: Path, folder_name: str, under_date: bool = True) -> Path:
    """Create a minimal valid run folder tree and return results root."""
    results = tmp_path / "results"
    src_dir = results / "db"

    if under_date:
        base = src_dir / "2026-03-22" / folder_name
    else:
        base = src_dir / folder_name

    run_dir = base / "equities" / "5min"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# Report\n")
    return results


def test_discover_runs_finds_new_layout_folder(tmp_path):
    """discover_runs() must find folders in the new YYYY-MM-DD/compound layout."""
    folder_name = "February-10-2026_to_February-13-2026_smi-then-wr"
    results = _make_mock_run_tree(tmp_path, folder_name, under_date=True)

    discover_runs = _get_discover_runs()
    with patch("scripts_py.dashboard.RESULTS_DIR", results):
        runs = discover_runs()

    assert len(runs) > 0, (
        f"discover_runs() returned no runs for new-layout folder {folder_name!r}"
    )
    assert runs[0]["date_folder"] == folder_name


def test_discover_runs_finds_good_prefix_folder(tmp_path):
    """discover_runs() must find legacy folders that start with 'good_'."""
    folder_name = "good_December-08-2025_to_March-13-2026_run-March-13-2026"
    results = _make_mock_run_tree(tmp_path, folder_name, under_date=False)

    discover_runs = _get_discover_runs()
    with patch("scripts_py.dashboard.RESULTS_DIR", results):
        runs = discover_runs()

    assert len(runs) > 0, (
        f"discover_runs() returned no runs for 'good_' prefix folder {folder_name!r}"
    )


def test_discover_runs_finds_all_valid_patterns(tmp_path):
    """discover_runs() must find runs for every naming pattern observed in production."""
    results = tmp_path / "results"
    src_dir = results / "db"

    for i, folder_name in enumerate(VALID_FOLDER_NAMES):
        # Mix new (YYYY-MM-DD parent) and legacy (directly under src) layouts
        if "_to_" in folder_name and not folder_name.startswith("good_"):
            base = src_dir / f"2026-0{i + 1}-01" / folder_name
        else:
            base = src_dir / folder_name
        run_dir = base / "equities" / "5min"
        run_dir.mkdir(parents=True)
        (run_dir / "report.md").write_text("# Report\n")

    discover_runs = _get_discover_runs()
    with patch("scripts_py.dashboard.RESULTS_DIR", results):
        runs = discover_runs()

    found_names = {r["date_folder"] for r in runs}
    missing = [n for n in VALID_FOLDER_NAMES if n not in found_names]
    assert not missing, f"discover_runs() silently skipped these folders: {missing}"
