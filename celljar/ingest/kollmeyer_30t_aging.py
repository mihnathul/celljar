"""Ingester for KOLLMEYER 30T fast-charge aging dataset (Borealis 2025).

Data source:
    Duque, J., Kollmeyer, P. J., Naguib, M. (2023, updated 2025).
    "Battery Aging Dataset for 15 Minute Fast Charging of Samsung 30T Cells"
    Borealis Data Repository. doi:10.5683/SP3/UYPYDJ -- CC-BY-4.0

Cells: Samsung INR21700-30T, six cells aged at 25 degC under five fast-charge
protocols until ~70% SOH. Distinct from the Mendeley characterization deposit
(doi:10.17632/9xyvy2njj3.2) which only ships BOL HPPC / drive-cycle data.

Six aged cells, one per protocol (CC has two replicates):
    CC      Constant Current 4.2V CC/CV, ~15 min fast charge      (cell #1)
    CC2     Constant Current 4.2V CC/CV, second replicate         (cell #2)
    BC      Boost Charging (high current at low SOC)              (cell #3)
    BCR     Boost Charging with Rest pauses                       (cell #4)
    BCNP    Boost Charging with Negative Pulses (30 s period)     (cell #5)
    BCNP_1s Boost Charging with Negative Pulses (1 s period)      (cell #6)

Test schedule per checkup (every ~30 fast-charge / drive-cycle cycles):
    1C charge to 4.2V CC/CV
    0.5C discharge to 2.5V                  ("..._halfC_0.5C_dchg_(a)_...")
    1C  discharge to 2.5V                   ("..._OneC_1C_dchg_...")
    2C  discharge to 2.5V                   ("..._TwoC_2C_dchg_...")
    HPPC at 10% SOC steps                   ("..._HPPC_...")
    OCV (C/20 sweep) every 60 cycles        ("..._OCV_0.05C_...")

Aging block between checkups: fast-charge profile + 15 drive cycles, repeated.

File layout (one ZIP per protocol-segment, ~16 GB total):
    03 + 04   CC protocol cycles 0..1908
    05 + 06   BC protocol cycles 0..1908
    07        BCR protocol full life
    08 + 09   BCNP protocol cycles 1..1730
    10        BCNP_1s protocol full life
    11        CC2 protocol full life

Each ZIP contains:
    {Protocol} protocol[_Cycles a to b]/Cycle XXXX/<test files>

Each test file is a MATLAB .mat with the canonical Kollmeyer `meas` struct:
    Time, Voltage, Current, Power, Ah, Wh, Battery_temp_DegC, TimeStamp,
    description

(Note: aging set uses lower-case `Battery_temp_DegC`; characterization set
uses `Battery_Temp_degC`. The harmonizer accepts either.)

The summary file (02-Data_Summary_SIX_Protocols.xlsx, served as TSV by
Borealis) holds the per-checkpoint 0.5C capacity and SOH for all six cells.
That file alone is enough to build the cycle_summary; the .mat files supply
the raw V/I/T timeseries.
"""

from __future__ import annotations

import csv
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Iterator

import numpy as np
import polars as pl
from scipy.io import loadmat


# ---------------------------------------------------------------------------
# Protocol / cell identification
# ---------------------------------------------------------------------------
# (key, archive prefix tokens, label, cell index in summary table)
_PROTOCOLS = [
    ("CC",      ["03-", "04-"],  "Constant Current"),
    ("CC2",     ["11-"],         "Constant Current Second Cell"),
    ("BC",      ["05-", "06-"],  "Boost Charging"),
    ("BCR",     ["07-"],         "Boost Charging with Rest"),
    ("BCNP",    ["08-", "09-"],  "Boost Charging with Negative Pulses"),
    ("BCNP_1s", ["10-"],         "Boost Charging with Negative Pulses (1s period)"),
]

_PROTOCOL_FOR_FILE: dict[str, str] = {
    prefix: key for key, prefixes, _ in _PROTOCOLS for prefix in prefixes
}


