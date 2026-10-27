"""Track C: scripts/compare_against.py (AGAINST _v1 vs _v2) and the _v2 prompt itself.

No Bedrock: a fake client reads each prompt like the model would (which prompt, which criterion,
which HTML) and answers with canned strengths, quoting the finding's real HTML so the pipeline's
own citation check runs for real.
"""

import json
import re

import pytest

import settings
from agents import evidence
from scanners.findings import load_findings
from scripts import compare_against as cmp

CLICK_HERE = "F-before-home-el0210-2.4.4"
AFTER_LOGO = "F-after-home-el0062-4.1.2"


def kind(prompt: str) -> str:
    if "FAILS the WCAG" in prompt:
        return "for"
    return "v2" if "Your only job" in prompt else "v1"


class FakeBedrock:
    """strengths: {(kind, sc): strength}; bad: {(kind, sc): "invent" | "garbage"}."""

    def __init__(self, strengths, bad=None):
        self.strengths, self.bad, self.prompts = strengths, bad or {}, []

    def converse(self, modelId, messages, inferenceConfig):
        prompt = messages[0]["content"][0]["text"]
        self.prompts.append(prompt)
        sc = re.search(r"SUCCESS CRITERION: (\S+)", prompt).group(1)
        html = re.search(r"^HTML: (.*)$", prompt, re.M).group(1)
        key = (kind(prompt), sc)
        if self.bad.get(key) == "garbage":
            text = "I can't help with that."
        else:
            quote = "this text is not in the evidence" if self.bad.get(key) == "invent" else html[:30]
            text = json.dumps({"argument": "Specific argument.", "cited_sc": sc,
                               "quoted_evidence": [quote], "strength": self.strengths.get(key, 2)})
        return {"output": {"message": {"content": [{"text": text}]}},
                "usage": {"inputTokens": 1000, "outputTokens": 300}}


@pytest.fixture
def findings():
    return {f.finding_id: f.model_dump() for v in ("before", "after") for f in load_findings(v, "home")}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(evidence, "_calls_made", 0)
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 20)


def test_v2_prompt_fills_cleanly_and_keeps_the_contract(findings):
    prompt = evidence.fill_prompt(evidence.load_prompt(cmp.NEW), findings[CLICK_HERE])
    assert "does NOT fail" in prompt  # tests/test_evidence.py checks this phrase on the live prompt
    assert "{" + "sc}" not in prompt and "{context_text}" not in prompt
    assert "Killer bees" in prompt and "Never argue the other side" in prompt
    for field in ('"argument"', '"cited_sc"', '"quoted_evidence"', '"strength"'):
        assert field in prompt  # same JSON shape, so parse_side needs no change


def test_for_runs_once_and_both_againsts_see_the_same_evidence(findings):
    client = FakeBedrock({("for", "2.4.4"): 4, ("v1", "2.4.4"): 1, ("v2", "2.4.4"): 1})
    cmp.compare_one(client, findings[CLICK_HERE], "fail")
    assert [kind(p) for p in client.prompts] == ["for", "v1", "v2"]
    v1, v2 = client.prompts[1], client.prompts[2]
    block = lambda p: p[p.index("SUCCESS CRITERION:"):p.index("Return JSON")]  # noqa: E731
    assert block(v1) == block(v2)  # identical criterion and evidence; only the instructions differ


def test_known_fail_that_stays_likely_fail_is_no_worse(findings):
    client = FakeBedrock({("for", "2.4.4"): 4, ("v1", "2.4.4"): 1, ("v2", "2.4.4"): 1})
    r = cmp.compare_one(client, findings[CLICK_HERE], "fail")
    assert r["versions"][cmp.NEW]["recommendation"] == "likely_fail"
    assert r["versions"][cmp.NEW]["citations_verified"] is True and r["no_worse"]


def test_known_fail_drifting_to_unsure_is_worse(findings):
    client = FakeBedrock({("for", "2.4.4"): 4, ("v1", "2.4.4"): 1, ("v2", "2.4.4"): 3})
    r = cmp.compare_one(client, findings[CLICK_HERE], "fail")
    assert not r["no_worse"] and "known fail moved towards pass" in r["problems"][0]


def test_known_pass_drifting_towards_fail_is_worse_and_towards_pass_is_fine(findings):
    worse = cmp.compare_one(FakeBedrock({("for", "4.1.2"): 3, ("v1", "4.1.2"): 2, ("v2", "4.1.2"): 1}),
                            findings[AFTER_LOGO], "pass")
    assert worse["versions"][cmp.NEW]["recommendation"] == "likely_fail" and not worse["no_worse"]
    better = cmp.compare_one(FakeBedrock({("for", "4.1.2"): 3, ("v1", "4.1.2"): 2, ("v2", "4.1.2"): 5}),
                             findings[AFTER_LOGO], "pass")
    assert better["versions"][cmp.NEW]["recommendation"] == "likely_pass" and better["no_worse"]


