# Changelog

## 2026-08-22 — fix/history-persistence-pages-parity

### Fixed
- `update.yml` now stages `data/history.json` alongside `data/latest.json`
  (#2). Rolling filings history persists across scheduled runs instead of
  freezing at its initial bootstrap state.
- One-time backfill restored 61 missing filing paths into `history.json`,
  covering all current non-media EDGAR references in the live feed.
- Added `scripts/check_coverage.py` as a pre-commit CI gate that fails the
  workflow when any `latest.json` filing path is absent from `history.json`.
- Added deterministic pytest suite in `tests/` covering workflow staging,
  validator presence, and path-coverage parity.

### Notes
- Issue #1 (Pages stale feed) was a transient GitHub Pages deployment
  cancellation; subsequent runs confirm Pages parity. No code change required.