# ---------------------------------------------------------------------------
# Test-type recognition
# ---------------------------------------------------------------------------
# The aging archives bundle a lot of files per checkup. We classify only the
# characterization tests we want to harmonize as standalone time-series.
# Skipped: whole_Schedule weeks, fifteen-drive aggregates, tenpercent_chg
# pulses, forexcel partials, intermediate week markers.

_TEST_PATTERNS: list[tuple[str, re.Pattern]] = [
    # 0.5C discharge before fast charge (a) - the CAP source
    ("DCH_0p5C_a", re.compile(r"_halfC_0\.5C_dchg_\(a\)_", re.IGNORECASE)),
    # 0.5C discharge after fast charge (b) - second checkpoint snapshot
    ("DCH_0p5C_b", re.compile(r"_halfC_0\.5C_dchg_\(b\)_", re.IGNORECASE)),
    # 1C discharge characterization
    ("DCH_1C",     re.compile(r"_OneC_1C_dchg_",          re.IGNORECASE)),
    # 2C discharge characterization
    ("DCH_2C",     re.compile(r"_TwoC_2C_dchg_",          re.IGNORECASE)),
    # HPPC at 10% SOC steps
    ("HPPC",       re.compile(r"_HPPC_",                  re.IGNORECASE)),
    # OCV (C/20 discharge), present only at week 1 / 60 etc.
    ("OCV_C20",    re.compile(r"_OCV_0\.05C_",            re.IGNORECASE)),
    # 1C charge to 4.2V CC/CV between dchg(a) and 1C dchg
    ("CHG_1C",     re.compile(r"_ONE_C_charge_",          re.IGNORECASE)),
]

# Patterns to skip outright (logged for visibility, not ingested).
_SKIP_PATTERNS = [
    re.compile(r"_whole_Schedule_",      re.IGNORECASE),
    re.compile(r"_Fifteen_Drive_Cycles", re.IGNORECASE),
    re.compile(r"_tenpercent_chg_",      re.IGNORECASE),
    re.compile(r"_forexcel_",            re.IGNORECASE),
    re.compile(r"^Cover\d",              re.IGNORECASE),  # marker-only mats
    re.compile(r"^Cover20",              re.IGNORECASE),
    re.compile(r"_C20_chg_",             re.IGNORECASE),
]

