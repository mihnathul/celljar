# Changelog

All notable changes to celljar are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **KOLLMEYER 30T_AGING** source - Duque/Kollmeyer/Naguib Borealis dataset (DOI 10.5683/SP3/UYPYDJ): 6 Samsung INR21700-30T cells aged under five fast-charge protocols (CC, CC2, BC, BCR, BCNP, BCNP_1s) until ~70% SOH. Adds 931 characterization tests + 282 cycle_summary rows.
- `checkup_id` field on `test_metadata` - FK that groups segments belonging to the same Reference Performance Test (RPT) block. Populated for KOLLMEYER 30T_AGING; null for sources without RPT structure.
- `soc_method` field on `test_metadata` - documents how `soc_range_min/max` were determined (`coulomb_count` / `protocol_asserted` / `source_published` / null).
- `resistance_method` field on `cycle_summary` - documents how `resistance_dc_ohm` was determined (`source_published` / null). celljar never derives R_DC from V/I/T.
- `qocv` added to the allowed `test_type` enum (C/20 sweep, formerly mis-labelled `C20DischargeCharge` for Kollmeyer / HNEI).
- Viewer "Source links" sidebar section - per-DOI deduplication so KOLLMEYER's three deposits (Mendeley 30T BOL, Borealis aging, Mendeley HG2) surface separately with their dataset URLs, DOIs, licenses, and citations.
- Viewer "Cell ID legend" tooltip + inline caption - explains source-specific naming conventions (KOLLMEYER aging suffixes, MATR/CLO BxCy, Naumann TxSOCy encoding, NASA B{NNNN}, BILLS VAH).
- Viewer dev panel - gated on `CELLJAR_DEBUG=1`; shows mtime + Clear cache button + terminal cache-miss logging.

### Changed

- **`cycle_summary` is now STRICTLY source-published** - rows come only from a source originator's own published per-cycle / per-checkpoint table (NAUMANN xlsx, NASA per-discharge scalar, KOLLMEYER 30T_AGING xlsx). Prior versions derived rows from V/I/T for MATR / CLO / BILLS; that derivation now lives in downstream tools (PyBOP, equiv-circ-model, custom fitters). celljar is a harmonization layer, not an extraction layer.
- `test_type` for C/20 OCV sweep changed from `C20DischargeCharge` to `qocv`; `test_id` prefix changed from `CAP_CHECK_OCV_C20` to `qOCV_C20` (KOLLMEYER 30T BOL, HNEI).
- Viewer aging plot renders as a single-panel layout when no source in the selection publishes R_DC (NASA / KOLLMEYER aging) and as two panels only when at least one trace has R_DC data (NAUMANN).
- Viewer Tests table sorted by `(cell_id, cycle_count_at_test, test_type)` and pulls `checkup_id` forward as a visible column.
- Filter dropdowns hide cells with zero tests (e.g. ECKER stub, MOHTAT without raw data).

### Fixed

- NASA PCoE `cycle_summary` was previously initialized but never appended to (scaffold-only bug). Per-discharge `Capacity_Ah` scalars now flow into `cycle_summary` as 2714 rows; retention >150% sentinel matches the existing `soh_pct` behavior for cells with unreliable first-discharge BOL.
- KOLLMEYER 30T_AGING harmonizer clamps negative `sample_dt` (timer resets across stitched segments) so the schema's `ge=0` invariant holds.
- Streamlit viewer cache invalidation - cached loaders' `_mtime` kwarg was being silently dropped from the cache key because Streamlit's `@st.cache_data` excludes underscore-prefixed parameters by convention. Renamed to `mtime` so file-change-triggered cache misses actually fire.
- Viewer `cycle_summary` reads now use pyarrow instead of DuckDB - sidesteps `NotImplementedException: PlainSkip not implemented` on Parquet files written with newer Polars / Parquet 2.0 encodings.
- Aging plot x-axis tick labels were hidden by `shared_xaxes=True` when the bottom row had no data; both axes now force `showticklabels=True`.

### Removed

- "SOH vs cycle count" panel in the viewer - redundant with the cycle_summary-driven "Aging trajectory" panel and misleading for sources with one test per cell.

### Scope statement

celljar is a HARMONIZATION layer. It performs unit conversion, schema normalization, and provenance preservation. It does NOT fit `R_DC`, `dV/dQ`, OCV, or ECM parameters from V/I/T - those are downstream-tool concerns. The boundary is enforced in the schema via `*_method` fields that document where each derived quantity came from.

## [0.2.1] - 2026-04-25

- Renamed `cellstore` → `celljar`.
- Added sources: CLO, BILLS, MOHTAT, NASA PCoE, SNL Preger, Naumann, Ecker 2015 (stub). MATR raw bundle now distributed via HuggingFace.
- New `cycle_summary` entity (per-cycle aggregates).
- HuggingFace Dataset distribution at `huggingface.co/datasets/mihnathul/celljar`.
- Per-test provenance (`source_doi`, `source_citation`, `source_license`) carried as columns.
- Internal pipeline migrated to Polars; parquet uses zstd compression.
- Harmonizers decomposed into a uniform pipeline pattern.
- Streamlit viewer split into focused modules; HuggingFace is the default source.

## [0.1.0] - 2026-04-15

Initial release.

### Schema

- Canonical three-entity schema: `cell_metadata`, `test_metadata`, `timeseries`
- Defined as JSON Schema files in `schemas/` (authoritative contract)
- Mirrored as Pandera models in `celljar/harmonize/harmonize_schema.py` for
  runtime DataFrame validation inside the Python pipeline
- `test_metadata` carries `soh_pct`, `soh_method`, and `cycle_count_at_test`
  fields for aging context (methodology will iterate - see roadmap)

### Sources

- **ORNL_LEAF** - 2013 Nissan Leaf pouch cell, HPPC (raw + harmonized data bundled)
- **HNEI** - Panasonic NCR18650PF, 5-pulse HPPC (download required)
- **MATR** (Severson 2019) - 124 A123 LFP cells, fast-charge cycling (download required)

### Tooling

- `examples/demo_end_to_end.py` - run the pipeline across all present sources
- `apps/viewer.py` - Streamlit viewer with filters, aging plot, overlay
- GitHub Actions CI across Python 3.9-3.12
- Dependabot weekly updates
