"""celljar viewer - Streamlit UI for the harmonized data.

    streamlit run apps/viewer.py

Default: fetches metadata from HuggingFace; queries timeseries.parquet over
HTTPS via DuckDB. Override env vars:
    CELLJAR_LOCAL=1               read from data/harmonized/
    CELLJAR_HF_REVISION=v0.2.1    pin to a specific HF tag

Data access lives in apps/data.py; aging-plot helpers in apps/aging.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Sibling-module imports (apps/data.py, apps/aging.py, apps/plots.py).
sys.path.insert(0, str(Path(__file__).parent))
from data import (
    HARMONIZED, HF_REPO, HF_REVISION, USE_LOCAL,
    data_mtime, ensure_metadata,
    load_cells, load_tests, load_timeseries, load_cycle_summary_for_tests,
)

# Check env directly so the dev panel works even if data.py is held by a
# stale Streamlit module cache (only the top-level script auto-reloads on
# save; imports do not).
import os as _os
_DEBUG = _os.environ.get("CELLJAR_DEBUG") == "1"
from aging import build_aging_figure, resolve_per_test_axis
from bundle import build_bundle_zip
from plots import build_overlay_figure


# --- Page setup ---
st.set_page_config(page_title="celljar", layout="wide")

# Let multiselect chips show their full label instead of truncating with "..." -
# the sidebar has horizontal slack and our cell_id / test_type strings are
# self-describing (KOLLMEYER_30T_INR21700, C20DischargeCharge, etc.).
st.markdown(
    """
    <style>
    [data-baseweb="tag"] span[title] {
        max-width: none !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }
    [data-baseweb="tag"] {
        max-width: none !important;
        height: auto !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("celljar - harmonized public battery test data")
_source_caption = (
    f"data: HuggingFace `{HF_REPO}` @ `{HF_REVISION}`"
    if not USE_LOCAL else
    "data: local `data/harmonized/` (CELLJAR_LOCAL=1)"
)
st.caption(
    f"{_source_caption} · "
    "[github.com/mihnathul/celljar](https://github.com/mihnathul/celljar)"
)

_have_metadata, _hf_error = ensure_metadata()
if not _have_metadata:
    st.error(_hf_error or "No data available.")
    st.stop()


# --- Load data (cached) ---
_MTIME = data_mtime()
cells = load_cells(mtime=_MTIME)
tests = load_tests(mtime=_MTIME)

if cells.empty or tests.empty:
    st.warning(
        "No harmonized data. Run `python examples/demo_end_to_end.py` first, "
        "or check HuggingFace connectivity."
    )
    st.stop()


# --- Dev panel: only renders when CELLJAR_DEBUG=1, prints cache-miss lines
# to the terminal as well. Use to verify @st.cache_data invalidates correctly
# after a demo re-run (mtime should tick, "Clear cache" button is one click).
if _DEBUG:
    import datetime as _dt
    from pathlib import Path as _Path
    with st.sidebar.expander("celljar-debug", expanded=True):
        st.caption(f"Aggregate mtime: `{_MTIME}`")
        for _name in ("cells", "tests", "timeseries.parquet", "cycle_summary.parquet"):
            _p = _Path(HARMONIZED) / _name
            if _p.exists():
                _m = _p.stat().st_mtime
                _ts = _dt.datetime.fromtimestamp(_m).strftime("%H:%M:%S")
                st.caption(f"`{_name}` mtime={_m:.3f} ({_ts})")
        if st.button("Clear st.cache_data"):
            st.cache_data.clear()
            st.rerun()

# --- Sidebar filters ---
st.sidebar.header("Filters")

# Hide cells with zero tests (e.g. ECKER stub, Mohtat without raw data). They
# clutter the dropdown and leave the user at a dead-end "No options to select".
_cells_with_tests = set(tests["cell_id"].dropna().unique())
cells_active = cells[cells["cell_id"].isin(_cells_with_tests)]

src = st.sidebar.multiselect(
    "Source", sorted(cells_active["source"].dropna().unique()),
    help="Pick one or more datasets (e.g. HNEI, NASA_PCOE) to filter on.",
)

# Cell options narrow to those in the selected source(s).
if src:
    cells_for_src = cells_active[cells_active["source"].isin(src)]
    available_cells = sorted(cells_for_src["cell_id"].dropna().unique())
else:
    available_cells = sorted(cells_active["cell_id"].dropna().unique())

# Per-cell-ID legends. Two consumption channels for the same data:
#   1. `help=` tooltip on the Cell multiselect - visible BEFORE selection, on
#      hover over the "?" icon. Shows the legend for cells in scope.
#   2. Inline caption below the multiselect - visible AFTER cells are picked.
# Two kinds of legend entry:
#   - SUFFIX_TABLE: cell_id ends in a meaningful token (e.g. KOLLMEYER aging
#     protocols). One line per matched suffix.
#   - STRUCTURAL: cell_id has a fixed multi-part structure (e.g. MATR BxCy,
#     Naumann test-conditions encoding). One line explaining the structure.
_CELL_ID_LEGENDS: list[dict] = [
    {
        "prefix": "KOLLMEYER_30T_AGING_",
        "kind": "suffix_table",
        "suffixes": {
            # Names below are the ORIGINATOR's literal labels from
            # 02-Data_Summary_SIX_Protocols.xlsx legend. Period for BCNP
            # comes from the test-description PDF (1.9 s + 0.1 s = 2 s).
            "CC":      "Constant Current (2.8C 15-min fast charge, baseline)",
            "CC2":     "Constant Current TWO (second cell)",
            "BC":      "Boost Charging (4C 5 min + 2.2C 10 min)",
            "BCR":     "Boost Charging with Rest (1.9 s pulse + 0.1 s rest)",
            "BCNP":    "Boost Charging with Negative Pulse (~2 s period)",
            "BCNP_1s": "Boost Charging with 1 second Negative Pulse",
        },
    },
    {
        "prefix": "MATR_B",
        "kind": "structural",
        "lines": [
            "`MATR_B{batch}C{cell}` - B=batch (1-3), C=cell index in batch.",
            "Each cell ran a different fast-charge policy (72 policies, 124 cells).",
        ],
    },
    {
        "prefix": "CLO_B",
        "kind": "structural",
        "lines": [
            "`CLO_B4C{cell}` - B4 = batch 4 (Stanford's convention extending "
            "MATR's b1/b2/b3 numbering), C = cell index in batch (0-44).",
            "All 45 cells come from the consolidated CLO release `.mat` file. "
            "The upstream project page (data.matr.io) shows 4 OED iterations + "
            "a validation batch, but Stanford collapsed them into one `b4` "
            "cohort for release.",
            "Each cell's charge policy was chosen ONLINE by Bayesian optimization (Attia 2020).",
        ],
    },
    {
        "prefix": "NAUMANN_",
        "kind": "structural",
        "lines": [
            "`NAUMANN_<TYPE>_T{temp}_SOC{soc}[_D{dod}_C{chg}_C{dchg}]`:",
            "TYPE: `CAL`=calendar storage, `CYC`=cycle aging, `LOAD`=load-profile.",
            "`T` = chamber temperature degC; `SOC` = mean SOC (% or fraction).",
            "Cycling tests add `D` = DoD %, `C{chg}_C{dchg}` = charge/discharge C-rates.",
        ],
    },
    {
        "prefix": "NASA_PCOE_B",
        "kind": "structural",
        "lines": [
            "`NASA_PCOE_B{NNNN}` - B = NASA's battery number, 4-digit zero-padded "
            "(e.g. B0005, B0007). NOT a batch index - just a sequential cell ID.",
        ],
    },
    {
        "prefix": "BILLS_EVTOL_VAH",
        "kind": "structural",
        "lines": [
            "`BILLS_EVTOL_VAH{NN}` - VAH = Variable Acceleration Hover; "
            "{NN} = sequential cell number in the 22-cell eVTOL cohort.",
        ],
    },
]


def _legend_blocks_for(scope_cells: list[str]) -> list[str]:
    """Return the legend text blocks relevant to a set of cell_ids."""
    blocks: list[str] = []
    for entry in _CELL_ID_LEGENDS:
        matched = [c for c in scope_cells if c.startswith(entry["prefix"])]
        if not matched:
            continue
        if entry["kind"] == "suffix_table":
            suffix_map = entry["suffixes"]
            matched_suffixes = sorted({
                sfx for cell in matched
                for sfx in suffix_map
                if cell == f"{entry['prefix']}{sfx}"
            })
            if matched_suffixes:
                blocks.append(
                    "\n".join(f"- `{s}` - {suffix_map[s]}" for s in matched_suffixes)
                )
        elif entry["kind"] == "structural":
            blocks.append("\n".join(entry["lines"]))
    return blocks


# Tooltip for the Cell multiselect: built from cells in scope so the user
# sees the legend BEFORE picking anything (hover the "?" icon).
_tooltip_base = (
    "Optionally narrow to specific cell IDs within the selected source(s). "
    "Leave empty to include all cells in the source."
)
_tooltip_blocks = _legend_blocks_for(available_cells)
_cell_help = _tooltip_base
if _tooltip_blocks:
    _cell_help += "\n\n**Cell ID legend:**\n\n" + "\n\n".join(_tooltip_blocks)

cell_sel = st.sidebar.multiselect(
    "Cell", available_cells, help=_cell_help,
)

# Inline caption below the multiselect: only renders AFTER cells are picked,
# narrows to just the legends relevant to the selection.
_legend_blocks = _legend_blocks_for(cell_sel)
if _legend_blocks:
    st.sidebar.caption("Cell ID legend  \n\n" + "  \n\n".join(_legend_blocks))

# Test-type options narrow to types actually present in the selected source(s)
# and (if any) selected cell(s). Without this, the dropdown shows all corpus
# test types even when the source/cell scope doesn't contain any.
if src:
    if cell_sel:
        scope_cell_ids = cell_sel
    else:
        scope_cell_ids = cells_for_src["cell_id"].tolist()
    available_types = (
        tests[tests["cell_id"].isin(scope_cell_ids)]
        ["test_type"].dropna().unique()
    )
else:
    available_types = tests["test_type"].dropna().unique()

ttype = st.sidebar.multiselect(
    "Test type", sorted(available_types),
    help="Filter by characterization protocol (hppc, cycle_aging, drive_cycle, etc.).",
)

# --- Sidebar: source provenance links for the selected source(s) ---
# One entry per UNIQUE DOI (a single `source` like KOLLMEYER can ship multiple
# deposits: Mendeley 30T BOL, Borealis 30T aging, Mendeley HG2 -> three DOIs).
# Narrows further to the selected cell(s) so picking one HG2 cell doesn't
# surface the unrelated 30T BOL + 30T aging deposits.
# Disambiguation label is the longest common cell_id prefix under each DOI.
if src:
    import os as _os

    prov_rows = (
        tests.merge(cells[["cell_id", "source"]], on="cell_id", how="left")
        .loc[lambda d: d["source"].isin(src),
             ["source", "source_doi", "source_url", "source_citation",
              "source_license", "source_license_url", "cell_id"]]
    )
    if cell_sel:
        prov_rows = prov_rows[prov_rows["cell_id"].isin(cell_sel)]
    if not prov_rows.empty:
        grouped = (
            prov_rows
            .groupby(["source", "source_doi"], dropna=False)
            .agg(source_url=("source_url", "first"),
                 source_citation=("source_citation", "first"),
                 source_license=("source_license", "first"),
                 source_license_url=("source_license_url", "first"),
                 cell_ids=("cell_id", lambda s: sorted(s.unique())))
            .reset_index()
            .sort_values(["source", "source_doi"])
        )
        doi_counts = grouped.groupby("source")["source_doi"].nunique().to_dict()

        def _short_label(source: str, cell_ids: list[str]) -> str:
            if doi_counts.get(source, 1) <= 1 or not cell_ids:
                return source
            common = _os.path.commonprefix(list(cell_ids))
            if common.startswith(f"{source}_"):
                common = common[len(source) + 1:]
            common = common.rstrip("_")
            return f"{source} - {common}" if common else source

        st.sidebar.markdown("### Source links")
        for _, r in grouped.iterrows():
            label = _short_label(r["source"], r["cell_ids"])
            with st.sidebar.expander(label, expanded=False):
                if r.get("source_url"):
                    st.markdown(f"[Dataset]({r['source_url']})")
                _doi_val = r.get("source_doi")
                # NASA PCoE has no registered DOI; null / NaN / empty all map
                # to "no DOI" and we just skip the line.
                if _doi_val is not None and str(_doi_val).strip().lower() not in ("", "nan", "none"):
                    doi = str(_doi_val).strip()
                    doi_url = f"https://doi.org/{doi}" if not doi.startswith("http") else doi
                    st.markdown(f"[DOI: {doi}]({doi_url})")
                if r.get("source_license"):
                    if r.get("source_license_url"):
                        st.markdown(f"License: [{r['source_license']}]({r['source_license_url']})")
                    else:
                        st.markdown(f"License: {r['source_license']}")
                if r.get("source_citation"):
                    st.caption(r["source_citation"])

# Empty-state gate: avoid rendering an empty-looking app on first load.
if not src or not ttype:
    st.info(
        "**Select one or more sources and test types in the sidebar to begin.**\n\n"
        f"celljar harmonizes {cells['source'].nunique()} sources and "
        f"{len(tests):,} tests across "
        f"{cells['chemistry'][cells['chemistry'] != 'mixed'].nunique()} named chemistries. "
        "Pick a source (e.g. HNEI for HPPC, MATR for cycling-aging) and a test type to "
        "inspect cells, overlay timeseries, and download bundles. Optionally filter "
        "to a specific cell ID."
    )
    st.stop()

# Apply filters.
cells_f = cells[cells["source"].isin(src)]
if cell_sel:
    cells_f = cells_f[cells_f["cell_id"].isin(cell_sel)]
tests_f = tests[
    tests["cell_id"].isin(cells_f["cell_id"]) & tests["test_type"].isin(ttype)
]


# --- Metrics row ---
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Cells", len(cells_f))
# Unique cell models - distinguishes cell instances from distinct hardware designs.
unique_models = (
    cells_f[["manufacturer", "model_number"]]
    .dropna(how="all").drop_duplicates().shape[0]
)
c2.metric("Unique cell models", unique_models)
c3.metric("Tests", len(tests_f))
c4.metric("Samples", f"{int(tests_f['n_samples'].sum()):,}" if not tests_f.empty else "0")
named_chems = cells_f["chemistry"][cells_f["chemistry"] != "mixed"].nunique()
c5.metric("Chemistries", named_chems,
          help="Count of named chemistry families (LFP/NMC/NCA/LCO/LMO/LTO). "
               "Excludes the generic 'mixed' bucket.")


# --- Bundle export ---
# Closure captures filtered DataFrames + the cached loader; bundle.py stays
# Streamlit-free.
def _build_bundle_zip(test_ids: list[str]) -> bytes:
    return build_bundle_zip(
        test_ids, cells_f, tests_f,
        load_timeseries=lambda tid: load_timeseries(tid, mtime=_MTIME),
    )


# --- Tables ---
st.subheader("Cells")
st.dataframe(cells_f, use_container_width=True, hide_index=True)

st.subheader("Tests")
# Provenance fields (source_doi / source_url / source_citation / source_license)
# are duplicated per-row; drop from the table since the sidebar "Source links"
# section renders them once per source.
_provenance_cols = [
    "source_doi", "source_url", "source_citation",
    "source_license", "source_license_url",
]
# Sort by (cell_id, cycle_count_at_test, test_type) so all tests from one
# checkup land as a contiguous block. For sources like Kollmeyer 30T_AGING
# where ~6 segments belong to one RPT block, this surfaces the schedule
# structure without a schema change.
_sort_cols = [c for c in ("cell_id", "cycle_count_at_test", "test_type") if c in tests_f.columns]
_tests_show = tests_f.sort_values(_sort_cols) if _sort_cols else tests_f
_tests_show = _tests_show.drop(columns=[c for c in _provenance_cols if c in _tests_show.columns])
# Pull checkup_id forward so the RPT grouping is visible without scrolling.
# Sources without RPT structure leave checkup_id null - the column just shows
# blanks for them, which is the honest behaviour.
_front_cols = [c for c in (
    "test_id", "cell_id", "checkup_id", "test_type", "cycle_count_at_test",
    # coulomb_count is the measured charge-throughput descriptor (cycler-native).
    # Pulled forward so it's visible; soc_range_* (now only populated for
    # source-published / protocol-asserted cases) sits further right.
    "coulomb_count_observed_min_Ah", "coulomb_count_observed_max_Ah",
) if c in _tests_show.columns]
_back_cols = [c for c in _tests_show.columns if c not in _front_cols]
_tests_show = _tests_show[_front_cols + _back_cols]
st.dataframe(_tests_show, use_container_width=True, hide_index=True)


# NOTE: a separate "SOH vs cycle count" panel used to live here. It pulled
# scalar soh_pct + cycle_count_at_test from test_metadata, which only gives
# one point per test - useless for sources like Kollmeyer 30T_AGING that
# ship one continuous test per cell, and redundant for sources like Naumann
# now that cycle_summary covers the full per-cycle trajectory cleanly. The
# Aging trajectory panel below is the canonical replacement.

# --- Inspect / overlay (V/I/T timeseries) ---
st.subheader("Inspect tests")
if tests_f.empty:
    st.stop()

selected = st.multiselect(
    "Tests to overlay",
    sorted(tests_f["test_id"].tolist()),
    default=[sorted(tests_f["test_id"].tolist())[0]],
)
align = st.checkbox("Align at t = 0", value=True)

if selected:
    loaded = {tid: load_timeseries(tid, mtime=_MTIME) for tid in selected}

    if all(df.empty for df in loaded.values()):
        st.caption(
            "Selected tests have no raw V/I/T timeseries (likely summary-only "
            "sources like Naumann). See the 'Aging trajectory' panel below for "
            "cycle_summary data instead."
        )
    else:
        st.plotly_chart(
            build_overlay_figure(loaded, align=align),
            use_container_width=True,
        )


# --- Aging trajectory (cycle_summary) ---
if selected:
    csum_df = load_cycle_summary_for_tests(tuple(selected), mtime=_MTIME)
    if not csum_df.empty:
        st.subheader("Aging trajectory")
        st.caption(
            "One point = one cycle. Capacity (+ R_DC when published by the source). "
            "celljar harmonizes - it does NOT derive these from V/I/T. "
            "~1000x smaller than V/I/T, so multi-cell overlays render fast."
        )
        per_test_axis = resolve_per_test_axis(csum_df, selected)

        if not per_test_axis:
            st.caption(
                "Selected tests have cycle_summary rows but no usable aging axis "
                "(elapsed_time_s / equivalent_full_cycles / cycle_number all null)."
            )
        else:
            kinds = {kind for (_, _, kind) in per_test_axis.values()}
            if len(kinds) > 1:
                st.warning(
                    "Selected tests use incompatible aging axes - pick all "
                    "calendar OR all cycling tests."
                )
            else:
                fig_age, _, _, _ = build_aging_figure(csum_df, selected, per_test_axis)
                st.plotly_chart(
                    fig_age, use_container_width=True,
                    config={
                        "modeBarButtonsToAdd": ["drawline", "drawrect", "eraseshape"],
                        "displayModeBar": True,
                    },
                )
                st.caption(
                    "Data source: `cycle_summary.parquet` "
                    f"({len(csum_df):,} rows for the selected test(s))."
                )
                # Per-source origin notes - only show the notes relevant to
                # what is actually in the current selection. Helps the user
                # understand whether they are looking at a true publication-
                # only summary (Naumann) or a per-cycle scalar attached to
                # cycling data by the source (NASA, Kollmeyer xlsx).
                _origin_notes = {
                    "NAUMANN": (
                        "NAUMANN - rows come from the source's published "
                        "xlsx (capacity vs storage time, R_DC vs FEC). The "
                        "source ships NO V/I/T - the summary IS the dataset."
                    ),
                    "NASA_PCOE": (
                        "NASA_PCOE - capacity_Ah is the per-discharge scalar "
                        "NASA's cycler attached to each .mat record at test "
                        "time. R_DC is not in this dataset (NASA publishes "
                        "EIS Re/Rct fits, which celljar deliberately skips "
                        "as they are model-fits, not measurements)."
                    ),
                    "KOLLMEYER": (
                        "KOLLMEYER 30T_AGING - rows come from the Borealis "
                        "xlsx summary table published alongside the raw "
                        "zips, one row per check-up (~30 cycle spacing). "
                        "R_DC is not in the xlsx."
                    ),
                }
                _sources_in_view = sorted({
                    cid.split("_")[0]
                    if not cid.startswith(("NASA_PCOE", "KOLLMEYER_30T_AGING"))
                    else ("NASA_PCOE" if cid.startswith("NASA_PCOE") else "KOLLMEYER")
                    for cid in csum_df["cell_id"].dropna().unique()
                })
                _shown = [_origin_notes[s] for s in _sources_in_view if s in _origin_notes]
                if _shown:
                    with st.expander("Where this aging data comes from", expanded=False):
                        for note in _shown:
                            st.markdown(f"- {note}")


# --- Download (bottom of page) ---
# Placed last so the inspection + aging panels lead; download is the final
# "now grab the data" step. Reached only when tests_f is non-empty (the
# Inspect section's st.stop() guards the empty case upstream).
st.subheader("Download selected tests")
st.caption(
    "Pick one or more tests. The ZIP bundle mirrors celljar's canonical "
    "layout - `cells/*.json`, `tests/*.json`, `timeseries.parquet` - so you "
    "unpack it and have everything (metadata, license, citation, measurements) "
    "for those tests, self-contained."
)
dl_selected = st.multiselect(
    "Tests", sorted(tests_f["test_id"].tolist()), key="dl_specific",
)
if dl_selected:
    bundle = _build_bundle_zip(dl_selected)
    st.download_button(
        f"Download {len(dl_selected)} test(s) - celljar bundle (ZIP)",
        data=bundle,
        file_name=f"celljar_bundle_{len(dl_selected)}tests.zip",
        mime="application/zip", key="dl_bundle",
        help=f"~{len(bundle)/1e6:.2f} MB - includes cell metadata, test "
             "metadata, and timeseries parquet.",
    )
else:
    st.download_button(
        "Download 0 test(s) - celljar bundle (ZIP)",
        data=b"", file_name="empty.zip", mime="application/zip",
        disabled=True, key="dl_bundle_disabled",
    )
