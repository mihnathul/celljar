# Changelog

All notable changes to celljar are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-05-27

- Added **KOLLMEYER 30T_AGING** source (6 Samsung INR21700-30T cells, 5 fast-charge protocols).
- New schema fields: `checkup_id` (groups RPT checkup segments), `coulomb_count_observed_min/max_Ah` (measured charge throughput), `soc_method` + `resistance_method` (provenance tags), and a `qocv` test_type for C/20 sweeps.
- `cycle_summary` is now strictly source-published - removed the V/I/T-derived rows (MATR / CLO / BILLS); per-cycle derivation belongs in downstream tools.
- Viewer: per-DOI source links, cell-ID legends, aging plot updates, and a `CELLJAR_DEBUG=1` dev panel.
- Fixed two viewer cache-invalidation bugs, a DuckDB/Parquet read incompatibility, and the NASA `cycle_summary` scaffold-no-append bug.
- Scope: celljar harmonizes measurements - it does not fit R_DC / dV-dQ / OCV from V/I/T (that's downstream-pipeline). Source-published values ARE carried, tagged via `*_method` (e.g. Naumann's R_DC).

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
