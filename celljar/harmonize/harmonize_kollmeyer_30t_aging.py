"""Harmonize KOLLMEYER 30T fast-charge aging data to canonical schema.

Input:  dict from celljar.ingest.kollmeyer_30t_aging.ingest() with keys
        "tests" (list of per-.mat records) and "summary" (per-checkpoint
        capacity / SOH for each of the six cells).

Output: cells_metadata, test_metadata, timeseries, cycle_summary.

Six aged cells, one cell_metadata record each (one cell_id per protocol).
Hundreds of test_metadata rows per cell - every checkup (~30 cycles apart)
contributes a HPPC + 0.5C dchg + 1C dchg + 2C dchg + 1C chg test.

Source: Duque, Kollmeyer, Naguib (2023, updated 2025), doi:10.5683/SP3/UYPYDJ.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

import numpy as np
import polars as pl


# ---------------------------------------------------------------------------
# Cells - one Samsung INR21700-30T per protocol
# ---------------------------------------------------------------------------
_CELL_TEMPLATE = {
    "source": "KOLLMEYER",
    "manufacturer": "Samsung SDI",
    "model_number": "INR21700-30T",
    "chemistry": "NMC",
    "cathode": "NMC",
    "anode": "graphite",
    "electrolyte": None,
    "form_factor": "cylindrical",
    "nominal_capacity_Ah": 3.0,
    "nominal_voltage_V": 3.6,
    "max_voltage_V": 4.2,
    "min_voltage_V": 2.5,
}

_PROTOCOL_DESCRIPTION = {
    "CC":      "Constant Current 4.2V CC/CV, 15-min fast charge to ~80% SOC then CV taper",
    "CC2":     "Constant Current 4.2V CC/CV, 15-min fast charge (replicate cell)",
    "BC":      "Boost Charging - high current at low SOC, taper at higher SOC",
    "BCR":     "Boost Charging with intermittent rest pauses",
    "BCNP":    "Boost Charging with Negative Pulses (30 s period)",
    "BCNP_1s": "Boost Charging with Negative Pulses (1 s period)",
}


_SOURCE_PROVENANCE = {
    "source_doi": "10.5683/SP3/UYPYDJ",
    "source_url": "https://borealisdata.ca/dataset.xhtml?persistentId=doi:10.5683/SP3/UYPYDJ",
    "source_citation": (
        "Duque, J., Kollmeyer, P. J., Naguib, M. (2023, updated 2025). "
        "Battery Aging Dataset for 15 Minute Fast Charging of Samsung 30T "
        "Cells. Borealis Data Repository. https://doi.org/10.5683/SP3/UYPYDJ"
    ),
    "source_license": "CC-BY-4.0",
    "source_license_url": "https://creativecommons.org/licenses/by/4.0/",
}


# Map ingester test_type tokens -> celljar canonical test_type strings.
# Schema regex: capacity_check | hppc | C{rate}{Discharge|Charge|DischargeCharge}
_TEST_TYPE_CANON = {
    "DCH_0p5C_a": "C0p5Discharge",
    "DCH_0p5C_b": "C0p5Discharge",
    "DCH_1C":     "C1Discharge",
    "DCH_2C":     "C2Discharge",
    "HPPC":       "hppc",
    "OCV_C20":    "C20Discharge",
    "CHG_1C":     "C1Charge",
}

# Discharge / charge C-rate per test_type, for test_metadata.c_rate_*
_TEST_C_RATE = {
    "DCH_0p5C_a": (None, 0.5),
    "DCH_0p5C_b": (None, 0.5),
    "DCH_1C":     (None, 1.0),
    "DCH_2C":     (None, 2.0),
    "HPPC":       (None, None),
    "OCV_C20":    (None, 0.05),
    "CHG_1C":     (1.0,  None),
}


# Folder-name pattern: ".../Cycle 0037/..." -> 37
_FOLDER_CYCLE_RE = re.compile(r"/Cycle\s+(\d+)/", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step_type_expr(threshold_A: float = 0.01) -> pl.Expr:
    return (
        pl.when(pl.col("current_A").abs() < threshold_A).then(pl.lit("rest"))
        .when(pl.col("current_A") > 0).then(pl.lit("charge"))
        .otherwise(pl.lit("discharge"))
        .alias("step_type")
    )


def _folder_cycle(member_path: str) -> int | None:
    m = _FOLDER_CYCLE_RE.search(member_path)
    return int(m.group(1)) if m else None


def _cell_id(protocol: str) -> str:
    return f"KOLLMEYER_30T_AGING_{protocol}"


def _build_test_id(protocol: str, test_type_token: str, cycle_num: int | None,
                   variant: str = "") -> str:
    cell = _cell_id(protocol)
    cyc = "BOL" if cycle_num in (None, 0) else f"CYC{cycle_num}"
    suffix = f"_{variant}" if variant else ""
    return f"{cell}_{test_type_token}_{cyc}{suffix}"


def _soh_lookup_table(summary: dict[str, list[dict]]) -> dict[str, list[tuple[int, float, float]]]:
    """Return per-protocol [(cycle_lo, soh_pct, capacity_Ah), ...] sorted."""
    out: dict[str, list[tuple[int, float, float]]] = {}
    for proto, rows in summary.items():
        triples: list[tuple[int, float, float]] = []
        for r in rows:
            cyc = r.get("cycle_number")
            soh = r.get("soh_frac")
            cap = r.get("capacity_Ah")
            if cyc is None:
                continue
            soh_pct = float(soh) * 100.0 if soh is not None else float("nan")
            triples.append((int(cyc), soh_pct, float(cap) if cap is not None else float("nan")))
        triples.sort(key=lambda t: t[0])
        out[proto] = triples
    return out


def _soh_at(soh_table: list[tuple[int, float, float]], folder_cycle: int | None) -> tuple[float | None, int | None]:
    """Given a folder cycle (BOL=0, then 37, 72, ...), return (soh_pct,
    summary_cycle) for the closest summary checkpoint that falls within
    [folder_cycle, folder_cycle + 36]. Falls back to nearest by absolute
    distance if no exact-window match exists.
    """
    if not soh_table:
        return None, None
    if folder_cycle is None:
        return None, None
    # First try the natural mapping: summary cycle in [folder_cycle, folder_cycle+36)
    for cyc, soh_pct, _cap in soh_table:
        if folder_cycle <= cyc < folder_cycle + 36:
            return soh_pct, cyc
    # Fallback: nearest by absolute distance
    nearest = min(soh_table, key=lambda t: abs(t[0] - folder_cycle))
    return nearest[1], nearest[0]


def _safe_min(arr: np.ndarray) -> float:
    a = arr[np.isfinite(arr)]
    return float(a.min()) if a.size else float("nan")


def _safe_max(arr: np.ndarray) -> float:
    a = arr[np.isfinite(arr)]
    return float(a.max()) if a.size else float("nan")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def harmonize(ingested: dict, capacity_Ah: float = 3.0) -> dict:
    """Harmonize ingester output to canonical celljar tables.

    Args:
        ingested: dict from kollmeyer_30t_aging.ingest()
        capacity_Ah: nominal cell capacity for SOC bookkeeping (3.0 Ah)

    Returns:
        Dict with cell_metadata, cells_metadata, test_metadata, timeseries,
        cycle_summary.
    """
    tests: list[dict] = ingested["tests"]
    summary: dict[str, list[dict]] = ingested["summary"]
    soh_tables = _soh_lookup_table(summary)

    # ---- one cell record per protocol that appears in either tests or summary
    protocols: set[str] = {t["protocol"] for t in tests} | set(summary.keys())
    cells_metadata: list[dict] = []
    for proto in sorted(protocols):
        cell_meta = dict(_CELL_TEMPLATE)
        cell_meta["cell_id"] = _cell_id(proto)
        cell_meta["source_cell_id"] = f"INR21700-30T_{proto}"
        cells_metadata.append(cell_meta)

    # ---- cycle_summary: one row per (protocol, summary checkpoint)
    cycle_summary_rows: list[dict] = []
    for proto in sorted(summary.keys()):
        cell_id = _cell_id(proto)
        bol_cap = None
        for ckpt in summary[proto]:
            cap = ckpt.get("capacity_Ah")
            if cap is None or not np.isfinite(cap) or cap <= 0:
                continue
            if bol_cap is None:
                bol_cap = cap
            soh_frac = ckpt.get("soh_frac")
            cycle_summary_rows.append({
                "test_id": f"{cell_id}_AGING_LIFE",
                "cell_id": cell_id,
                "cycle_number": int(ckpt["cycle_number"]),
                "equivalent_full_cycles": float(ckpt.get("fast_charges") or ckpt["cycle_number"]),
                "elapsed_time_s": None,
                "capacity_Ah": float(cap),
                "capacity_retention_pct": (
                    float(soh_frac) * 100.0 if soh_frac is not None
                    else (float(cap) / bol_cap * 100.0 if bol_cap else None)
                ),
                "resistance_dc_ohm": None,
                "resistance_dc_pulse_duration_s": None,
                "resistance_dc_soc_pct": None,
                "resistance_method": None,
                "energy_Wh": None,
                "coulombic_efficiency": None,
                "temperature_C_mean": 25.0,
            })

    # ---- per-protocol life-summary test_metadata (one row, holds the aging
    # curve as cycle_summary; no raw timeseries attached at this id).
    test_metadata: list[dict] = []
    for proto in sorted(summary.keys()):
        cell_id = _cell_id(proto)
        ckpts = summary[proto]
        cycles = [int(c["cycle_number"]) for c in ckpts if c.get("cycle_number") is not None]
        max_cycle = max(cycles) if cycles else 0
        test_metadata.append({
            "test_id": f"{cell_id}_AGING_LIFE",
            "cell_id": cell_id,
            "test_type": "cycle_aging",
            "temperature_C_min": 25.0,
            "temperature_C_max": 25.0,
            "soc_range_min": 0.0,
            "soc_range_max": 1.0,
            "soc_step": None,
            "soc_method": "protocol_asserted",
            "c_rate_charge": None,         # varies by protocol; documented in description
            "c_rate_discharge": 1.0,       # nominal drive-cycle dchg average
            "protocol_description": (
                f"Fast-charge aging - {_PROTOCOL_DESCRIPTION.get(proto, proto)}. "
                f"Each aging block: protocol fast-charge to 4.2V (~15 min) "
                f"followed by drive-cycle discharge. Periodic checkups every "
                f"~30 cycles (0.5C/1C/2C dchg + HPPC; OCV every 60 cycles)."
            ),
            "num_cycles": int(max_cycle),
            "soh_pct": (
                float(ckpts[-1].get("soh_frac") * 100.0)
                if ckpts and ckpts[-1].get("soh_frac") is not None else None
            ),
            "soh_method": "capacity_vs_first_checkpoint",
            "cycle_count_at_test": int(max_cycle),
            # Life-summary record represents the WHOLE life, not a single
            # RPT block - leave checkup_id null.
            "checkup_id": None,
            "test_year": 2024,
            "n_samples": 0,
            "duration_s": None,
            "voltage_observed_min_V": None,
            "voltage_observed_max_V": None,
            "current_observed_min_A": None,
            "current_observed_max_A": None,
            "temperature_observed_min_C": None,
            "temperature_observed_max_C": None,
            "sample_dt_min_s": None,
            "sample_dt_median_s": None,
            "sample_dt_max_s": None,
            # Life-summary record has no raw timeseries -> no coulomb count.
            "coulomb_count_observed_min_Ah": None,
            "coulomb_count_observed_max_Ah": None,
            **_SOURCE_PROVENANCE,
        })

    # ---- per-test characterization records (raw timeseries)
    timeseries_by_test: dict[str, pl.DataFrame] = {}
    test_id_counter: dict[str, int] = defaultdict(int)

    for t in tests:
        proto = t["protocol"]
        tt_token = t["test_type"]
        cycle_num = t["cycle_number"]
        member = t["source_file"]
        folder_cyc = _folder_cycle(member)
        cell_id = _cell_id(proto)
        canon_test_type = _TEST_TYPE_CANON[tt_token]
        c_rate_chg, c_rate_dch = _TEST_C_RATE[tt_token]

        soh_pct, summary_cycle = _soh_at(soh_tables.get(proto, []), folder_cyc)

        variant = ""
        if tt_token == "DCH_0p5C_a":
            variant = "a"
        elif tt_token == "DCH_0p5C_b":
            variant = "b"

        test_id = _build_test_id(proto, tt_token, cycle_num, variant)
        # Disambiguate any residual collisions (a single cycle # can repeat
        # if a checkup runs the same test twice for diagnostics).
        if test_id in timeseries_by_test:
            test_id_counter[test_id] += 1
            test_id = f"{test_id}_R{test_id_counter[test_id]}"

        raw = t["raw_df"]
        n = raw.height

        df = pl.DataFrame({
            "timestamp_s":    raw["Time"].cast(pl.Float64),
            "current_A":      raw["Current"].cast(pl.Float64),
            "voltage_V":      raw["Voltage"].cast(pl.Float64),
            "temperature_C":  raw["Battery_Temp_degC"].cast(pl.Float64),
            "coulomb_count_Ah": raw["Ah"].cast(pl.Float64),
            "energy_Wh":      raw["Wh"].cast(pl.Float64),
            "displacement_um": np.full(n, np.nan),
        }).with_columns([
            pl.lit(test_id).alias("test_id"),
            pl.lit(1, dtype=pl.Int64).alias("cycle_number"),
            pl.lit(None, dtype=pl.Int64).alias("step_number"),
            _step_type_expr(),
        ])

        timeseries_by_test[test_id] = df

        ts = df["timestamp_s"].to_numpy()
        sample_dt = np.diff(ts) if ts.size > 1 else np.array([])
        # Aging .mats can have timer resets across stitched segments -> negative dt.
        # Schema requires sample_dt_min_s >= 0, so filter to positive deltas for stats.
        sample_dt = sample_dt[sample_dt >= 0] if sample_dt.size else sample_dt
        v = df["voltage_V"].to_numpy()
        i_arr = df["current_A"].to_numpy()
        T = df["temperature_C"].to_numpy()

        cycle_count_at = folder_cyc if folder_cyc is not None else (cycle_num or 0)

        test_metadata.append({
            "test_id": test_id,
            "cell_id": cell_id,
            "test_type": canon_test_type,
            "temperature_C_min": 25.0,
            "temperature_C_max": 25.0,
            "soc_range_min": None,
            "soc_range_max": None,
            "soc_step": 0.1 if tt_token == "HPPC" else None,
            "soc_method": None,
            "c_rate_charge": c_rate_chg,
            "c_rate_discharge": c_rate_dch,
            "protocol_description": (
                f"{tt_token} characterization in {proto} aging campaign at "
                f"folder cycle {folder_cyc}"
                + (f" (file cycle #{cycle_num})" if cycle_num is not None else "")
            ),
            "num_cycles": 1,
            "soh_pct": soh_pct,
            "soh_method": "capacity_vs_first_checkpoint",
            "cycle_count_at_test": int(cycle_count_at),
            # FK grouping: every test at the same folder cycle belongs to the
            # same Reference Performance Test (RPT) block.
            "checkup_id": f"{cell_id}_CHECKUP_{int(cycle_count_at):04d}",
            "test_year": 2024,
            "n_samples": int(n),
            "duration_s": float(ts.max() - ts.min()) if n else 0.0,
            "voltage_observed_min_V": _safe_min(v),
            "voltage_observed_max_V": _safe_max(v),
            "current_observed_min_A": _safe_min(i_arr),
            "current_observed_max_A": _safe_max(i_arr),
            "temperature_observed_min_C": _safe_min(T),
            "temperature_observed_max_C": _safe_max(T),
            "sample_dt_min_s": float(_safe_min(sample_dt)) if sample_dt.size else None,
            "sample_dt_median_s": float(np.median(sample_dt)) if sample_dt.size else None,
            "sample_dt_max_s": float(_safe_max(sample_dt)) if sample_dt.size else None,
            "coulomb_count_observed_min_Ah": (
                float(np.nanmin(df["coulomb_count_Ah"].to_numpy()))
                if np.isfinite(df["coulomb_count_Ah"].to_numpy()).any() else None
            ),
            "coulomb_count_observed_max_Ah": (
                float(np.nanmax(df["coulomb_count_Ah"].to_numpy()))
                if np.isfinite(df["coulomb_count_Ah"].to_numpy()).any() else None
            ),
            **_SOURCE_PROVENANCE,
        })

    return {
        "cell_metadata": cells_metadata[0] if cells_metadata else None,
        "cells_metadata": cells_metadata,
        "test_metadata": test_metadata,
        "timeseries": timeseries_by_test,
        "cycle_summary": cycle_summary_rows,
    }
