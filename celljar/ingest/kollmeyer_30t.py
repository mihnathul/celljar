"""Ingester for KOLLMEYER / Samsung INR21700-30T dataset.

Data source: Kollmeyer, P., Skells, M. (2022). Samsung INR21700 30T 3Ah
Li-ion Battery Data. Mendeley Data, v2. doi:10.17632/9xyvy2njj3.2

Cell: Samsung SDI INR21700-30T -- NMC, 3.0 Ah nominal, cylindrical 21700,
high-power cell (35A continuous discharge rating).

File format: MATLAB .mat files. The dataset is organized in temperature
subfolders (e.g. -10degC/, -20degC/, 0degC/, 10degC/, 25degC/, 40degC/),
with .mat files inside each.

Filename convention (per dataset readme):
    {Date}_{Time} {Job#}_{Descriptor}_{Temp}degC_IN21700_30T.mat

Examples:
    03-22-19_15.28 746_HPPC_10degC_IN21700_30T.mat
    03-24-19_06.06 746_C20DisCh_10degC_IN21700_30T.mat
    03-26-19_00.33 746_Dis_0p5C_10degC_IN21700_30T.mat
    03-14-19_02.03 722_Mixed8_40degC_IN21700_30T.mat
    11-07-18_15.14 557_Cap_1C_40degC_IN21700_30T.mat
    11-06-18_19.09 556_US06_40degC_IN21700_30T.mat

Negative temperatures use a literal minus sign (e.g. "-10degC", "-20degC")
in both folder names AND filenames.

Each .mat holds a `meas` struct with these fields:
    Time, Voltage, Current, Ah, Wh, Power
    Battery_Temp_degC, TimeStamp

Test scope (per readme):
    - HPPC at 4 discharge C-rates (1, 2, 6, 12C) and 4 charge C-rates
      (0.5, 1, 2, 4C), reduced at lower temperatures
    - C/20 discharge (OCV characterization) ("..._C20DisCh.mat")
    - Rate-specific discharges (0.5C, 2C) ("..._Dis_0p5C.mat", "..._Dis_2C.mat")
    - 1C capacity tests ("..._Cap_1C.mat")
    - Drive cycles: UDDS, HWFET, LA92, US06
    - Mixed drive cycles 1-8 (random mix of UDDS/HWFET/LA92/US06)
    - Charge events (16 charges, ..._Charge##.mat) - skipped
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
from scipy.io import loadmat


# ---------------------------------------------------------------------------
# Skip patterns - conditioning, pause cycles, station markers
# ---------------------------------------------------------------------------
_SKIP_PATTERNS: list[re.Pattern] = [
    # Charge events: "_Charge##.mat" or bare "_Charge.mat" without a number.
    re.compile(r"_Charge\d*(?:_|\.|$)", re.IGNORECASE),
    # Pause cycles
    re.compile(r"_PausCycl", re.IGNORECASE),
    re.compile(r"_Pause", re.IGNORECASE),
    # Pre-charge conditioning, station markers
    re.compile(r"_PreChg", re.IGNORECASE),
    re.compile(r"_TS\d+", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# C-rate parsing helper: '0p5' -> 0.5
# ---------------------------------------------------------------------------
def _parse_c_rate(c_str: str) -> float | None:
    s = c_str.replace("p", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Test-type pattern registry. Format follows:
#   _DESCRIPTOR_(n|-)?TEMPdegC_
# Negative temps prefixed with 'n' (HG2-style) or '-' (30T-style).
# ---------------------------------------------------------------------------
_TEMP_RE = r"(?P<sign>[n\-])?(?P<temp>\d+)degC"

_TEST_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # HPPC: "_HPPC_TempdegC_"
    ("HPPC", re.compile(rf"_HPPC_{_TEMP_RE}", re.IGNORECASE), "hppc"),
    # C/20 discharge: "_C20DisCh_TempdegC_"
    ("OCV_C20", re.compile(rf"_C20DisCh_{_TEMP_RE}", re.IGNORECASE), "capacity_check"),
    # Capacity at specific C-rate: "_Cap_1C_TempdegC_"
    (
        "Cap",
        re.compile(rf"_Cap_(?P<c_rate>\d+(?:p\d+)?)C_{_TEMP_RE}", re.IGNORECASE),
        "capacity_check",
    ),
    # Rate-specific discharge: "_Dis_0p5C_TempdegC_", "_Dis_2C_TempdegC_"
    (
        "Dis",
        re.compile(rf"_Dis_(?P<c_rate>\d+(?:p\d+)?)C_{_TEMP_RE}", re.IGNORECASE),
        "capacity_check",
    ),
    # Standard drive cycles
    ("UDDS", re.compile(rf"_UDDS_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    ("US06", re.compile(rf"_US06_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    ("HWFET", re.compile(rf"_HWFE?T_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    ("LA92", re.compile(rf"_LA92_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    # Mixed drive cycles: "_Mixed1_TempdegC_" .. "_Mixed8_TempdegC_"
    (
        "Mixed",
        re.compile(rf"_Mixed(?P<idx>\d+)_{_TEMP_RE}", re.IGNORECASE),
        "drive_cycle",
    ),
]


def _parse_temp(match: re.Match) -> int:
    """Extract temperature from match. Negative if sign is 'n' or '-'."""
    groups = match.groupdict()
    t = int(groups["temp"])
    sign = groups.get("sign")
    return -t if sign in ("n", "-", "N") else t


def _should_skip(name: str) -> bool:
    return any(p.search(name) for p in _SKIP_PATTERNS)


def _match_filename(name: str):
    for profile, regex, test_type in _TEST_PATTERNS:
        m = regex.search(name)
        if m:
            return profile, m, test_type
    return None


def _load_meas_df(mat_path: Path) -> pl.DataFrame:
    """Load a Kollmeyer .mat and return a canonical meas DataFrame."""
    m = loadmat(str(mat_path), squeeze_me=False)
    meas = m["meas"]
    # Some Kollmeyer files don't include Chamber_Temp_degC; fall back to NaN.
    n = meas["Time"][0, 0].size
    chamber = (
        meas["Chamber_Temp_degC"][0, 0].ravel()
        if "Chamber_Temp_degC" in meas.dtype.names
        else [float("nan")] * n
    )
    return pl.DataFrame({
        "Time": meas["Time"][0, 0].ravel(),
        "Voltage": meas["Voltage"][0, 0].ravel(),
        "Current": meas["Current"][0, 0].ravel(),
        "Ah": meas["Ah"][0, 0].ravel(),
        "Wh": meas["Wh"][0, 0].ravel(),
        "Battery_Temp_degC": meas["Battery_Temp_degC"][0, 0].ravel(),
        "Chamber_Temp_degC": chamber,
    })


def ingest(raw_dir: str) -> dict:
    """Load Samsung INR21700-30T (Kollmeyer 2022) .mat files.

    Recurses into temperature subfolders.

    Args:
        raw_dir: Path to data/raw/KOLLMEYER_30T/ containing the dataset.

    Returns:
        Dict keyed by ``(test_type, profile, temperature_C)`` or
        ``(test_type, profile, temperature_C, idx)`` for repeated/multi-rate.
    """
    raw = Path(raw_dir)
    if not raw.exists():
        raise FileNotFoundError(
            f"KOLLMEYER_30T data not found at {raw}. See "
            f"data/raw/KOLLMEYER_30T/SOURCE_DATA_PROVENANCE.md for download "
            f"instructions (Mendeley dataset: 9xyvy2njj3)."
        )

    datasets: dict = {}
    seen_counts: dict = {}
    seen_sizes: dict = {}

    skipped_conditioning: list[str] = []
    skipped_unrecognized: list[str] = []

    # Recurse so dataset-internal temp subfolders work transparently.
    for mat_file in sorted(raw.rglob("*.mat")):
        name = mat_file.name

        if _should_skip(name):
            skipped_conditioning.append(name)
            continue

        hit = _match_filename(name)
        if hit is None:
            skipped_unrecognized.append(name)
            continue

        profile, match, test_type = hit
        temp_c = _parse_temp(match)

        cycle_idx = None
        c_rate = None
        c_rate_str = None
        groups = match.groupdict()
        if groups.get("idx") is not None:
            try:
                cycle_idx = int(match.group("idx"))
            except (ValueError, TypeError):
                cycle_idx = match.group("idx")
        if groups.get("c_rate") is not None:
            c_rate_str = groups["c_rate"]
            c_rate = _parse_c_rate(c_rate_str)
            if cycle_idx is None and c_rate is not None:
                cycle_idx = c_rate_str  # disambiguate multi-rate files

        base_key = (test_type, profile, temp_c)
        file_size = mat_file.stat().st_size
        if file_size in seen_sizes.get(base_key, set()) and cycle_idx is None:
            continue
        seen_sizes.setdefault(base_key, set()).add(file_size)

        try:
            df = _load_meas_df(mat_file)
        except Exception:  # pragma: no cover
            continue

        seen_counts[base_key] = seen_counts.get(base_key, 0) + 1
        occurrence = seen_counts[base_key]

        if occurrence == 1 and cycle_idx is None:
            key = base_key
        else:
            idx = cycle_idx if cycle_idx is not None else occurrence
            if base_key in datasets and occurrence == 2:
                first = datasets.pop(base_key)
                first_idx = first.get("cycle_index") or 1
                datasets[(*base_key, first_idx)] = first
            candidate_key = (*base_key, idx)
            if candidate_key in datasets:
                key = (*base_key, occurrence)
            else:
                key = candidate_key

        datasets[key] = {
            "raw_df": df,
            "temperature_C": temp_c,
            "profile": profile,
            "celljar_test_type": test_type,
            "source_file": name,
            "cycle_index": cycle_idx,
            "c_rate": c_rate,
            "c_rate_str": c_rate_str,
        }

    if not datasets:
        raise FileNotFoundError(
            f"No KOLLMEYER_30T test files matched in {raw}. Expected files like "
            f"'25degC/03-14-19_17.34 729_HPPC_25degC_IN21700_30T.mat'. "
            f"Found: {[p.name for p in raw.rglob('*.mat')][:5]}..."
        )

    if skipped_conditioning:
        print(f"[kollmeyer_30t] Skipped {len(skipped_conditioning)} conditioning files")
    if skipped_unrecognized:
        print(f"[kollmeyer_30t] Skipped {len(skipped_unrecognized)} unrecognized .mat files")
    print(f"[kollmeyer_30t] Ingested {len(datasets)} tests")

    return datasets
