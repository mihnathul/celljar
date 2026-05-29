"""Data access layer for the celljar viewer.

Two backends, picked by environment:
    LocalProvider   reads from data/harmonized/   (CELLJAR_LOCAL=1)
    HFProvider      reads from HuggingFace        (default)

Both implement the same `DataProvider` protocol so the viewer doesn't care
which one it's talking to. To add a new backend (e.g. S3), implement the
protocol and add it to `get_provider()`.

Env vars:
    CELLJAR_LOCAL=1               read from data/harmonized/ (dev mode)
    CELLJAR_HF_REVISION=v0.3.0    pin HF backend to a specific tag/commit/branch
    CELLJAR_DEBUG=1               print cache-miss lines + show mtime panel in
                                  the sidebar (dev only)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import duckdb
import pandas as pd
import streamlit as st


HARMONIZED = Path(__file__).parent.parent / "data" / "harmonized"
HF_REPO = "mihnathul/celljar"
HF_REVISION = os.environ.get("CELLJAR_HF_REVISION", "main")
DEBUG = os.environ.get("CELLJAR_DEBUG") == "1"


def _dbg(msg: str) -> None:
    """Print cache-miss / dev-trace lines when CELLJAR_DEBUG=1."""
    if DEBUG:
        print(f"[celljar-debug] {msg}", flush=True)
USE_LOCAL = os.environ.get("CELLJAR_LOCAL", "").lower() in ("1", "true", "yes")
HF_BASE = f"https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}"


# --- Provider protocol ------------------------------------------------------

class DataProvider(Protocol):
    """How the viewer reads harmonized data - local-disk or remote-HF."""

    name: str

    def ensure_ready(self) -> tuple[bool, str | None]:
        """Best-effort prepare-for-reads. Returns (ok, error_msg)."""
        ...

    def timeseries_uri(self) -> str:
        """Path/URL DuckDB can SELECT FROM for timeseries.parquet."""
        ...

    def cycle_summary_uri(self) -> str:
        """Path/URL DuckDB can SELECT FROM for cycle_summary.parquet."""
        ...


class LocalProvider:
    """Reads from data/harmonized/ on the local filesystem."""

    name = "local"

    def ensure_ready(self) -> tuple[bool, str | None]:
        cells_dir = HARMONIZED / "cells"
        tests_dir = HARMONIZED / "tests"
        if (
            cells_dir.exists() and tests_dir.exists()
            and any(cells_dir.glob("*.json")) and any(tests_dir.glob("*.json"))
        ):
            return True, None
        return False, (
            f"CELLJAR_LOCAL=1 is set but no local data found at {HARMONIZED}. "
            "Run `python examples/demo_end_to_end.py` to generate it, "
            "or unset CELLJAR_LOCAL to fetch from HuggingFace instead."
        )

    def timeseries_uri(self) -> str:
        return str(HARMONIZED / "timeseries.parquet")

    def cycle_summary_uri(self) -> str:
        return str(HARMONIZED / "cycle_summary.parquet")


class HFProvider:
    """Reads from HuggingFace.

    Downloads lightweight metadata (cells/, tests/, cycle_summary.parquet) on
    first use; queries timeseries.parquet remotely via DuckDB HTTPS so users
    don't need 3+ GB of disk to inspect data.
    """

    name = "huggingface"

    def __init__(self, repo: str = HF_REPO, revision: str = HF_REVISION):
        self.repo = repo
        self.revision = revision

    def ensure_ready(self) -> tuple[bool, str | None]:
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            return False, (
                "HuggingFace is the default data source. Install with "
                "`pip install huggingface-hub`, or set CELLJAR_LOCAL=1 and run "
                "`python examples/demo_end_to_end.py` to use local data."
            )

        try:
            HARMONIZED.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                self.repo, repo_type="dataset", revision=self.revision,
                local_dir=str(HARMONIZED),
                allow_patterns=["cells/*.json", "tests/*.json", "cycle_summary.parquet"],
            )
            return True, None
        except Exception as exc:                  # noqa: BLE001
            cells_dir = HARMONIZED / "cells"
            have_local = cells_dir.exists() and any(cells_dir.glob("*.json"))
            msg = (
                f"Could not fetch metadata from HuggingFace ({self.repo} @ {self.revision}): "
                f"{exc}.\n\nSet `CELLJAR_LOCAL=1` and run "
                "`python examples/demo_end_to_end.py` to use local data instead."
            )
            if have_local:
                msg += "\n\n(Found local data - set CELLJAR_LOCAL=1 to use it.)"
            return False, msg

    def timeseries_uri(self) -> str:
        return f"https://huggingface.co/datasets/{self.repo}/resolve/{self.revision}/timeseries.parquet"

    def cycle_summary_uri(self) -> str:
        return f"https://huggingface.co/datasets/{self.repo}/resolve/{self.revision}/cycle_summary.parquet"


@st.cache_resource
def get_provider() -> DataProvider:
    """Pick the backend based on env vars (cached for the session)."""
    return LocalProvider() if USE_LOCAL else HFProvider()


@st.cache_resource
def ensure_metadata() -> tuple[bool, str | None]:
    return get_provider().ensure_ready()


def data_mtime() -> float:
    """Aggregate mtime of harmonized data so caches auto-invalidate when files change.

    For the cells/ and tests/ directories we take the max mtime over the JSON
    FILES, not the directory's own mtime. A directory's mtime only bumps when
    entries are added/removed/renamed - NOT when an existing file is overwritten
    in place (which is exactly what re-running demo_end_to_end.py does). Using
    the directory mtime alone meant the cache key never changed on a content
    re-write, so the viewer served stale DataFrames after a re-harmonize.
    """
    mtimes: list[float] = []
    for f in (HARMONIZED / "timeseries.parquet", HARMONIZED / "cycle_summary.parquet"):
        if f.exists():
            mtimes.append(f.stat().st_mtime)
    for d in (HARMONIZED / "cells", HARMONIZED / "tests"):
        if d.exists():
            mtimes.extend(f.stat().st_mtime for f in d.glob("*.json"))
    return max(mtimes) if mtimes else 0.0


# --- Cached loaders ---------------------------------------------------------

@st.cache_data
def load_cells(mtime: float = 0.0) -> pd.DataFrame:
    _dbg(f"cache MISS load_cells mtime={mtime}")
    return pd.DataFrame([json.load(open(p)) for p in (HARMONIZED / "cells").glob("*.json")])


@st.cache_data
def load_tests(mtime: float = 0.0) -> pd.DataFrame:
    _dbg(f"cache MISS load_tests mtime={mtime}")
    return pd.DataFrame([json.load(open(p)) for p in (HARMONIZED / "tests").glob("*.json")])


@st.cache_data
def load_timeseries(test_id: str, mtime: float = 0.0) -> pd.DataFrame:
    _dbg(f"cache MISS load_timeseries test_id={test_id} mtime={mtime}")
    uri = get_provider().timeseries_uri()
    return duckdb.sql(
        f"SELECT * FROM '{uri}' WHERE test_id = '{test_id}' ORDER BY timestamp_s"
    ).df()


@st.cache_data
def load_cycle_summary_for_tests(test_ids_tuple: tuple, mtime: float = 0.0) -> pd.DataFrame:
    """cycle_summary rows for the given test_ids. Empty DF if file missing or no rows.

    Uses pyarrow rather than DuckDB to avoid `NotImplementedException: PlainSkip
    not implemented` on Parquet files written with newer Polars / Parquet 2.0
    encodings. cycle_summary is small (<10k rows) so a full read + filter is
    fast enough and removes one source of version-compat breakage.
    """
    if not test_ids_tuple:
        return pd.DataFrame()
    _dbg(f"cache MISS load_cycle_summary_for_tests n_tests={len(test_ids_tuple)} mtime={mtime}")
    # cycle_summary.parquet is small (<10k rows) and downloaded locally in
    # both local + HF modes (snapshot_download includes it). Read from the
    # local path with pyarrow directly so we avoid the DuckDB Parquet 2.0
    # encoding issue (NotImplementedException: PlainSkip) on Polars-written
    # files.
    local_path = HARMONIZED / "cycle_summary.parquet"
    if not local_path.exists():
        return pd.DataFrame()
    import pyarrow.parquet as pq
    table = pq.read_table(local_path, filters=[("test_id", "in", list(test_ids_tuple))])
    return table.to_pandas()
