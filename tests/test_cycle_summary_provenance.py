"""Invariant: cycle_summary contains ONLY source-published rows.

celljar is a harmonization layer, not an extraction layer. cycle_summary
rows must come from a source originator's own published per-cycle data:

- NAUMANN     - xlsx summary table (the source ships no V/I/T at all)
- NASA_PCOE   - per-discharge Capacity_Ah scalar attached to each .mat record
- KOLLMEYER   - Borealis xlsx summary (30T_AGING) alongside raw zips

Sources that ship V/I/T but no source-authored per-cycle summary (MATR, CLO,
BILLS, ORNL, HNEI, etc.) MUST NOT have cycle_summary rows. If this test
ever fails, somebody re-introduced V/I/T derivation - which should live in
downstream tools, not celljar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

HARMONIZED = Path(__file__).parent.parent / "data" / "harmonized"
CYCLE_SUMMARY = HARMONIZED / "cycle_summary.parquet"


ALLOWED_SOURCE_PREFIXES = ("NAUMANN", "NASA_PCOE", "KOLLMEYER_30T_AGING")


@pytest.fixture(scope="module")
def cycle_summary_df():
    if not CYCLE_SUMMARY.exists():
        pytest.skip("No cycle_summary.parquet - run demo_end_to_end.py first")
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pytest.skip("pyarrow not installed")
    return pq.read_table(CYCLE_SUMMARY).to_pandas()


def test_cycle_summary_sources_are_source_published(cycle_summary_df) -> None:
    """Every cycle_summary row must originate from a source that publishes
    per-cycle aggregates itself (no V/I/T-derived rows)."""
    bad_rows = cycle_summary_df[
        ~cycle_summary_df["cell_id"].str.startswith(ALLOWED_SOURCE_PREFIXES)
    ]
    bad_sources = bad_rows["cell_id"].str.split("_").str[0].unique().tolist()
    assert bad_rows.empty, (
        f"cycle_summary has {len(bad_rows)} rows from non-source-published "
        f"sources: {bad_sources}. celljar must not derive cycle_summary "
        "from V/I/T."
    )


def test_nasa_cycle_summary_populated(cycle_summary_df) -> None:
    """NASA's harmonizer used to have a scaffold-no-append bug. Guard against
    a regression: NASA_PCOE should contribute non-zero rows."""
    nasa = cycle_summary_df[cycle_summary_df["cell_id"].str.startswith("NASA_PCOE")]
    assert len(nasa) > 0, (
        "NASA_PCOE has no cycle_summary rows - the scaffold-no-append bug "
        "may have regressed."
    )


def test_naumann_publishes_r_dc(cycle_summary_df) -> None:
    """Naumann is the only source that publishes R_DC alongside capacity.
    Their rows should have resistance_dc_ohm populated."""
    nau = cycle_summary_df[cycle_summary_df["cell_id"].str.startswith("NAUMANN")]
    if nau.empty:
        pytest.skip("No NAUMANN cycle_summary rows - source data missing")
    assert nau["resistance_dc_ohm"].notna().any(), (
        "NAUMANN cycle_summary rows have no resistance_dc_ohm - the source "
        "publishes R_DC vs FEC; if all rows are null the xlsx parse may have "
        "regressed."
    )


def test_kollmeyer_aging_xlsx_summary_loaded(cycle_summary_df) -> None:
    """KOLLMEYER 30T_AGING ships a Borealis xlsx summary table with capacity
    + SOH per check-up for all 6 cells - guard against parse regressions."""
    aging = cycle_summary_df[
        cycle_summary_df["cell_id"].str.startswith("KOLLMEYER_30T_AGING")
    ]
    if aging.empty:
        pytest.skip("No KOLLMEYER_30T_AGING cycle_summary rows - raw xlsx missing")
    n_cells = aging["cell_id"].nunique()
    assert n_cells == 6, (
        f"Expected 6 KOLLMEYER_30T_AGING cells in cycle_summary, got {n_cells}. "
        "The Borealis xlsx summary parser may have regressed."
    )
