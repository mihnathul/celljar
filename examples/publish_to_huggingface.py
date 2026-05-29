"""Publish harmonized celljar output to HuggingFace Datasets.

Run after `python examples/demo_end_to_end.py` to push the harmonized
bundle (cells/, tests/, timeseries.parquet) to:
    https://huggingface.co/datasets/mihnathul/celljar

Requires:
    pip install huggingface_hub
    huggingface-cli login   # one-time, requires write token
"""

from pathlib import Path
import argparse
import json
import re
import sys

from huggingface_hub import HfApi, create_repo

ROOT = Path(__file__).parent.parent
HARMONIZED = ROOT / "data" / "harmonized"
README = ROOT / "README.md"
HF_REPO = "mihnathul/celljar"
GH_REPO = "https://github.com/mihnathul/celljar"


def verify_auth() -> str:
    """Return HF username, or exit with helpful error."""
    api = HfApi()
    try:
        info = api.whoami()
        return info["name"]
    except Exception:
        sys.exit(
            "Not authenticated with HuggingFace.\n"
            "Run: huggingface-cli login\n"
            "(You'll need a write-scoped token from https://huggingface.co/settings/tokens)"
        )


# Sources whose license does not permit redistribution by celljar.
# The publisher refuses to upload the harmonized bundle if any cell from
# one of these sources is present under data/harmonized/cells/.
# (Empty for v0.3 - all current sources have permissive licenses.)
_NO_REDISTRIBUTE_SOURCES: set[str] = set()


def verify_data() -> dict:
    """Confirm harmonized output exists. Return summary stats."""
    parquet = HARMONIZED / "timeseries.parquet"
    if not parquet.exists():
        sys.exit(
            f"No timeseries.parquet found at {parquet}.\n"
            "Run examples/demo_end_to_end.py first to produce the harmonized bundle."
        )
    cells_dir = HARMONIZED / "cells"
    tests_dir = HARMONIZED / "tests"
    if not cells_dir.exists() or not tests_dir.exists():
        sys.exit(
            f"Missing cells/ or tests/ under {HARMONIZED}.\n"
            "Run examples/demo_end_to_end.py first."
        )
    cells = list(cells_dir.glob("*.json"))
    tests = list(tests_dir.glob("*.json"))
    if not cells or not tests:
        sys.exit("cells/ or tests/ is empty. Run examples/demo_end_to_end.py first.")

    # Refuse to upload sources whose license doesn't permit redistribution.
    blocked: list[str] = []
    for p in cells:
        try:
            with open(p) as f:
                c = json.load(f)
        except Exception:
            continue
        if c.get("source") in _NO_REDISTRIBUTE_SOURCES:
            blocked.append(f"{c.get('source')}: {c.get('cell_id')}")
    if blocked:
        sys.exit(
            "Refusing to publish - the harmonized bundle contains cells from "
            "sources celljar does not redistribute:\n  "
            + "\n  ".join(blocked)
            + "\nRemove these cells/tests from data/harmonized/ before publishing. "
            "See data/raw/calce/SOURCE_DATA_PROVENANCE.md."
        )

    # Total bytes across the bundle (for the summary at the end)
    total_bytes = parquet.stat().st_size
    for p in cells + tests:
        total_bytes += p.stat().st_size
    return {
        "cells": len(cells),
        "tests": len(tests),
        "parquet_bytes": parquet.stat().st_size,
        "total_bytes": total_bytes,
    }


from celljar.bundle import (   # noqa: E402
    collect_sources as _collect_sources_impl,
    timeseries_row_count as _timeseries_row_count_impl,
    collect_datasets as _collect_datasets_impl,
    sync_readme_text,
)


def _collect_sources() -> dict:
    return _collect_sources_impl(HARMONIZED)


def _timeseries_row_count() -> int:
    return _timeseries_row_count_impl(HARMONIZED)


