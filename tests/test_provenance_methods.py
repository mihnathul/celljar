"""Provenance-method invariants - the *_method fields that document how each
derived quantity was determined.

Three method fields are checked:

- soh_method on test_metadata          -> {capacity_vs_first_checkpoint, bol_assumption, null}
- soc_method on test_metadata          -> {coulomb_count, protocol_asserted, source_published, null}
- resistance_method on cycle_summary   -> {source_published, null}

These tests use the locally-harmonized bundle when present (set CELLJAR_LOCAL=1
+ run examples/demo_end_to_end.py first). When the bundle is absent the tests
are skipped, mirroring the rest of the test suite's "skip-if-no-raw-data"
convention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HARMONIZED = Path(__file__).parent.parent / "data" / "harmonized"
CELLS_DIR = HARMONIZED / "cells"
TESTS_DIR = HARMONIZED / "tests"
CYCLE_SUMMARY = HARMONIZED / "cycle_summary.parquet"


SOH_METHOD_ALLOWED = {"capacity_vs_first_checkpoint", "bol_assumption", None}
SOC_METHOD_ALLOWED = {"protocol_asserted", "source_published", None}
RESISTANCE_METHOD_ALLOWED = {"source_published", None}


@pytest.fixture(scope="module")
def loaded_tests() -> list[dict]:
    if not TESTS_DIR.exists() or not any(TESTS_DIR.glob("*.json")):
        pytest.skip("No harmonized tests/*.json - run demo_end_to_end.py first")
    return [json.loads(p.read_text()) for p in TESTS_DIR.glob("*.json")]


@pytest.fixture(scope="module")
def cycle_summary_df():
    if not CYCLE_SUMMARY.exists():
        pytest.skip("No cycle_summary.parquet - run demo_end_to_end.py first")
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pytest.skip("pyarrow not installed")
    return pq.read_table(CYCLE_SUMMARY).to_pandas()


# ---------------------------------------------------------------------------
# soh_method
# ---------------------------------------------------------------------------

def test_soh_method_values_are_in_allowed_set(loaded_tests: list[dict]) -> None:
    bad = [(t["test_id"], t.get("soh_method")) for t in loaded_tests
           if t.get("soh_method") not in SOH_METHOD_ALLOWED]
    assert not bad, f"unrecognised soh_method values: {bad[:5]}"


def test_soh_method_null_iff_soh_pct_null(loaded_tests: list[dict]) -> None:
    """Both go together: either both null, or both populated."""
    mismatched = [
        t["test_id"] for t in loaded_tests
        if (t.get("soh_pct") is None) != (t.get("soh_method") is None)
    ]
    assert not mismatched, f"soh_pct / soh_method nullness diverges: {mismatched[:5]}"


# ---------------------------------------------------------------------------
# soc_method
# ---------------------------------------------------------------------------

def test_soc_method_values_are_in_allowed_set(loaded_tests: list[dict]) -> None:
    bad = [(t["test_id"], t.get("soc_method")) for t in loaded_tests
           if t.get("soc_method") not in SOC_METHOD_ALLOWED]
    assert not bad, f"unrecognised soc_method values: {bad[:5]}"


def test_soc_method_populated_when_soc_range_present(loaded_tests: list[dict]) -> None:
    """If soc_range_min/max are non-null, soc_method must say where they came from."""
    bad = [
        t["test_id"] for t in loaded_tests
        if (t.get("soc_range_min") is not None or t.get("soc_range_max") is not None)
        and t.get("soc_method") is None
    ]
    assert not bad, f"soc_range_* populated but soc_method null: {bad[:5]}"


# ---------------------------------------------------------------------------
# resistance_method
# ---------------------------------------------------------------------------

def test_resistance_method_values_are_in_allowed_set(cycle_summary_df) -> None:
    if "resistance_method" not in cycle_summary_df.columns:
        pytest.skip("cycle_summary has no resistance_method column")
    bad = (
        cycle_summary_df.loc[
            ~cycle_summary_df["resistance_method"].isin(
                [v for v in RESISTANCE_METHOD_ALLOWED if v is not None]
            )
            & cycle_summary_df["resistance_method"].notna()
        ]
    )
    assert bad.empty, f"unrecognised resistance_method values: {bad['resistance_method'].unique().tolist()[:5]}"


def test_resistance_method_populated_when_r_dc_present(cycle_summary_df) -> None:
    if "resistance_method" not in cycle_summary_df.columns:
        pytest.skip("cycle_summary has no resistance_method column")
    bad = cycle_summary_df[
        cycle_summary_df["resistance_dc_ohm"].notna()
        & cycle_summary_df["resistance_method"].isna()
    ]
    assert bad.empty, (
        f"{len(bad)} rows have resistance_dc_ohm set but resistance_method is null"
    )
