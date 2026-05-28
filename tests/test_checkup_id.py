"""Tests for checkup_id - the optional grouping FK that links test segments
belonging to one Reference Performance Test (RPT) block.

Currently used by KOLLMEYER 30T_AGING; null for sources without RPT structure.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

HARMONIZED = Path(__file__).parent.parent / "data" / "harmonized"
TESTS_DIR = HARMONIZED / "tests"


@pytest.fixture(scope="module")
def aging_tests() -> list[dict]:
    if not TESTS_DIR.exists():
        pytest.skip("No tests/*.json - run demo_end_to_end.py first")
    aging = [json.loads(p.read_text()) for p in TESTS_DIR.glob("KOLLMEYER_30T_AGING_*.json")]
    if not aging:
        pytest.skip("No KOLLMEYER_30T_AGING tests harmonized - need raw AGING zips")
    return aging


def test_aging_characterization_tests_have_checkup_id(aging_tests: list[dict]) -> None:
    """Every per-segment characterization test (test_type != cycle_aging) must have a
    populated checkup_id. The 'life summary' test_type=cycle_aging row is exempt
    because it represents the whole cell life, not a single RPT block."""
    missing = [
        t["test_id"] for t in aging_tests
        if t.get("test_type") != "cycle_aging" and not t.get("checkup_id")
    ]
    assert not missing, f"Aging characterization tests missing checkup_id: {missing[:5]}"


def test_aging_life_summary_has_null_checkup_id(aging_tests: list[dict]) -> None:
    """The per-cell life summary represents the whole life, not a single RPT
    block - its checkup_id MUST be null."""
    bad = [
        t["test_id"] for t in aging_tests
        if t.get("test_type") == "cycle_aging" and t.get("checkup_id") is not None
    ]
    assert not bad, f"Life-summary tests have non-null checkup_id (should be null): {bad}"


def test_checkup_id_groups_have_multiple_segments(aging_tests: list[dict]) -> None:
    """Each checkup_id should group at least 2 segments (a real RPT block has 5-7).
    If we see checkup_ids with only 1 segment, the grouping is broken."""
    groups: dict[str, list[str]] = defaultdict(list)
    for t in aging_tests:
        cid = t.get("checkup_id")
        if cid is not None:
            groups[cid].append(t["test_type"])
    singleton = [cid for cid, members in groups.items() if len(members) < 2]
    assert not singleton, (
        f"{len(singleton)} checkup_id groups have <2 segments: {singleton[:5]}"
    )


def test_non_aging_sources_have_null_checkup_id() -> None:
    """checkup_id is currently only meaningful for KOLLMEYER 30T_AGING.
    All other sources should leave it null."""
    if not TESTS_DIR.exists():
        pytest.skip("No tests/*.json - run demo_end_to_end.py first")
    bad: list[str] = []
    for p in TESTS_DIR.glob("*.json"):
        t = json.loads(p.read_text())
        if t["cell_id"].startswith("KOLLMEYER_30T_AGING_"):
            continue
        if t.get("checkup_id") is not None:
            bad.append(t["test_id"])
    assert not bad, f"Non-AGING tests unexpectedly have checkup_id set: {bad[:5]}"
