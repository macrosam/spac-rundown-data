"""Offline proof for issue #4, using committed feed symptoms, not SEC truth."""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("filings", ROOT / "fetch_spac_filings.py")
filings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filings)


@pytest.mark.parametrize("blurb", [
    "offering $5,125,000; 15,000,000 units; $10.00/unit (auto-extracted)",
    "offering $100,000; 28,750,000 units; $10.00/unit (auto-extracted)",
])
def test_committed_contradictions_withhold_amount_without_inventing_one(blurb):
    clean = filings.consistent_offering_blurb(blurb)
    assert "offering $" not in clean
    assert clean == "; ".join(blurb.split("; ")[1:])
    assert filings.consistent_offering_blurb(clean) == clean


@pytest.mark.parametrize("blurb", [
    "offering $150,000,000; 15,000,000 units; $10.00/unit (auto-extracted)",
    "offering $12,375,000; 1,500,000 units; $8.25/unit; focus: technology",
    "offering $75,000,000; 7,500,000 units (auto-extracted)",
    "offering $250,000; $10.00/unit (auto-extracted)",
    "meeting September 30, 2026; extension on the agenda (auto-extracted)",
])
def test_consistent_or_incomplete_facts_are_preserved(blurb):
    assert filings.consistent_offering_blurb(blurb) == blurb


def test_new_extraction_withholds_first_unrelated_dollar_match(monkeypatch):
    # Synthetic extractor input reproduces the committed Velos symptom. It is
    # deliberately not presented as a captured SEC filing or verified economics.
    raw = "</SEC-HEADER><p>$100,000 elsewhere</p><p>28,750,000 Units</p>"
    raw += "<p>offering price of $10.00</p>"
    monkeypatch.setattr(filings, "fetch", lambda *args, **kwargs: raw)
    assert filings.extract_blurb("S-1/A", "fixture.txt", "fixture") == (
        "28,750,000 units; $10.00/unit (auto-extracted)"
    )


def test_cached_history_is_sanitized_in_both_publication_files(tmp_path, monkeypatch):
    row = {
        "company": "Velos Acquisition I Corp.", "form": "S-1/A", "cik": "2016072",
        "filed": date.today().isoformat(),
        "path": "edgar/data/2016072/0001213900-26-104746.txt",
        "blurb": "offering $100,000; 28,750,000 units; $10.00/unit (auto-extracted)",
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data/history.json").write_text(json.dumps({"rows": {row["path"]: row}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEC_USER_AGENT", "offline-fixture")
    monkeypatch.setattr(filings, "fetch", lambda *args, **kwargs: None)
    monkeypatch.setattr(filings.time, "sleep", lambda _: None)
    assert filings.main() == 0
    latest = json.loads((tmp_path / "data/latest.json").read_text())["amends"][0]
    cached = json.loads((tmp_path / "data/history.json").read_text())["rows"][row["path"]]
    expected = "28,750,000 units; $10.00/unit (auto-extracted)"
    assert latest["blurb"] == cached["blurb"] == expected
    assert latest["path"] == row["path"]
