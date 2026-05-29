"""Harmonize KOLLMEYER / LG INR18650-HG2 data to celljar canonical schema.

Input:  dict from celljar.ingest.kollmeyer_hg2.ingest() - one record per .mat file,
        keyed by (test_type, profile, temp_C) or (test_type, profile, temp_C, idx).
Output: canonical cell_metadata / test_metadata / timeseries.

Supported test_types: hppc, drive_cycle, capacity_check.

All tests share the same physical cell (LG INR18650HG2), so the
cell_metadata is a single record. Each .mat becomes one test record.

Source: Kollmeyer, P., Vidal, C., Naguib, M., Skells, M. (2020). Mendeley Data v3.
DOI: 10.17632/cp3473x7xv.3 -- CC-BY-4.0
"""

from __future__ import annotations

import numpy as np
import polars as pl


# Cell metadata - LG INR18650HG2, NMC chemistry per datasheet.
# H-NMC variant (high-power NMC) with graphite + SiO additive anode.
CELL_METADATA = {
    "cell_id": "KOLLMEYER_HG2_INR18650",
    "source": "KOLLMEYER",
    "source_cell_id": "INR18650-HG2",
    "manufacturer": "LG Chem",
    "model_number": "INR18650HG2",
    "chemistry": "NMC",                   # H-NMC (high-power) variant
    "cathode": "NMC",                     # high-power NMC formulation
    "anode": "graphite",                  # also has SiO additive (per LG datasheet)
    "electrolyte": None,
    "form_factor": "cylindrical",
    "nominal_capacity_Ah": 3.0,
    "nominal_voltage_V": 3.6,
    "max_voltage_V": 4.2,
    "min_voltage_V": 2.5,
}


# Per-source provenance applied to each test_metadata record.
_SOURCE_PROVENANCE = {
    "source_doi": "10.17632/cp3473x7xv.3",
    "source_url": "https://data.mendeley.com/datasets/cp3473x7xv/3",
    "source_citation": (
        "Kollmeyer, P., Vidal, C., Naguib, M., Skells, M. (2020). "
        "LG 18650HG2 Li-ion Battery Data and Example Deep Neural Network "
        "xEV SOC Estimator Script. Mendeley Data, v3. "
        "https://doi.org/10.17632/cp3473x7xv.3"
    ),
    "source_license": "CC-BY-4.0",
    "source_license_url": "https://creativecommons.org/licenses/by/4.0/",
}


_PROTOCOL_DESCRIPTIONS = {
    "HPPC":     "Multi-rate HPPC at 10% SOC steps (Kollmeyer HG2 protocol)",
    "UDDS":     "Urban Dynamometer Driving Schedule (UDDS) drive cycle",
    "US06":     "US06 Supplemental Federal Test Procedure (high-acceleration)",
    "HWFET":    "Highway Fuel Economy Test (HWFET) drive cycle",
    "LA92":     "LA92 (Unified / California) drive cycle",
    "Mixed":    "Mixed randomized drive cycle (Mixed1..Mixed8 - Kollmeyer training profile)",
    "OCV_C20":  "C/20 discharge characterization (capacity check / pseudo-OCV)",
    "Cap":      "Constant-current capacity check at specified C-rate",
    "Dis":      "Constant-current discharge at specified C-rate",
}


def _capacity_check_test_type(profile: str, c_rate_str: str | None) -> str:
    """Map (profile, c_rate filename token) → C-rate-aware test_type.

    Replaces the generic 'capacity_check' label so the viewer surfaces the
    actual protocol, e.g. 'C20DischargeCharge' (C/20 OCV sweep) vs 'C2Discharge'
    (2C constant-current discharge).
    """
    if profile == "OCV_C20":
        return "C20DischargeCharge"
    if c_rate_str is None:
        return "capacity_check"
    if profile == "Dis":
        return f"C{c_rate_str}Discharge"
    if profile == "Cap":
        return f"C{c_rate_str}DischargeCharge"
    return "capacity_check"


def _step_type_expr(threshold_A: float = 0.01) -> pl.Expr:
    """Vectorized polars expression for step_type from `current_A`."""
    return (
        pl.when(pl.col("current_A").abs() < threshold_A).then(pl.lit("rest"))
        .when(pl.col("current_A") > 0).then(pl.lit("charge"))
        .otherwise(pl.lit("discharge"))
        .alias("step_type")
    )


def _normalise_key(key) -> tuple:
    """Accept the ingester's tuple key. Returns (test_type, profile, temp_C, idx|None)."""
    if isinstance(key, tuple):
        if len(key) == 3:
            test_type, profile, temp_c = key
            return test_type, profile, int(temp_c), None
        if len(key) == 4:
            test_type, profile, temp_c, idx = key
            try:
                idx_val = int(idx)
            except (ValueError, TypeError):
                idx_val = idx  # e.g. "Rp" for repeat
            return test_type, profile, int(temp_c), idx_val
    raise ValueError(f"Unrecognised KOLLMEYER_HG2 ingest key: {key!r}")


