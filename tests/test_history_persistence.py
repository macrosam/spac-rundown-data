"""Deterministic checks for SPAC Rundown history persistence and Pages parity.

Run: python3 -m pytest tests/ -v
"""
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_workflow_stages_both_data_files():
    wf = (REPO / ".github/workflows/update.yml").read_text()
    assert "git add data/latest.json data/history.json" in wf, (
        "update.yml must stage both latest.json and history.json"
    )


def test_workflow_has_path_coverage_check():
    wf = (REPO / ".github/workflows/update.yml").read_text()
    assert "check_coverage.py" in wf, "update.yml must run the path-coverage validator"


def test_latest_paths_covered_by_history():
    latest = json.loads((REPO / "data/latest.json").read_text())
    history = json.loads((REPO / "data/history.json").read_text())["rows"]
    missing = []
    for section in ("pipeline", "pricings", "amends", "mergers", "votes",
                    "pipes", "sponsors", "exchanges"):
        for rec in latest.get(section, []):
            if rec.get("path") and rec["path"] not in history:
                missing.append(rec["path"])
    assert not missing, f"{len(missing)} filing paths absent from history: {missing[:5]}"