def test_no_known_answer_means_no_direction_check(findings):
    fid = "F-before-home-el0231-1.3.1"
    r = cmp.compare_one(FakeBedrock({("for", "1.3.1"): 2, ("v1", "1.3.1"): 2, ("v2", "1.3.1"): 5}),
                        findings[fid], None)
    assert r["versions"][cmp.NEW]["recommendation"] == "likely_pass" and r["no_worse"]


def test_lost_citation_verification_is_worse(findings):
    client = FakeBedrock({("for", "2.4.4"): 4, ("v1", "2.4.4"): 1, ("v2", "2.4.4"): 1},
                         bad={("v2", "2.4.4"): "invent"})
    r = cmp.compare_one(client, findings[CLICK_HERE], "fail")
    assert r["versions"][cmp.OLD]["citations_verified"] and not r["versions"][cmp.NEW]["citations_verified"]
    assert not r["no_worse"] and "citations verified" in r["problems"][0]


def test_unusable_v2_reply_is_worse(findings):
    client = FakeBedrock({("for", "2.4.4"): 4, ("v1", "2.4.4"): 1}, bad={("v2", "2.4.4"): "garbage"})
    r = cmp.compare_one(client, findings[CLICK_HERE], "fail")
    assert not r["no_worse"] and "new AGAINST reply unusable" in r["problems"][0]


def test_expected_outcome_uses_the_ground_truth():
    assert cmp.expected_outcome("after", AFTER_LOGO, set()) == "pass"
    assert cmp.expected_outcome("before", CLICK_HERE, {CLICK_HERE}) == "fail"
    assert cmp.expected_outcome("before", "F-before-home-el0231-1.3.1", {CLICK_HERE}) is None


def test_cap_too_low_refuses_before_any_call(monkeypatch, capsys):
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 11)
    monkeypatch.setattr(cmp, "_client", lambda: pytest.fail("must not build a client"))
    assert cmp.main([]) == 1
    assert "ADJ_MAX_BEDROCK_CALLS=12" in capsys.readouterr().err


def test_full_run_writes_a_report_and_a_verdict(monkeypatch, tmp_path, capsys):
    # v2 a little firmer on the After logo, otherwise identical: no worse everywhere
    strengths = {("for", "2.4.4"): 4, ("v1", "2.4.4"): 1, ("v2", "2.4.4"): 1,
                 ("for", "4.1.2"): 3, ("v1", "4.1.2"): 2, ("v2", "4.1.2"): 4}
    client = FakeBedrock(strengths)
    monkeypatch.setattr(cmp, "_client", lambda: client)
    monkeypatch.setattr(cmp, "REPORT", tmp_path / "against_v2_comparison.md")
    monkeypatch.setattr(cmp, "REPO", tmp_path)
    assert cmp.main(["--write"]) == 0
    assert len(client.prompts) == cmp.CALLS_NEEDED == 12
    text = (tmp_path / "against_v2_comparison.md").read_text()
    assert "no worse on all 4" in text and "PROMPT_AGAINST" in text
    assert text.count("**AGAINST, `evidence_against_v2`**") == 4
    assert "| `F-after-home-el0062-4.1.2` | pass | unsure -> unsure |" in text


def test_a_worse_finding_says_keep_v1(monkeypatch, tmp_path):
    client = FakeBedrock({("for", "2.4.4"): 4, ("v1", "2.4.4"): 1, ("v2", "2.4.4"): 4})
    monkeypatch.setattr(cmp, "_client", lambda: client)
    monkeypatch.setattr(cmp, "REPORT", tmp_path / "r.md")
    monkeypatch.setattr(cmp, "REPO", tmp_path)
    assert cmp.main(["--write"]) == 0
    assert "Keep `evidence_against_v1`" in (tmp_path / "r.md").read_text()


def test_a_bedrock_error_writes_nothing(monkeypatch, tmp_path, capsys):
    class Broken:
        def converse(self, **kw):
            raise ConnectionError("proxy refused")
    monkeypatch.setattr(cmp, "_client", lambda: Broken())
    monkeypatch.setattr(cmp, "REPORT", tmp_path / "r.md")
    assert cmp.main(["--write"]) == 1
    assert not (tmp_path / "r.md").exists() and "proxy refused" in capsys.readouterr().err


def test_live_pipeline_still_uses_v1():
    """Switching is a separate, human-approved change after reading the comparison."""
    assert evidence.PROMPT_AGAINST == cmp.OLD
