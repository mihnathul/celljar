"""Shared helpers for working with the harmonized output bundle.

Both `examples/demo_end_to_end.py` and `examples/publish_to_huggingface.py`
need the same primitives (path resolution, NaN scrubbing, source enumeration,
row counting). Centralizing them here so the two scripts stay aligned.

No I/O happens at import time - every function takes a path argument explicitly.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def harmonized_dir(root: Path | None = None) -> Path:
    """Path to data/harmonized/ under the repo root."""
    if root is None:
        # celljar/bundle.py → repo root is two parents up.
        root = Path(__file__).parent.parent
    return root / "data" / "harmonized"


def nan_to_none(obj: Any) -> Any:
    """Recursively replace float NaN with None - pandera.polars + JSON-friendly.

    pandera.polars treats NaN as a non-null Float64 value (so range checks like
    `ge=0, le=1` fail on NaN), and NaN is not JSON-serializable. Walking the
    structure once before validation/serialization avoids both pitfalls.
    """
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: nan_to_none(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [nan_to_none(v) for v in obj]
    return obj


def collect_sources(harmonized: Path) -> dict[str, dict]:
    """Walk cells/ + tests/ to pull per-source provenance for the dataset card.

    Returns:
        {SOURCE_NAME: {"citation": ..., "license": ..., "doi": ..., "url": ...,
                        "license_url": ...}} - first non-null values win.
    """
    cells_dir = harmonized / "cells"
    tests_dir = harmonized / "tests"
    if not cells_dir.exists() or not tests_dir.exists():
        return {}

    sources: dict[str, dict] = {}

    for p in cells_dir.glob("*.json"):
        try:
            cell = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        src = cell.get("source")
        if src and src not in sources:
            sources[src] = {}

    # Test metadata carries the per-source citation / license / DOI fields.
    for p in tests_dir.glob("*.json"):
        try:
            test = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        cell_id = test.get("cell_id", "")
        # Determine source from cell_id prefix (e.g. "ORNL_LEAF_2013" → "ORNL").
        # Or fall back to the cell file lookup if needed.
        src = None
        for s in sources:
            if cell_id.startswith(s):
                src = s
                break
        if src is None:
            continue
        bucket = sources[src]
        for key_in, key_out in [
            ("source_citation", "citation"),
            ("source_license", "license"),
            ("source_license_url", "license_url"),
            ("source_url", "url"),
            ("source_doi", "doi"),
        ]:
            if not bucket.get(key_out) and test.get(key_in):
                bucket[key_out] = test[key_in]

    return sources


def validate_invariants(
    test_metadata: list[dict],
    cycle_summary: list[dict] | None = None,
) -> None:
    """Domain invariants that span fields - checked beyond pandera's per-field validation.

    Pandera enforces single-field constraints (`ge=0`, `isin=...`); this catches
    cross-field issues that would otherwise produce nonsensical data:

      test_metadata:
        - voltage_observed_min_V <= voltage_observed_max_V
        - current_observed_min_A <= current_observed_max_A
        - temperature_observed_min_C <= temperature_observed_max_C
        - sample_dt_min_s <= sample_dt_median_s <= sample_dt_max_s
        - calendar_aging tests should plausibly have null cycle_count_at_test
          (warn, not fail - some sources may stamp 0)

      cycle_summary:
        - At least one aging axis (cycle_number / equivalent_full_cycles /
          elapsed_time_s) must be non-null per row.
        - calendar_aging tests' cycle_summary rows expected to have
          elapsed_time_s populated.

    Raises:
        ValueError on the first violation, with test_id context.
    """
    for t in test_metadata:
        tid = t.get("test_id", "<unknown>")

        for low_key, high_key, label in [
            ("voltage_observed_min_V", "voltage_observed_max_V", "voltage"),
            ("current_observed_min_A", "current_observed_max_A", "current"),
            ("temperature_observed_min_C", "temperature_observed_max_C", "temperature"),
        ]:
            lo, hi = t.get(low_key), t.get(high_key)
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(
                    f"{tid}: {label}_observed_min ({lo}) > _max ({hi}) - "
                    "ingester is producing inverted observed bounds."
                )

        dt_min = t.get("sample_dt_min_s")
        dt_med = t.get("sample_dt_median_s")
        dt_max = t.get("sample_dt_max_s")
        if all(v is not None for v in (dt_min, dt_med, dt_max)):
            if not (dt_min <= dt_med <= dt_max):
                raise ValueError(
                    f"{tid}: sample_dt invariant violated - "
                    f"min={dt_min}, median={dt_med}, max={dt_max}"
                )

    if cycle_summary:
        # Map test_id → test_type so we can check calendar-aging-specific invariants.
        type_by_test = {t.get("test_id"): t.get("test_type") for t in test_metadata}

        for row in cycle_summary:
            tid = row.get("test_id", "<unknown>")
            axes = [
                row.get("cycle_number"),
                row.get("equivalent_full_cycles"),
                row.get("elapsed_time_s"),
            ]
            if not any(a is not None for a in axes):
                raise ValueError(
                    f"cycle_summary row for {tid} has no aging axis "
                    "(cycle_number / equivalent_full_cycles / elapsed_time_s "
                    "all null) - at least one is required."
                )

            ttype = type_by_test.get(tid)
            if ttype == "calendar_aging" and row.get("elapsed_time_s") is None:
                raise ValueError(
                    f"cycle_summary row for {tid} is calendar_aging but "
                    "elapsed_time_s is null - calendar aging requires a time axis."
                )


# --- Dataset table (single source of truth for the HF card AND the README) ---

# Display label per dataset. The unit of a "dataset" is one published deposit
# (one DOI); a single source can publish several (e.g. KOLLMEYER ships three:
# 30T aging + 30T BoL + HG2 BoL). NASA PCoE has no DOI, so it is keyed by its
# source name. Cell model, chemistry, test types, cell count, and license are
# all derived from the bundle - only the human-friendly label is curated here.
DATASET_LABELS = {
    "10.5281/zenodo.2580327":    "ORNL Leaf 2013",
    "10.17632/wykht8y7tg.1":     "HNEI 18650PF",
    "10.1038/s41560-019-0356-8": "MATR (Severson 2019)",
    "10.1038/s41586-020-1994-5": "CLO (Attia 2020)",
    "10.1184/R1/14226830":       "BILLS eVTOL (Bills 2023)",
    "NASA_PCOE":                 "NASA PCoE",
    "10.17632/kxh42bfgtj.1":     "Naumann 2018/2020",
    "10.5683/SP3/UYPYDJ":        "Kollmeyer 30T aging (Duque 2025)",
    "10.17632/9xyvy2njj3.2":     "Kollmeyer 30T BoL",
    "10.17632/cp3473x7xv.3":     "Kollmeyer HG2 BoL",
}

# Source render order (matches the README); rows fall back to alphabetical.
_SOURCE_ORDER = ["ORNL", "HNEI", "MATR", "CLO", "BILLS", "NASA_PCOE", "NAUMANN", "KOLLMEYER"]

# Manufacturer long-form -> short display name for the Cell model column.
_MFG_SHORT = {
    "A123 Systems": "A123", "Samsung SDI": "Samsung", "LG Chem": "LG",
    "Sony-Murata": "Sony",
}

# Render order for the coarse test_type categories; C-rate capacity-check labels
# (C2Discharge, C0p5Discharge, ...) - also first-class `test_type` values per the
# schema - sort after, by their natural string order.
_TEST_TYPE_ORDER = ["hppc", "qocv", "drive_cycle", "capacity_check", "cycle_aging", "calendar_aging"]


def _format_model(mfg, model) -> str:
    """Cell model from the `manufacturer` / `model_number` schema fields."""
    short = _MFG_SHORT.get(mfg, mfg)
    if model:
        return model if str(model).startswith(short) else f"{short} {model}"
    if mfg and mfg.startswith("Unknown"):
        return "undisclosed"
    return short or "-"


def _format_test_types(raw: set) -> str:
    """Actual `test_type` values as stored, coarse categories first."""
    def rank(t):
        return (_TEST_TYPE_ORDER.index(t) if t in _TEST_TYPE_ORDER else len(_TEST_TYPE_ORDER), t)
    return ", ".join(f"`{t}`" for t in sorted(raw, key=rank)) if raw else "-"


def collect_datasets(harmonized: Path) -> list[dict]:
    """Group the bundle into datasets - one published deposit (one DOI) each.

    NASA PCoE has no DOI, so it is keyed by its source name. A source can map to
    several datasets (KOLLMEYER -> 30T aging + 30T BoL + HG2 BoL). Stub cells
    (e.g. the Ecker placeholder) carry a cell record but no tests; the grouping
    is test-driven, so they drop out automatically.

    Returns a list of dicts (sorted for display) with keys: label, source, doi,
    url, license, license_url, n_cells, models (set of (mfg, model)), chemistry
    (set), test_types (set).
    """
    cells_dir = harmonized / "cells"
    tests_dir = harmonized / "tests"
    if not cells_dir.exists() or not tests_dir.exists():
        return []

    sources = collect_sources(harmonized)
    cell_source: dict[str, str] = {}
    cell_model: dict[str, tuple] = {}
    cell_chem: dict[str, str] = {}
    for p in cells_dir.glob("*.json"):
        try:
            cell = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        cid = cell.get("cell_id")
        if cid:
            cell_source[cid] = cell.get("source")
            cell_model[cid] = (cell.get("manufacturer"), cell.get("model_number"))
            cell_chem[cid] = cell.get("chemistry")

    datasets: dict = {}
    for p in tests_dir.glob("*.json"):
        try:
            t = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        cid = t.get("cell_id", "")
        src = cell_source.get(cid) or next((s for s in sources if cid.startswith(s)), None)
        if not src:
            continue
        doi = t.get("source_doi")
        key = doi or src
        d = datasets.setdefault(key, {
            "source": src, "doi": doi,
            "label": DATASET_LABELS.get(key) or DATASET_LABELS.get(src) or src,
            "license": t.get("source_license"),
            "license_url": t.get("source_license_url"),
            "url": t.get("source_url"),
            "cells": set(), "models": set(), "chemistry": set(), "test_types": set(),
        })
        d["cells"].add(cid)
        if cid in cell_model:
            d["models"].add(cell_model[cid])
        if cell_chem.get(cid):
            d["chemistry"].add(cell_chem[cid])
        if t.get("test_type"):
            d["test_types"].add(t["test_type"])

    for d in datasets.values():
        d["n_cells"] = len(d["cells"])

    return sorted(
        datasets.values(),
        key=lambda d: (
            _SOURCE_ORDER.index(d["source"]) if d["source"] in _SOURCE_ORDER else len(_SOURCE_ORDER),
            -d["n_cells"], d["label"],
        ),
    )


def render_dataset_table(datasets: list[dict]) -> str:
    """One row per dataset: label, cell model, chemistry, test types, cell
    count, license, DOI. Every column is a schema field (or derived from one);
    all links are absolute, so the table renders identically on GitHub and HF."""
    if not datasets:
        return "_No datasets discovered in the harmonized bundle._"

    rows = [
        "| Dataset | Cell model | Chemistry | Test types | Cells | License | DOI |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in datasets:
        models = " / ".join(_format_model(*m) for m in sorted(d["models"]))
        chem = ", ".join(f"`{c}`" for c in sorted(d["chemistry"])) or "-"
        test_types = _format_test_types(d["test_types"])
        lic = d.get("license") or "see upstream"
        lic_url = d.get("license_url")
        lic_cell = f"[{lic}]({lic_url})" if lic_url else lic
        doi = d.get("doi")
        url = d.get("url")
        if doi:
            doi = str(doi)
            doi_cell = f"[{doi}]({doi if doi.startswith('http') else 'https://doi.org/' + doi})"
        elif url:
            doi_cell = f"[dataset]({url})"
        else:
            doi_cell = "see upstream"
        rows.append(
            f"| {d['label']} | {models} | {chem} | {test_types} | {d['n_cells']} | {lic_cell} | {doi_cell} |"
        )
    return "\n".join(rows)


def timeseries_row_count(harmonized: Path) -> int:
    """Row count in timeseries.parquet, or -1 if file missing / unreadable."""
    parquet = harmonized / "timeseries.parquet"
    if not parquet.exists():
        return -1
    try:
        import pyarrow.parquet as pq
        return pq.ParquetFile(parquet).metadata.num_rows
    except Exception:                                # noqa: BLE001
        try:
            import polars as pl
            return pl.scan_parquet(parquet).select(pl.len()).collect().item()
        except Exception:                            # noqa: BLE001
            return -1


# --- README <-> HF card sync -------------------------------------------------
# The README is the single source of truth. The HF card is the README with the
# repo-only sections stripped (examples/publish_to_huggingface.py) plus YAML
# frontmatter. Two regions of the README are themselves generated from the
# bundle and kept fresh by `python examples/sync_readme.py`:
DATASETS_TABLE_START = "<!-- DATASETS_TABLE:START -->"
DATASETS_TABLE_END = "<!-- DATASETS_TABLE:END -->"
CONTENTS_START = "<!-- CONTENTS:START -->"
CONTENTS_END = "<!-- CONTENTS:END -->"


def render_contents_line(harmonized: Path) -> str:
    """One-line totals: cell models, cells, tests, timeseries rows, datasets."""
    ds = collect_datasets(harmonized)
    n_datasets = len(ds)
    n_cells = len({c for d in ds for c in d["cells"]})
    n_models = len({m for d in ds for m in d["models"]})
    n_tests = len(list((harmonized / "tests").glob("*.json")))
    n_rows = timeseries_row_count(harmonized)
    rows_str = f"~{round(n_rows / 1e6)}M" if n_rows >= 0 else "many"
    return (f"Contents: {n_models} unique cell models, {n_cells} cells, "
            f"{n_tests:,} tests, {rows_str} timeseries rows across {n_datasets} datasets (listed below).")


def _splice(text: str, start: str, end: str, inner: str) -> str:
    if start not in text or end not in text:
        raise ValueError(f"markers {start} / {end} not found")
    pre = text[: text.index(start) + len(start)]
    post = text[text.index(end):]
    return pre + inner + post


def sync_readme_text(text: str, harmonized: Path) -> str:
    """Refresh the generated regions of the README (datasets table + contents
    line) from the bundle, leaving everything else untouched."""
    text = _splice(text, DATASETS_TABLE_START, DATASETS_TABLE_END,
                   f"\n{render_dataset_table(collect_datasets(harmonized))}\n")
    text = _splice(text, CONTENTS_START, CONTENTS_END, render_contents_line(harmonized))
    return text
