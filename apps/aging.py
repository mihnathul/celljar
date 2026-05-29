"""Aging-trajectory plot helpers for the celljar viewer.

Builds a capacity-retention + DC-resistance vs aging-axis plot from
cycle_summary.parquet rows. Aging axis is auto-picked per source (cycle_number,
equivalent_full_cycles, or elapsed_time_s).
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def pick_aging_axis(sub: pd.DataFrame) -> tuple[str, str, str]:
    """Decide which aging axis to use for a single test's cycle_summary rows.

    Priority:
        1. elapsed_time_s   → calendar-aging tests (Naumann)
        2. equivalent_full_cycles  → mixed-DoD aging (FEC-indexed)
        3. cycle_number     → standard cycling-to-failure

    Returns:
        (column_name, axis_label, kind)
        kind is "calendar" | "cycling" | "" (empty if none usable).
    """
    if "elapsed_time_s" in sub.columns and sub["elapsed_time_s"].notna().any():
        return "elapsed_time_s", "Time elapsed (days)", "calendar"
    if "equivalent_full_cycles" in sub.columns and sub["equivalent_full_cycles"].notna().any():
        return "equivalent_full_cycles", "Equivalent full cycles", "cycling"
    if "cycle_number" in sub.columns and sub["cycle_number"].notna().any():
        return "cycle_number", "Cycle number", "cycling"
    return "", "", ""


def resolve_per_test_axis(csum_df: pd.DataFrame, selected: list[str]) -> dict:
    """Map each selected test_id to its aging axis (column, label, kind)."""
    out: dict[str, tuple[str, str, str]] = {}
    for tid in selected:
        sub = csum_df[csum_df["test_id"] == tid]
        if sub.empty:
            continue
        col, label, kind = pick_aging_axis(sub)
        if col:
            out[tid] = (col, label, kind)
    return out


def build_aging_figure(
    csum_df: pd.DataFrame,
    selected: list[str],
    per_test_axis: dict,
) -> tuple[go.Figure, bool, bool, str]:
    """Build the aging plot: capacity (top) + DC resistance (bottom IF the
    selection has any R_DC data; otherwise just capacity).

    Returns (figure, plotted_capacity, plotted_resistance, x_label).
    """
    # Decide layout up front: only allocate a resistance row if at least one
    # selected test has resistance_dc_ohm in its cycle_summary (currently only
    # NAUMANN). Otherwise render a single-panel capacity-only figure - cleaner
    # than showing an empty annotated subplot every time.
    has_any_resistance = False
    for tid in selected:
        if tid not in per_test_axis:
            continue
        sub = csum_df[csum_df["test_id"] == tid]
        if "resistance_dc_ohm" in sub.columns and sub["resistance_dc_ohm"].notna().any():
            has_any_resistance = True
            break

    if has_any_resistance:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05)
    else:
        fig = make_subplots(rows=1, cols=1)
    palette = px.colors.qualitative.Plotly
    plotted_cap = False
    plotted_res = False
    x_label = ""

    for i, tid in enumerate(selected):
        if tid not in per_test_axis:
            continue
        x_col, x_label, _ = per_test_axis[tid]
        sub = csum_df[csum_df["test_id"] == tid].sort_values(by=x_col)
        x_vals = sub[x_col]
        if x_col == "elapsed_time_s":
            x_vals = x_vals / 86400.0  # seconds → days

        color = palette[i % len(palette)]
        common = dict(
            mode="lines+markers", legendgroup=tid, name=tid,
            line=dict(color=color), marker=dict(color=color),
        )

        # Capacity: prefer retention %, fall back to absolute Ah.
        y_cap, cap_label = _capacity_series(sub)
        if y_cap is not None:
            fig.add_trace(
                go.Scatter(x=x_vals, y=y_cap, showlegend=True, **common),
                row=1, col=1,
            )
            plotted_cap = True
            if cap_label:
                fig.update_yaxes(title_text=cap_label, row=1, col=1)

        # DC resistance - only added when the figure has a row 2.
        if has_any_resistance and \
           "resistance_dc_ohm" in sub.columns and sub["resistance_dc_ohm"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=x_vals, y=sub["resistance_dc_ohm"],
                    showlegend=(not plotted_cap), **common,
                ),
                row=2, col=1,
            )
            plotted_res = True

    if not plotted_cap:
        fig.add_annotation(
            text="No capacity data", xref="x domain", yref="y domain",
            x=0.5, y=0.5, showarrow=False, row=1, col=1,
        )

    fig.update_xaxes(title_text=x_label, showticklabels=True, row=1, col=1)
    if has_any_resistance:
        fig.update_yaxes(title_text="DC resistance (Ω)", row=2, col=1)
        # shared_xaxes hides top-row ticks by default; force them on so the
        # capacity panel keeps its tick labels.
        fig.update_xaxes(title_text=x_label, showticklabels=True, row=2, col=1)
        fig.update_layout(height=600, margin=dict(t=20))
    else:
        fig.update_layout(height=400, margin=dict(t=20))

    return fig, plotted_cap, plotted_res, x_label


def _capacity_series(sub: pd.DataFrame) -> tuple[pd.Series | None, str | None]:
    """Pick capacity_retention_pct if present, else capacity_Ah, else None."""
    if "capacity_retention_pct" in sub.columns and sub["capacity_retention_pct"].notna().any():
        return sub["capacity_retention_pct"], "Capacity retention (%)"
    if "capacity_Ah" in sub.columns and sub["capacity_Ah"].notna().any():
        return sub["capacity_Ah"], "Capacity (Ah)"
    return None, None