def _build_test_id(test_type: str, profile: str, temp_c: int, idx) -> str:
    """Build a deterministic test_id."""
    base = CELL_METADATA["cell_id"]
    temp_tag = f"{int(temp_c)}C"

    if test_type == "hppc":
        if profile == "HPPC":
            test_id = f"{base}_HPPC_{temp_tag}"
        else:
            test_id = f"{base}_HPPC_{profile.upper()}_{temp_tag}"
    elif test_type == "drive_cycle":
        test_id = f"{base}_DRIVE_CYCLE_{profile.upper()}_{temp_tag}"
    elif test_type == "capacity_check":
        test_id = f"{base}_CAP_CHECK_{profile.upper()}_{temp_tag}"
    else:
        test_id = f"{base}_{test_type.upper()}_{profile.upper()}_{temp_tag}"

    if idx is not None:
        test_id = f"{test_id}_{idx}"
    return test_id


def harmonize(ingested_data: dict, capacity_Ah: float = 3.0) -> dict:
    """Harmonize KOLLMEYER_HG2 .mat data to canonical timeseries.

    Args:
        ingested_data: Dict from kollmeyer_hg2.ingest()
        capacity_Ah: Nominal cell capacity for SOC calculation (3.0 Ah default)

    Returns:
        Dict with cell_metadata, cells_metadata, test_metadata, timeseries.
    """
    timeseries_by_test = {}
    test_metadata = []

    for key, data in ingested_data.items():
        test_type, profile, temp_c, idx = _normalise_key(key)
        test_type = data.get("celljar_test_type", test_type)
        profile = data.get("profile", profile)
        temp_c = int(data.get("temperature_C", temp_c))

        # test_id keeps the legacy 'CAP_CHECK' segment for stable identifiers,
        # but the test_type label gets refined to the C-rate-specific protocol.
        test_id = _build_test_id(test_type, profile, temp_c, idx)
        if test_type == "capacity_check":
            test_type = _capacity_check_test_type(profile, data.get("c_rate_str"))
        raw = data["raw_df"]
        if not isinstance(raw, pl.DataFrame):
            raw = pl.from_pandas(raw)

        n = raw.height
        df = pl.DataFrame({
            "timestamp_s": raw["Time"].cast(pl.Float64),
            "current_A": raw["Current"].cast(pl.Float64),
            "voltage_V": raw["Voltage"].cast(pl.Float64),
            "temperature_C": raw["Battery_Temp_degC"].cast(pl.Float64),
            "coulomb_count_Ah": raw["Ah"].cast(pl.Float64),
            "energy_Wh": raw["Wh"].cast(pl.Float64),
            "displacement_um": np.full(n, np.nan),
        })
        df = df.with_columns([
            pl.lit(test_id).alias("test_id"),
            pl.lit(1, dtype=pl.Int64).alias("cycle_number"),
            pl.lit(None, dtype=pl.Int64).alias("step_number"),
            _step_type_expr(),
        ])

        # SOC range not persisted - it would be a coulomb-count / nominal-cap
        # derivation, not a measurement (celljar harmonize-don't-derive policy).
        timeseries_by_test[test_id] = df

        sample_dt = np.diff(df["timestamp_s"].to_numpy())

        soc_step = 0.1 if test_type == "hppc" and profile == "HPPC" else None
        protocol_description = _PROTOCOL_DESCRIPTIONS.get(
            profile, f"{profile} ({test_type})"
        )

        c_rate_discharge = (
            1.0 if profile == "Dis1C"
            else 0.05 if profile == "OCV_C20"
            else None
        )

        test_metadata.append({
            "test_id": test_id,
            "cell_id": CELL_METADATA["cell_id"],
            "test_type": test_type,
            "temperature_C_min": float(temp_c),
            "temperature_C_max": float(temp_c),
            "soc_range_min": None,
            "soc_range_max": None,
            "soc_step": soc_step,
            "soc_method": None,
            "c_rate_charge": None,
            "c_rate_discharge": c_rate_discharge,
            "protocol_description": protocol_description,
            "num_cycles": 1,
            "soh_pct": 100.0,
            "soh_method": "bol_assumption",
            "cycle_count_at_test": 0,
            "checkup_id": None,
            "test_year": 2020,
            "n_samples": int(len(df)),
            "duration_s": float(df["timestamp_s"].max() - df["timestamp_s"].min()),
            "voltage_observed_min_V": float(df["voltage_V"].min()),
            "voltage_observed_max_V": float(df["voltage_V"].max()),
            "current_observed_min_A": float(df["current_A"].min()),
            "current_observed_max_A": float(df["current_A"].max()),
            "temperature_observed_min_C": float(df["temperature_C"].min()),
            "temperature_observed_max_C": float(df["temperature_C"].max()),
            "sample_dt_min_s": float(max(0.0, np.min(sample_dt))) if len(sample_dt) else None,
            "sample_dt_median_s": float(np.median(sample_dt)) if len(sample_dt) else None,
            "sample_dt_max_s": float(np.max(sample_dt)) if len(sample_dt) else None,
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
        "cell_metadata": CELL_METADATA,
        "cells_metadata": [CELL_METADATA],
        "test_metadata": test_metadata,
        "timeseries": timeseries_by_test,
    }
