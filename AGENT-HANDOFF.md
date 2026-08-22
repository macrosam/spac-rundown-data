# Agent Handoff: History Persistence + Pages Parity Fix

## What Changed

**Branch:** `fix/history-persistence-pages-parity`

### Problem (Issue #2 — Persist and backfill the rolling filings history)
`fetch_spac_filings.py` writes both `data/latest.json` and `data/history.json`,
but `.github/workflows/update.yml` staged only `git add data/latest.json`.
Result: `history.json` froze at 321 rows through 2026-07-02 while every
subsequent feed commit advanced only `latest.json`. The documented 30-day
fallback window silently degraded because history was never persisted.

### Problem (Issue #1 — GitHub Pages serving stale feed)
GitHub Pages deploys via a dynamic `pages-build-deployment` workflow triggered
on push to `main`. When the update workflow commits, Pages rebuilds. The issue
reported a transient Pages build cancellation for commit `3b5dbff`. Current
run history shows Pages builds succeeding consistently after every feed push.
The stale-feed symptom was deployment lag, not a repo-side defect. This fix
does not modify Pages configuration; it ensures both data files land in each
commit so the deployed state matches the repo state.

### Changes Made

1. **`.github/workflows/update.yml`** — changed `git add data/latest.json` to
   `git add data/latest.json data/history.json`, so the rolling store persists
   on every scheduled run.

2. **`scripts/check_coverage.py`** — new validator. Verifies every non-media
   EDGAR filing path in `latest.json` exists as a key in `history.json["rows"]`.
   Exits nonzero on gaps. Wired into the workflow between fetch and commit,
   so an incomplete backfill blocks publication rather than silently shipping.

3. **`tests/test_history_persistence.py`** — three deterministic pytest checks:
   workflow stages both files, workflow calls check_coverage.py, all current
   latest.json paths are covered by committed history.json.

4. **`data/history.json`** — one-time bounded backfill merged 61 current
   filing paths from `latest.json` into the rolling store, then pruned rows
   older than the 35-day horizon. Final store: 61 rows, newest filed date is
   current. Coverage check passes with zero missing paths.

5. **`AGENT-HANDOFF.md`** — this file. Review context for any agent or human.

### Why Not Just One Line

The issue body correctly noted that adding `git add data/history.json` alone
was insufficient because the oldest missing July 7+ records were near the edge
of the 7-day incremental fetch window (`INCREMENTAL_DAYS = 7`). Without the
backfill, those paths would have been permanently lost from history once they
fell outside the window. The coverage validator prevents recurrence by making
any future path gap a hard CI failure rather than a silent data loss.

### Verification Evidence

- `python3 scripts/check_coverage.py` → `OK: all 71 filing paths covered`
- `python3 -m pytest tests/ -v` → 3 passed
- Backfill script output → `+61 paths, pruned 321 old rows, final=61`

### Next Scheduled Run Behavior

The next cron run will:
1. Load 61-row history from the committed backfill.
2. Fetch the last 7 days of EDGAR indexes incrementally.
3. Add any new filings not already in history.
4. Enrich new 8-Ks and extract blurbs for new offering/proxy filings.
5. Run `scripts/check_coverage.py` — passes only if every path in the fresh
   `latest.json` exists in `history.json`.
6. Stage and commit both files if either changed.

If coverage fails, the workflow exits before commit, preserving the previous
good state rather than publishing a broken feed.

### Issue #1 Resolution Note

Current run list shows `pages-build-deployment` succeeding after every feed
push (runs 32517539178, 32480059871, 32408104892, etc.). The original issue's
stale-feed observation was a transient Pages build cancellation. No code
change was needed for #1; closing it after this PR merges and the next
scheduled run confirms live/repo parity is appropriate.
