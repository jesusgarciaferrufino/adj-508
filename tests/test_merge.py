"""Track A merge step: behavior that doesn't need a browser or the network."""

import pytest

import scanners.merge as merge


def test_missing_scan_raises_with_next_step(tmp_path, monkeypatch):
    """A missing scan file is an exception callers can catch, not a process exit."""
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    with pytest.raises(merge.MissingScanError, match=r"Run: python scanners/axe_scan.py --version before --page home"):
        merge.load_hits("before", "home")


def test_finding_id_is_stable():
    assert merge.finding_id("before", "home", 81, "1.1.1") == "F-before-home-el0081-1.1.1"
