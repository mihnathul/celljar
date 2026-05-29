"""Tests for the HuggingFace dataset card generator.

Architecture (post-refactor): the README is the single source of truth. The HF
card is the README with the repo-only sections (marked <!-- CARD:SKIP -->)
removed, plus YAML frontmatter. The datasets table and contents line are
generated from the bundle by celljar.bundle and injected into both. These tests
pin the invariants so card/README/schema can't silently drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add examples/ to path so we can import publish_to_huggingface.
sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

# Skip gracefully if huggingface_hub isn't installed (e.g. minimal CI).
pytest.importorskip("huggingface_hub")
from publish_to_huggingface import (  # noqa: E402
    HARMONIZED,
    README,
    build_dataset_card,
    build_frontmatter,
    _readme_to_card_body,
)
from celljar.bundle import collect_datasets, render_dataset_table, sync_readme_text  # noqa: E402


def test_frontmatter_is_valid_yaml():
    """The frontmatter block must start with --- and end with ---."""
    front = build_frontmatter({"ORNL": {}, "HNEI": {}})
    assert front.startswith("---")
    assert front.endswith("---")
    # Sources are listed as lowercase tags.
    assert "  - ornl" in front
    assert "  - hnei" in front


def test_readme_to_card_body_strips_skip_and_comments():
    """CARD:SKIP regions, HTML-comment markers, and blank-run collapse."""
    sample = (
        "# t\n\n"
        "<!-- CARD:SKIP:START -->\nrepo-only\n<!-- CARD:SKIP:END -->\n\n"
        "keep me\n\n"
        "inline<!-- CARD:SKIP:START --> dropped<!-- CARD:SKIP:END --> tail\n"
    )
    body = _readme_to_card_body(sample)
    assert "repo-only" not in body
    assert "dropped" not in body
    assert "<!--" not in body
    assert "keep me" in body
    assert "inline tail" in body
    assert "\n\n\n" not in body  # blank runs collapsed


def test_dataset_table_lists_every_active_dataset():
    """Each dataset discovered in the bundle gets a labelled row + the header.

    data/harmonized/ is generated and not tracked, so skip when it's absent -
    mirroring the rest of the suite's skip-if-no-bundle pattern.
    """
    datasets = collect_datasets(HARMONIZED)
    if not datasets:
        pytest.skip("No harmonized bundle - run demo_end_to_end.py first")
    table = render_dataset_table(datasets)
    assert "| Dataset | Cell model | Chemistry | Test types | Cells | License | DOI |" in table
    for d in datasets:
        assert d["label"] in table, f"dataset {d['label']!r} missing from table"


def test_card_is_readme_minus_skip_plus_frontmatter():
    """The card body must equal the (freshly-synced) README with skips removed."""
    card = build_dataset_card()
    body = card.split("---", 2)[2].strip()
    expected = _readme_to_card_body(sync_readme_text(README.read_text(), HARMONIZED)).strip()
    assert body == expected


def test_card_is_clean_and_complete():
    """End-to-end smoke: frontmatter present, no leaked markers/badges/repo-only
    sections, and the data-consumer sections survive."""
    card = build_dataset_card()
    assert card.startswith("---")                 # frontmatter
    assert "celljar" in card.lower()
    # No leakage of repo-only / template artifacts.
    assert "<!--" not in card
    assert "CARD:SKIP" not in card
    assert "shields.io" not in card
    assert "pypi" not in card.lower()
    assert "## Develop locally" not in card
    assert "## Contributing" not in card
    # Consumer-facing sections are present.
    assert "## Datasets" in card
    assert "## Query in place" in card
    # All four schema entities are still named (sourced from the README schema block).
    for entity in ("cell_metadata", "test_metadata", "timeseries", "cycle_summary"):
        assert entity in card