# Cycle number from "...cycle_#NN" or "...cycle_#NN_to_MM" suffix in filename.
_CYCLE_NUM_RE = re.compile(r"cycle_#(?P<n>\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Summary (.tab / .xlsx) parsing -> cycle_summary rows
# ---------------------------------------------------------------------------
# Header row index in the summary file (0-indexed) - empirically row 36.
_SUMMARY_HEADER_ROW = 36
_SUMMARY_FIRST_DATA_ROW = 37

# Column slices in the summary TSV: the file has three vertically-stacked
# blocks. Block A covers cells CC/BC/BCNP/BCR; Block B covers CC2; Block C
# covers BCNP_1s. Each block is `cycle | week | date | fast_charges | <Ah
# columns> | <SOH columns>`.
# Indices are 0-based column positions (verified against the actual TSV).
_SUMMARY_BLOCK_A = {
    "cycle_col":        0,
    "fast_charges_col": 3,
    "cells": [
        # (label, capacity_Ah_col, soh_col)
        ("CC",   4,  8),
        ("BC",   5,  9),
        ("BCNP", 6, 10),
        ("BCR",  7, 11),
    ],
}
_SUMMARY_BLOCK_B = {
    "cycle_col":        13,
    "fast_charges_col": 16,
    "cells": [("CC2", 17, 18)],
}
_SUMMARY_BLOCK_C = {
    "cycle_col":        20,
    "fast_charges_col": 23,
    "cells": [("BCNP_1s", 24, 25)],
}
_SUMMARY_BLOCKS = (_SUMMARY_BLOCK_A, _SUMMARY_BLOCK_B, _SUMMARY_BLOCK_C)


def _read_summary_rows(summary_path: Path) -> list[list[str]]:
    """Read the summary TSV (Borealis serves the .xlsx as TAB).

    The Borealis API reformats the original .xlsx to tab-separated; rows
    before _SUMMARY_HEADER_ROW are legend metadata that we ignore.
    """
    with summary_path.open(newline="") as f:
        return list(csv.reader(f, delimiter="\t"))


def _parse_float(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_int(s: str | None) -> int | None:
    v = _parse_float(s)
    return int(v) if v is not None and np.isfinite(v) else None


def parse_summary(summary_path: Path) -> dict[str, list[dict]]:
    """Parse the per-checkpoint summary table.

    Returns dict keyed by protocol label ("CC", "CC2", ...) -> list of dicts:
        { cycle_number, fast_charges, capacity_Ah, soh }

    Each row is a separate aging checkpoint. SOH is dimensionless 0..1 in
    the source - we keep that; the harmonizer converts to %.
    """
    rows = _read_summary_rows(summary_path)
    if len(rows) <= _SUMMARY_FIRST_DATA_ROW:
        raise ValueError(
            f"Summary file too short: only {len(rows)} rows, expected "
            f"at least {_SUMMARY_FIRST_DATA_ROW + 1}. Path: {summary_path}"
        )

    out: dict[str, list[dict]] = {}
    for block in _SUMMARY_BLOCKS:
        cycle_col = block["cycle_col"]
        fc_col = block["fast_charges_col"]
        for label, ah_col, soh_col in block["cells"]:
            checkpoints: list[dict] = []
            for r in rows[_SUMMARY_FIRST_DATA_ROW:]:
                if max(cycle_col, ah_col, soh_col) >= len(r):
                    continue
                cycle = _parse_int(r[cycle_col])
                cap_ah = _parse_float(r[ah_col])
                if cycle is None or cap_ah is None:
                    continue
                checkpoints.append({
                    "cycle_number": cycle,
                    "fast_charges": _parse_int(r[fc_col]) if fc_col < len(r) else None,
                    "capacity_Ah": cap_ah,
                    "soh_frac": _parse_float(r[soh_col]),
                })
            if checkpoints:
                out[label] = checkpoints
    return out


# ---------------------------------------------------------------------------
# Archive / .mat parsing
# ---------------------------------------------------------------------------

def _classify(name: str) -> str | None:
    """Return canonical test_type or None if the file should be skipped."""
    base = name.rsplit("/", 1)[-1]
    for pat in _SKIP_PATTERNS:
        if pat.search(base):
            return None
    for tt, pat in _TEST_PATTERNS:
        if pat.search(base):
            return tt
    return None


def _parse_cycle_num(name: str) -> int | None:
    m = _CYCLE_NUM_RE.search(name)
    return int(m.group("n")) if m else None


def _meas_to_df(mat_bytes: bytes) -> pl.DataFrame | None:
    """Decode a Kollmeyer 30T aging .mat into a canonical Polars DataFrame.

    Returns None if the file lacks a recognisable `meas` struct (some mats
    in the archive are single-scalar wrappers like dchghalfCA).
    """
    try:
        m = loadmat(BytesIO(mat_bytes), squeeze_me=False)
    except Exception:                                  # noqa: BLE001
        return None
    if "meas" not in m:
        return None
    meas = m["meas"]
    names = meas.dtype.names or ()
    if not {"Time", "Voltage", "Current"}.issubset(names):
        return None

    def _ravel(field: str) -> np.ndarray:
        return np.asarray(meas[field][0, 0]).ravel()

    n = _ravel("Time").size
    # Battery temp field is named lower-case in the aging set, upper-case
    # in the characterization set -- accept either.
    if "Battery_temp_DegC" in names:
        temp = _ravel("Battery_temp_DegC")
    elif "Battery_Temp_degC" in names:
        temp = _ravel("Battery_Temp_degC")
    else:
        temp = np.full(n, np.nan)

    return pl.DataFrame({
        "Time":         _ravel("Time").astype(np.float64),
        "Voltage":      _ravel("Voltage").astype(np.float64),
        "Current":      _ravel("Current").astype(np.float64),
        "Ah":           _ravel("Ah").astype(np.float64) if "Ah" in names else np.full(n, np.nan),
        "Wh":           _ravel("Wh").astype(np.float64) if "Wh" in names else np.full(n, np.nan),
        "Battery_Temp_degC": temp.astype(np.float64),
    })


def _archive_protocol(zip_path: Path) -> str | None:
    """Map archive filename to protocol key (CC/BC/BCR/BCNP/BCNP_1s/CC2)."""
    name = zip_path.name
    for prefix, proto in _PROTOCOL_FOR_FILE.items():
        if name.startswith(prefix):
            return proto
    return None


def _iter_archive_mats(zip_path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (member_name, bytes) for every .mat in the archive."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".mat"):
                continue
            with zf.open(info) as fh:
                yield info.filename, fh.read()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest(raw_dir: str) -> dict:
    """Walk the Borealis 30T fast-charge aging download.

    Args:
        raw_dir: Path to ``data/raw/KOLLMEYER_30T_AGING/`` containing the
            12 files of doi:10.5683/SP3/UYPYDJ. The summary file may be
            named with either an .xlsx or .tab extension (Borealis serves
            the original .xlsx as TAB-separated when fetched via API).

    Returns:
        Dict with two top-level keys:
            "tests":   list of dicts, one per (protocol, cycle_number,
                       test_type), each carrying the canonical raw_df.
            "summary": dict of protocol -> list of checkpoint dicts.
    """
    raw = Path(raw_dir)
    if not raw.exists():
        raise FileNotFoundError(
            f"KOLLMEYER_30T_AGING data not found at {raw}. See the dataset "
            "deposit at doi:10.5683/SP3/UYPYDJ and place the 12 files (one "
            "summary + 9 protocol archives + readme + test description) "
            "directly under data/raw/KOLLMEYER_30T_AGING/."
        )

    # Locate the summary file (Borealis ships .xlsx but the API delivers TSV).
    summary_candidates = sorted(raw.glob("02-Data_Summary*"))
    if not summary_candidates:
        raise FileNotFoundError(
            f"Missing summary file (02-Data_Summary*) in {raw}. The summary "
            "is required because it carries per-checkpoint capacity / SOH "
            "for all six cells."
        )
    summary = parse_summary(summary_candidates[0])

    archives = sorted(p for p in raw.glob("*.zip"))
    if not archives:
        raise FileNotFoundError(
            f"No protocol archives in {raw}. Expected files like "
            "'03-CONSTANT_CURRENT_Cycles_0_to_1000.zip' (id 650457) ... "
            "'11-CONSTANT_CURRENT_SECOND_CELL_protocol.zip' (id 880401)."
        )

    tests: list[dict] = []
    skipped_unmapped: list[str] = []
    skipped_unrecognised: list[str] = []
    skipped_unparseable: list[str] = []

    for zip_path in archives:
        protocol = _archive_protocol(zip_path)
        if protocol is None:
            skipped_unmapped.append(zip_path.name)
            continue

        for member, payload in _iter_archive_mats(zip_path):
            test_type = _classify(member)
            if test_type is None:
                continue

            df = _meas_to_df(payload)
            if df is None:
                skipped_unparseable.append(member)
                continue

            cycle_num = _parse_cycle_num(member)
            tests.append({
                "protocol": protocol,
                "test_type": test_type,
                "cycle_number": cycle_num,
                "raw_df": df,
                "source_file": member,
                "archive": zip_path.name,
            })

    if not tests:
        raise FileNotFoundError(
            f"No characterization .mat files matched in {raw}. "
            "Expected '_HPPC_', '_halfC_0.5C_dchg_(a)_', '_OneC_1C_dchg_', "
            "'_TwoC_2C_dchg_', '_OCV_0.05C_', '_ONE_C_charge_'."
        )

    print(
        f"[kollmeyer_30t_aging] {len(tests)} characterization tests across "
        f"{len(summary)} cells; "
        f"skipped {len(skipped_unrecognised)} unrecognised, "
        f"{len(skipped_unparseable)} unparseable .mat"
    )

    return {
        "tests": tests,
        "summary": summary,
    }
