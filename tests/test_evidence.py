"""Track C: FOR/AGAINST evidence agents. A fake client stands in for Bedrock; no network."""

import json
from pathlib import Path

import pytest

import settings
from agents import evidence

REPO = Path(__file__).resolve().parent.parent
CLICK_HERE = "F-before-home-el0210-2.4.4"


@pytest.fixture
def finding():
    data = json.loads((REPO / "data" / "findings" / "before_home.json").read_text())
    return next(f for f in data if f["finding_id"] == CLICK_HERE)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(evidence, "_calls_made", 0)
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 20)
    monkeypatch.setattr(settings, "EVIDENCE_ONLY", frozenset())


class FakeClient:
    """Answers FOR then AGAINST with canned replies and records the prompts it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def converse(self, modelId, messages, inferenceConfig):
        self.prompts.append(messages[0]["content"][0]["text"])
        return {"output": {"message": {"content": [{"text": self.replies.pop(0)}]}},
                "usage": {"inputTokens": 100, "outputTokens": 40}}


def reply(strength, quotes, sc="2.4.4", argument="Specific argument."):
    return json.dumps({"argument": argument, "cited_sc": sc, "quoted_evidence": quotes, "strength": strength})


def test_two_separate_calls_with_opposite_briefs(finding):
    client = FakeClient(reply(4, ["Click here"]), reply(2, ["Killer bees. Click here."]))
    evidence.build_cases(finding, client=client)
    for_prompt, against_prompt = client.prompts
    assert "FAILS" in for_prompt and "does NOT fail" in against_prompt
    assert "Killer bees" in for_prompt and "Killer bees" in against_prompt  # context reaches both
    assert "Specific argument" not in against_prompt  # AGAINST never sees FOR's output
    assert "{sc}" not in for_prompt and "{context_text}" not in against_prompt


def test_full_result_shape(finding):
    client = FakeClient(reply(4, ["Click here"]), reply(2, ["Killer bees. Click here."]))
    ai = evidence.build_cases(finding, client=client)["ai"]
    assert ai["recommendation"] == "likely_fail"
    assert ai["citations_verified"] is True
    assert ai["model_id"] == settings.MODEL_ID
    assert ai["prompt_version"] == "evidence_for_v1+evidence_against_v1"
    assert ai["usage"] == {"input_tokens": 200, "output_tokens": 80}
    assert ai["confidence"] is None
    assert ai["case_for"] != ai["case_against"]


@pytest.mark.parametrize(("f", "a", "want"), [(5, 3, "likely_fail"), (3, 5, "likely_pass"),
                                              (4, 3, "unsure"), (3, 3, "unsure")])
def test_recommendation_thresholds(f, a, want):
    assert evidence.recommend(f, a) == want


def test_citation_check_rejects_wrong_criterion(finding):
    assert not evidence.citations_ok({"cited_sc": "1.1.1", "quoted_evidence": ["Click here"]}, finding)


def test_citation_check_rejects_invented_quote(finding):
    assert not evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": ["Learn about killer bees"]}, finding)


def test_citation_check_rejects_no_quotes(finding):
    assert not evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": []}, finding)


def test_citation_check_accepts_html_context_and_scanner_message(finding):
    quotes = ['href="../offsite.html"', "Killer   bees.  Click here.", "identifies the purpose of the link"]
    assert evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": quotes}, finding)


@pytest.mark.parametrize("trivial", ["a", "href", "Click", "  bees "])
def test_citation_check_rejects_trivial_quotes(finding, trivial):
    """Fragments this short appear in almost any evidence, so they prove nothing."""
    assert not evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": ["Click here", trivial]}, finding)


def test_citation_check_ignores_the_selector(finding):
    selector_part = finding["evidence"]["selector"].split(" > ")[0]  # "#content", not in the HTML
    assert selector_part not in finding["evidence"]["html_snippet"]
    assert not evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": ["Click here", selector_part]},
                                     finding)


def test_citation_check_needs_one_quote_from_the_page(finding):
    scanner_only = ["identifies the purpose of the link"]
    assert not evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": scanner_only}, finding)


def test_citation_check_rejects_non_text_quotes(finding):
    assert not evidence.citations_ok({"cited_sc": "2.4.4", "quoted_evidence": ["Click here", 42]}, finding)


# The real Week 3 Bedrock replies (docs/evidence_samples.md) must still verify under the stricter check.
RECORDED = [
    ("before_home", "F-before-home-el0210-2.4.4",
     ["Click here", "Killer bees. Click here. Onions. Click here.",
      "Check that the link text combined with programmatically determined link context identifies the "
      "purpose of the link"],
     ["Click here", "Killer bees. Click here. Onions. Click here."]),
    ("before_home", "F-before-home-el0194-2.1.1",
     ["onmouseover=\"this.src='./img/morearrow_a.gif'\"", "onmouseout=\"this.src='./img/morearrow.gif'\""],
     ["onmouseover=\"this.src='./img/morearrow_a.gif'\"", "onmouseout=\"this.src='./img/morearrow.gif'\"",
      'alt=""']),
    ("before_home", "F-before-home-el0231-1.3.1",
     ["<b>Free Penguins</b>"], ["<b>Free Penguins</b>", "Free Penguins"]),
    ("after_home", "F-after-home-el0062-4.1.2",
     ['<a><img src="./img/toplogo.png" alt="Citylights: your access to the city."></a>',
      "Anchor element found with link content, but no href, ID or name attribute has been supplied."],
     ["no href, ID or name attribute has been supplied", 'alt="Citylights: your access to the city."']),
]


@pytest.mark.parametrize(("page", "fid", "for_quotes", "against_quotes"), RECORDED, ids=[r[1] for r in RECORDED])
def test_recorded_week3_cases_still_verify(page, fid, for_quotes, against_quotes):
    data = json.loads((REPO / "data" / "findings" / f"{page}.json").read_text())
    f = next(x for x in data if x["finding_id"] == fid)
    sc = f["wcag"][0]
    assert evidence.citations_ok({"cited_sc": sc, "quoted_evidence": for_quotes}, f)
    assert evidence.citations_ok({"cited_sc": sc, "quoted_evidence": against_quotes}, f)


def test_one_bad_quote_fails_verification(finding):
    client = FakeClient(reply(4, ["Click here"]), reply(2, ["text that is not on the page"]))
    assert evidence.build_cases(finding, client=client)["ai"]["citations_verified"] is False


def test_unusable_reply_raises(finding):
    with pytest.raises(evidence.EvidenceError):
        evidence.build_cases(finding, client=FakeClient("I think it fails.", reply(2, ["Click here"])))


def test_strength_out_of_range_raises():
    with pytest.raises(evidence.EvidenceError):
        evidence.parse_side(reply(9, ["Click here"]))


def test_call_cap_skips_without_calling(finding, monkeypatch):
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 1)
    client = FakeClient()
    ai = evidence.build_cases(finding, client=client)["ai"]
    assert client.prompts == [] and ai["case_for"] == "SKIPPED: call cap reached"
    assert ai["recommendation"] is None and ai["citations_verified"] is False  # not evaluated, not "unsure"


def test_evidence_only_skips_unselected(finding, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_ONLY", frozenset({"F-some-other-finding"}))
    client = FakeClient()
    ai = evidence.build_cases(finding, client=client)["ai"]
    assert client.prompts == [] and ai["case_for"].startswith("SKIPPED: not selected")
    assert ai["recommendation"] is None and not evidence.is_issue(ai)


def test_evidence_only_runs_selected(finding, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_ONLY", frozenset({CLICK_HERE}))
    client = FakeClient(reply(3, ["Click here"]), reply(3, ["Killer bees. Click here."]))
    assert evidence.build_cases(finding, client=client)["ai"]["recommendation"] == "unsure"
    assert len(client.prompts) == 2


def test_real_client_is_blocked_in_tests(finding):
    with pytest.raises(RuntimeError, match="must not call Bedrock"):
        evidence.build_cases(finding)


@pytest.mark.parametrize("raw", [4, 4.0, "4", " 4 "])
def test_strength_accepts_whole_numbers_in_any_form(raw):
    side = evidence.parse_side(json.dumps({"argument": "a", "cited_sc": "2.4.4",
                                           "quoted_evidence": ["Click here"], "strength": raw}))
    assert side["strength"] == 4


@pytest.mark.parametrize("raw", [0, 6, 4.5, "four", True, None])
def test_strength_rejects_anything_else(raw):
    with pytest.raises(evidence.EvidenceError, match="strength"):
        evidence.parse_side(json.dumps({"argument": "a", "cited_sc": "2.4.4",
                                        "quoted_evidence": ["Click here"], "strength": raw}))


def test_reply_with_text_and_braces_around_the_json():
    text = ('Sure! {not json} Here it is: {"argument": "uses {braces}", "cited_sc": "2.4.4", '
            '"quoted_evidence": ["Click here"], "strength": 2} Hope that helps {:)}')
    side = evidence.parse_side(text)
    assert side["argument"] == "uses {braces}" and side["strength"] == 2


@pytest.mark.parametrize("recommendation,verified,issue", [
    ("likely_fail", True, True),
    ("likely_fail", False, False),   # unverified citations: flagged for a human, never an issue
    ("likely_fail", None, False),
    ("likely_pass", True, False),
    ("unsure", True, False),
    (None, False, False),            # skipped: not evaluated
])
def test_issue_rule(recommendation, verified, issue):
    assert evidence.is_issue({"recommendation": recommendation, "citations_verified": verified}) is issue


def test_skipped_result_passes_the_shared_loader(finding, monkeypatch):
    from scanners.findings import validate_findings
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 0)
    ai = evidence.build_cases(finding)["ai"]  # no client needed: skipped before any call
    merged = {**finding, "ai": {**finding["ai"], **ai}}
    assert validate_findings([merged])[0].ai.recommendation is None
