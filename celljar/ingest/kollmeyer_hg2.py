"""Ingester for KOLLMEYER / LG INR18650-HG2 dataset.

Data source: Kollmeyer, P., Vidal, C., Naguib, M., Skells, M. (2020).
LG 18650HG2 Li-ion Battery Data and Example Deep Neural Network xEV SOC
Estimator Script. Mendeley Data, v3. doi:10.17632/cp3473x7xv.3

Cell: LG Chem INR18650HG2 -- NMC (high-power "H-NMC" variant) + graphite
with SiO additive, 3.0 Ah nominal, cylindrical 18650, 20A continuous
discharge rating.

File format: MATLAB .mat files. Dataset organized in temperature
subfolders (0degC/, 10degC/, 25degC/, 40degC/, n10degC/, n20degC/),
with .mat files inside each.

Filename convention follows Kollmeyer's standard:
    {Date}_{Time} {Job#}_{Descriptor}_{Temp}degC_LGHG2.mat

Examples:
    11-06-18_19.09 556_US06_40degC_LGHG2.mat
    11-08-18_09.53 562_Mixed6_40degC_LGHG2.mat
    12-04-18_22.00 593_HPPC_n10degC_LGHG2.mat
    12-21-18_13.25 607_HPPC_n20degC_LGHG2.mat

Negative temperatures use 'n' prefix (e.g. "n10degC", "n20degC").
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
from scipy.io import loadmat


# ---------------------------------------------------------------------------
# Skip patterns
# ---------------------------------------------------------------------------
_SKIP_PATTERNS: list[re.Pattern] = [
    # Charge events: "_Charge##.mat" or bare "_Charge.mat" without a number.
    # The dataset has 16 numbered charges per temperature plus occasional
    # un-numbered ones; both are conditioning, not characterization tests.
    re.compile(r"_Charge\d*(?:_|\.|$)", re.IGNORECASE),
    re.compile(r"_PausCycl", re.IGNORECASE),
    re.compile(r"_Pause", re.IGNORECASE),
    re.compile(r"_PreChg", re.IGNORECASE),
    re.compile(r"_TS\d+", re.IGNORECASE),
]


def _parse_c_rate(c_str: str) -> float | None:
    s = c_str.replace("p", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Test-type pattern registry
# ---------------------------------------------------------------------------
_TEMP_RE = r"(?P<sign>[n\-])?(?P<temp>\d+)degC"

_TEST_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("HPPC", re.compile(rf"_HPPC_{_TEMP_RE}", re.IGNORECASE), "hppc"),
    ("OCV_C20", re.compile(rf"_C20DisCh_{_TEMP_RE}", re.IGNORECASE), "capacity_check"),
    (
        "Cap",
        re.compile(rf"_Cap_(?P<c_rate>\d+(?:p\d+)?)C_{_TEMP_RE}", re.IGNORECASE),
        "capacity_check",
    ),
    (
        "Dis",
        re.compile(rf"_Dis_(?P<c_rate>\d+(?:p\d+)?)C_{_TEMP_RE}", re.IGNORECASE),
        "capacity_check",
    ),
    ("UDDS", re.compile(rf"_UDDS_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    ("US06", re.compile(rf"_US06_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    ("HWFET", re.compile(rf"_HWFE?T_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    ("LA92", re.compile(rf"_LA92_{_TEMP_RE}", re.IGNORECASE), "drive_cycle"),
    (
        "Mixed",
        re.compile(rf"_Mixed(?P<idx>\d+)_{_TEMP_RE}", re.IGNORECASE),
        "drive_cycle",
    ),
]


def _parse_temp(match: re.Match) -> int:
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
    m = loadmat(str(mat_path), squeeze_me=False)
    meas = m["meas"]
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
    """Load LG INR18650-HG2 (Kollmeyer 2020) .mat files.

    Recurses into temperature subfolders.
    """
    raw = Path(raw_dir)
    if not raw.exists():
        raise FileNotFoundError(
            f"KOLLMEYER_HG2 data not found at {raw}. See "
            f"data/raw/KOLLMEYER_HG2/SOURCE_DATA_PROVENANCE.md (Mendeley: cp3473x7xv)."
        )

    datasets: dict = {}
    seen_counts: dict = {}
    seen_sizes: dict = {}

    skipped_conditioning: list[str] = []
    skipped_unrecognized: list[str] = []

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
                cycle_idx = c_rate_str

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
            f"No KOLLMEYER_HG2 test files matched in {raw}. Expected Kollmeyer-style "
            f"naming. Found: {[p.name for p in raw.rglob('*.mat')][:5]}..."
        )

    if skipped_conditioning:
        print(f"[kollmeyer_hg2] Skipped {len(skipped_conditioning)} conditioning files")
    if skipped_unrecognized:
        print(f"[kollmeyer_hg2] Skipped {len(skipped_unrecognized)} unrecognized .mat files")
    print(f"[kollmeyer_hg2] Ingested {len(datasets)} tests")

    return datasets
