"""The triage-study sampler (scripts/triage_sample.py) on made-up findings: no data/ needed."""

import random

import pytest

from scripts import triage_sample as ts


class Fake:
    def __init__(self, d):
        self.d = d

    def model_dump(self):
        return self.d


def fake_findings(seed=7):
    rnd = random.Random(seed)
    out = {}
    for page in ts.QUOTA:
        rows = []
        for i in range(rnd.randint(40, 90)):
            sc = rnd.choice(["1.1.1", "1.3.1", "1.4.3", "2.1.1", "2.4.1", "2.4.4", "3.2.1", "3.2.4"])
            sel = rnd.choice(["html", "#home > a", "#news > a", f"td:nth-of-type({rnd.randint(1, 6)})"])
            rows.append({"finding_id": f"F-before-{page}-el{i:04d}-{sc}", "wcag": [sc],
                         "scanner_status": rnd.choice(["violation", "notice", "needs_review"]),
                         "evidence": {"selector": sel}, "scanner_details": [{"message": "m"}], "sources": ["pa11y"]})
        out[page] = rows
    return out


@pytest.fixture
def data(monkeypatch):
    d = fake_findings()
    monkeypatch.setattr(ts, "load_findings", lambda version, page: [Fake(x) for x in d[page]])
    return d


def test_sample_follows_the_protocol(data):
    rows = ts.sample()
    assert len(rows) == 30 and len({r["finding_id"] for r in rows}) == 30
    assert [sum(r["page"] == p for r in rows) for p in ts.QUOTA] == list(ts.QUOTA.values())
    assert sum(r["half"] == "A" for r in rows) == 15
    assert all(r["bucket"] in (ts.GREY, ts.HUMAN_ONLY) for r in rows)
    assert not any(r["status"] == "notice" and r["selector"] == "html" for r in rows)
    assert len({(r["sc"], r["selector"]) for r in rows}) == 30  # no repeated header/nav finding


def test_sample_is_the_same_every_time(data):
    assert ts.sample() == ts.sample()