def build_frontmatter(sources: dict) -> str:
    """HF dataset card YAML frontmatter (license, tags, source list)."""
    tags = [
        "battery", "lithium-ion", "energy-storage", "timeseries",
        "electrochemistry", "bms", "hppc", "cycling",
    ]
    tag_lines = "\n".join(f"  - {t}" for t in tags)
    source_tag_lines = "\n".join(f"  - {s.lower()}" for s in sorted(sources))
    return f"""---
license: cc-by-4.0
language:
  - en
pretty_name: celljar
tags:
{tag_lines}
size_categories:
  - 10K<n<100M
task_categories:
  - time-series-forecasting
  - tabular-regression
source_datasets:
{source_tag_lines if source_tag_lines else "  - original"}
---"""


_CARD_SKIP = re.compile(r"<!-- CARD:SKIP:START -->.*?<!-- CARD:SKIP:END -->", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")


def _readme_to_card_body(readme: str) -> str:
    """The HF card body is the README with the repo-only sections removed.

    Strips every <!-- CARD:SKIP:START -->...<!-- CARD:SKIP:END --> region
    (badges, Develop locally, Contributing), drops the now-empty HTML-comment
    markers (incl. the generated-region markers), and collapses the blank-line
    runs they leave behind.
    """
    body = _CARD_SKIP.sub("", readme)
    body = _HTML_COMMENT.sub("", body)
    body = _BLANK_RUN.sub("\n\n", body)
    return body.strip()


def build_dataset_card() -> str:
    """Compose the HF dataset card: YAML frontmatter + the README body with the
    repo-only sections stripped. The README is the single source of truth; its
    generated regions (datasets table, contents line) are refreshed in memory
    here so the card is current even if README.md on disk is stale.
    """
    sources = _collect_sources()
    active = {d["source"] for d in _collect_datasets_impl(HARMONIZED)}
    sources = {s: v for s, v in sources.items() if s in active}

    readme = sync_readme_text(README.read_text(), HARMONIZED)
    return f"{build_frontmatter(sources)}\n\n{_readme_to_card_body(readme)}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--message",
        default="Update harmonized bundle",
        help="Commit message for the HF dataset push.",
    )
    parser.add_argument(
        "--revision",
        default=None,
        help="Optional tag to create on the dataset after upload (e.g. v0.3.0).",
    )
    args = parser.parse_args()

    user = verify_auth()
    stats = verify_data()
    print(f"Authenticated as: {user}")
    print(
        f"Local harmonized: {stats['cells']} cells, {stats['tests']} tests, "
        f"{stats['parquet_bytes'] / 1e6:.1f} MB parquet "
        f"({stats['total_bytes'] / 1e6:.1f} MB total)"
    )

    create_repo(repo_id=HF_REPO, repo_type="dataset", exist_ok=True)

    # Write dataset card alongside the harmonized bundle so upload_folder picks it up
    card = build_dataset_card()
    card_path = HARMONIZED / "README.md"
    card_path.write_text(card)
    print(f"Wrote dataset card: {card_path} ({len(card):,} chars)")

    api = HfApi()
    print(f"Uploading to https://huggingface.co/datasets/{HF_REPO} ...")
    api.upload_folder(
        folder_path=str(HARMONIZED),
        repo_id=HF_REPO,
        repo_type="dataset",
        commit_message=args.message,
    )

    if args.revision:
        print(f"Tagging revision {args.revision} ...")
        api.create_tag(
            repo_id=HF_REPO,
            tag=args.revision,
            repo_type="dataset",
            tag_message=f"celljar {args.revision}",
            exist_ok=True,
        )

    n_rows = _timeseries_row_count()
    print()
    print("=" * 60)
    print("Published")
    print("=" * 60)
    print(f"  Cells:      {stats['cells']}")
    print(f"  Tests:      {stats['tests']}")
    print(f"  Timeseries: {n_rows:,} rows" if n_rows >= 0 else "  Timeseries: (row count unavailable)")
    print(f"  Uploaded:   {stats['total_bytes'] / 1e6:.1f} MB")
    print(f"  URL:        https://huggingface.co/datasets/{HF_REPO}")
    print()
    print("Verify the push works end-to-end with:")
    print(
        "  python -c \"import pandas as pd; "
        f"print(pd.read_parquet('https://huggingface.co/datasets/{HF_REPO}/resolve/main/timeseries.parquet', "
        "filters=[('test_id', '==', 'ORNL_LEAF_2013_HPPC_25C')]).head())\""
    )


if __name__ == "__main__":
    main()
