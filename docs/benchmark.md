# ADJ benchmark

How well ADJ finds the accessibility issues W3C documents on its Before and After demo site,
how we measured it, what a run costs, and where the numbers stop. Every figure here comes from
a file in this repo, named next to it, so anyone can re-run it (see "Reproduce" at the end).

> **Status, Oct 23: baseline.** These are the numbers with FOR/AGAINST switched off, so every grey
> finding counts as "flagged for a human". Recall (b), coverage (c) and the ground truth are final
> for this code. Recall (a) and the After false alarms are **lower bounds**. The full FOR/AGAINST
> run (Wed Oct 28, after the new checks and the regenerated findings merge) replaces the Results
> and Cost sections; the Method section does not change.

## At a glance

Source: [`docs/scores/2026-10-23.md`](scores/2026-10-23.md), commit `5df8d76`, all 8 pages.

| Measure | Value today | Target | Note |
| --- | --- | --- | --- |
| Before recall (a): reported as an issue | 23.1% (30 of 130) | 90% | Lower bound: FOR/AGAINST off |
| Before recall (b): reported or sent to a human | **46.9% (61 of 130)** | 90% | The scanners never touch 69 rows; new checks target them |
| Criterion coverage (c), detected | **48.1% (37 of 77)** | n/a | Page and criterion pairs |
| After false alarms | 0 | near 0 | Lower bound: no grey finding can be an issue yet |
| Mapper spot-check | 10 of 10 (16 of 17 on every before/home row) | 8 of 10 | [`docs/mapper_spotcheck.md`](mapper_spotcheck.md) |
| Cost of a full run (8 pages) | about $8 (estimate) | under $25 a month | Measured on Oct 28 |

## Method

### Test bed

- **Site:** W3C's [Before and After Demonstration](https://www.w3.org/WAI/demos/bad/) (CityLights):
  four Before pages (home, news, tickets, survey) built with known failures, and four After
  pages, the same content repaired.
- **Standard:** Section 508 (Revised 508 E205.4), which adopts WCAG 2.0 Level A and AA: 38
  success criteria. WCAG 2.1, 2.2 and AAA results are dropped at merge (`scanners/wcag.py`).
- **Scope:** the page content inside `#page`. W3C's demo wrapper (`#meta-header`,
  `#meta-footer`) is filtered out, because it isn't part of the site under test.

### Pipeline

Capture each page (Playwright), scan it with axe-core and Pa11y, and merge the results into one
finding per element and criterion, each with its selector, HTML, surrounding text and a
screenshot (`scanners/merge.py`). Then:

1. **Mapper** (`agents/mapper.py`) looks the criterion up in the WCAG 2.0 corpus.
2. **Router** (`agents/router.py`, policy v1) sends each finding to `machine` (the scanner
   reported a violation), `human-only` (a page-level "check this manually" notice, or a
   criterion the corpus marks as always needing a human), or `grey` (everything else).
3. **FOR/AGAINST** (`agents/evidence.py`): for each grey finding, two separate Claude calls
   argue that it fails and that it passes, from the same evidence. Code then checks every
   quoted citation against that evidence and sets a recommendation from the two strengths.
4. **PULSE**, the reviewer panel, is where a signed-in human records the decision. AI never
   sets a conformance status.

### Ground truth

`data/ground-truth/ground_truth.csv`, built by Ershad from W3C's evaluation reports for the four
Before pages (PR #23, Oct 20; revised Oct 22).

- **130 rows**, one per failure cause per page: home 33, news 33, tickets 31, survey 33.
- Each row names W3C's report entry (`w3c_ref`), where it is on the page, and the matching
  finding's ID, or `missed` when no scanner finding is that issue. Today: **61 matched, 69
  missed.**
- **A generic reminder is not a match** (decision 3, Oct 17). Pa11y emits page-level "check
  this manually" notices for the same criteria on every page. A row whose only candidate is one
  of those is `missed`.
- **Cross-criterion matches are allowed** when the element is right but the scanner filed it
  under another criterion (e.g. W3C's 3.3.2 "no label" matched to our 4.1.2 finding on the
  same field). There are 8; the score report lists them.
- **Checked:** 12 rows (3 per page, 6 matched and 6 missed) were re-checked against W3C's report
  and the served page, and recorded in the `verified_by` column. **All 12 held, unchanged.**
  Separately, Ershad's analysis of the missed rows ([`docs/missed_issues.md`](missed_issues.md))
  found two `missed` rows that an existing finding already covers (home and tickets 1.1.1); both
  were corrected to matches.
- **12 rows aren't on the served page.** W3C's reports repeat some failures across pages, and
  for these 12 rows (news 3, tickets 4, survey 5) nothing in the page source or the rendered page
  matches. They stay in the ground truth (decision, Oct 21), so **the headline is out of all
  130**. The figure "out of 118 on the served page" is shown as secondary.

### Scoring definitions

`scripts/score.py` produces every figure from the ground truth and one run per page. Its module
docstring holds the full definitions; in short:

**The issue rule** (decided Oct 17; one implementation, `agents.evidence.is_issue`). A finding is
**reported as an issue** if its bucket is `machine`, or it is `grey` with
`ai.recommendation == "likely_fail"` and `ai.citations_verified` true. Every other finding that
surfaced (an unverified `likely_fail`, `likely_pass`, `unsure`, not evaluated, or `human-only`)
is **flagged for a human**. The rule requires verified citations because every ACR statement
must link to evidence, and an unverified citation is exactly the case where it doesn't.

**Before recall**, out of the ground-truth rows on the pages scored:

$$
\text{(a)} = \frac{\text{rows whose finding is reported as an issue}}{\text{ground-truth rows}}
\qquad
\text{(b)} = \frac{\text{rows whose finding is reported or flagged}}{\text{ground-truth rows}}
$$

A `missed` row counts in the denominator and never in the numerator. (b) answers "did ADJ put
the issue in front of someone?"; (a) answers "did ADJ call it an issue by itself?".

**Criterion coverage**, out of the distinct (page, criterion) pairs in the ground truth (77):

$$
\text{(c)} = \frac{\text{pairs with a surfaced finding that is not a page-level notice}}{\text{page and criterion pairs}}
$$

(c+) is the same, with the page-level "check this manually" notices counted too. It is shown
only for comparison, because a reminder that fires on every page is not a detection. A
page-level **violation** (axe's missing `lang`, 3.1.1) is a real detection and counts in (c).
Coverage doesn't check that the element is the right one; it is the most generous figure.

**After false alarms:** findings on the four After pages that are reported as issues. W3C's After
reports pass every Level A and AA criterion there, so each one is a false alarm.

**A page with no run** shows "not run", never zero, and is left out of every ratio.

## Results (baseline, Oct 23, FOR/AGAINST off)

Source: [`docs/scores/2026-10-23.md`](scores/2026-10-23.md). Runs `20261023-210115-71a7` to
`20261023-210121-584d`, one per page, `ADJ_MAX_BEDROCK_CALLS=0`.

### Recall

| Figure | All 130 rows (headline) | 118 rows on the served page (secondary) |
| --- | --- | --- |
| (a) Reported as an issue | 23.1% (30) | 25.4% (30) |
| (b) Reported or flagged for a human | 46.9% (61) | 51.7% (61) |

| Coverage | Value |
| --- | --- |
| (c) Criterion coverage, detected | 48.1% (37 of 77) |
| (c+) Including page-level "check manually" reminders | 84.4% (65 of 77) |

The gap between (c) and (c+) is 28 pairs: seven criteria (1.3.2, 1.4.1, 1.4.5, 2.4.1, 2.4.6,
2.4.7, 3.2.4) that the scanners reach only through a generic reminder, on all four pages.

| Page | Rows | missed | (a) | (b) | (c) |
| --- | --- | --- | --- | --- | --- |
| home | 33 | 15 | 24.2% (8) | 54.5% (18) | 47.4% (9 of 19) |
| news | 33 | 19 | 21.2% (7) | 42.4% (14) | 47.4% (9 of 19) |
| tickets | 31 | 16 | 25.8% (8) | 48.4% (15) | 47.4% (9 of 19) |
| survey | 33 | 19 | 21.2% (7) | 42.4% (14) | 50.0% (10 of 20) |

### After false alarms

0 on all four After pages. This is a lower bound: with FOR/AGAINST off, the 300 grey findings on
the After site can't be issues. The full run measures the real number (Week 4 target: at most 10).

### Recall by criterion

| Criterion | Rows | missed | (a) | (b) |
| --- | --- | --- | --- | --- |
| 1.1.1 Non-text content | 24 | 9 | 9 | 15 |
| 1.3.1 Info and relationships | 11 | 3 | 0 | 8 |
| 1.3.2 Meaningful sequence | 7 | 7 | 0 | 0 |
| 1.4.1 Use of color | 5 | 5 | 0 | 0 |
| 1.4.3 Contrast (minimum) | 7 | 2 | 1 | 5 |
| 1.4.5 Images of text | 4 | 4 | 0 | 0 |
| 2.1.1 Keyboard | 8 | 4 | 0 | 4 |
| 2.4.1 Bypass blocks | 4 | 4 | 0 | 0 |
| 2.4.2 Page titled | 4 | 0 | 0 | 4 |
| 2.4.3 Focus order | 1 | 1 | 0 | 0 |
| 2.4.4 Link purpose (in context) | 7 | 2 | 4 | 5 |
| 2.4.6 Headings and labels | 4 | 4 | 0 | 0 |
| 2.4.7 Focus visible | 8 | 8 | 0 | 0 |
| 3.1.1 Language of page | 4 | 0 | 4 | 4 |
| 3.2.1 On focus | 4 | 4 | 0 | 0 |
| 3.2.2 On input | 4 | 0 | 0 | 4 |
| 3.2.4 Consistent identification | 4 | 4 | 0 | 0 |
| 3.3.2 Labels or instructions | 4 | 0 | 4 | 4 |
| 4.1.1 Parsing | 4 | 4 | 0 | 0 |
| 4.1.2 Name, role, value | 12 | 4 | 8 | 8 |
| **Total** | **130** | **69** | **30** | **61** |

### Where the 69 missed rows are, and what is meant to catch them

From [`docs/missed_issues.md`](missed_issues.md) (Ershad, Oct 20), which names the exact element
or CSS rule for every missed row. Week 4 adds checks for the first four groups as a new source,
`adj-rules` / `adj-vision`, in the same finding format.

| Group | Rows | Week 4 work | Caught after Week 4 |
| --- | --- | --- | --- |
| Code rule on the HTML or CSS (largest: links that call `blur()` on focus, 20 rows) | 36 | Daniel: code rules | Measured Oct 28 |
| HTML validator (4.1.1 parsing) | 4 | Ershad: validator | Measured Oct 28 |
| Comparing pages (3.2.4) | 4 | Ershad: page comparison | Measured Oct 28 |
| AI on the screenshot (images of text, reading order, color-only links) | 13 | Jesus: vision spike (cut-list item 1) | Measured Oct 28 |
| No instance on the served page | 12 | None: counted against us in the headline | n/a |
| **Total** | **69** | | |

If every check in the first three groups lands and works, (b) could reach about 81%
(61 + 36 + 4 + 4 = 105 of 130). That's a ceiling for planning, not a result.

## Cost and speed

Model: `us.anthropic.claude-sonnet-4-6` on Amazon Bedrock, temperature 0 (`settings.py`). Only
FOR/AGAINST calls Bedrock: two calls per grey finding. Everything else runs locally.

| | Before (4 pages) | After (4 pages) | All 8 pages |
| --- | --- | --- | --- |
| Grey findings | 478 | 300 | 778 |
| Bedrock calls | 956 | 600 | 1,556 |

**Estimate, until Oct 28.** The four real FOR/AGAINST samples in
[`docs/evidence_samples.md`](evidence_samples.md) used about 1,190 input and 368 output tokens per
finding (both calls together). At the US cross-region list price of $3.30 per million input and
$16.50 per million output tokens (third-party listing of Bedrock pricing, checked Oct 23):

$$
778 \times \left( \frac{1190 \times 3.30}{10^{6}} + \frac{368 \times 16.50}{10^{6}} \right) \approx \$7.80
$$

That is about 1 cent per grey finding: roughly $4.80 for the Before pages and $3.00 for After.
Four samples is a small base; findings with long HTML will use more input tokens. The real
figure comes from the run's `summary.json` token totals and the AWS Billing console.

| Measured on the full run (Oct 28) | Value |
| --- | --- |
| Input / output tokens, all 8 pages | Pending |
| Bedrock cost (AWS Billing) | Pending |
| Wall-clock time per page | Pending. Without AI the pipeline takes about 1 second a page, so the run time is almost all Bedrock calls |
| Calls that failed or were throttled | Pending |

## Limits, stated honestly

- **(a) vs (b).** (b) counts an issue as caught when ADJ sends it to a human, even if the AI
  leaned towards pass. That's deliberate (the brief asks ADJ to know when human judgment is
  needed), but (a) is the figure for "ADJ found it by itself". We report both, every time.
- **Coverage is generous.** (c) checks the criterion, not the element; (a) and (b) check the
  element.
- **What still needs a human.** Every conformance status in the ACR comes from a human decision
  in PULSE. Criteria like 2.4.3 (focus order) and 3.2.4 (consistent identification) are routed
  to a human by design.
- **The mapper spot-check measures the scanners too.** The mapper copies the criterion the
  scanner assigned and looks it up, so 10 of 10 says the scanners' assignment agrees with W3C on
  that sample. Where it doesn't (the QUICKMENU select, filed under 3.2.1 by Pa11y and under 3.2.2
  by W3C), only the human review step can correct it.
- **One test site.** CityLights is built to fail in known ways. Results on a real agency site
  will differ; ORAP and GAT would be the next test beds.
- **The ground truth is ours.** It follows W3C's reports closely and 12 rows were re-checked, but
  matching a finding to a W3C row involves judgment (cross-criterion matches, the 12 rows not on
  the served page). Both are listed in the score report so a reader can disagree row by row.

## Triage study

Whether ADJ makes a reviewer at least 30% faster than manual review (the brief's stretch goal).
Protocol: `docs/triage_study.md` (Ershad, Week 4). Results: added after the timed team review,
Oct 31 to Nov 1.

## Reproduce

From the repo root, with the venv active (setup: `claude/runbook-adj-508.md` in the project):

```bash
for v in before after; do for p in home news tickets survey; do
  ADJ_MAX_BEDROCK_CALLS=0 python -m agents.graph --version $v --page $p
done; done
python -m scripts.score
```

For the full run, raise `ADJ_MAX_BEDROCK_CALLS` above 2 calls per grey finding (e.g. 400 per
page) and add `--write` to save the dated report.

## Changes to this page

| Date | Change |
| --- | --- |
| Oct 23 | Method, baseline results (FOR/AGAINST off), cost estimate |
| Oct 28 | Planned: full FOR/AGAINST run with the new checks; real cost and runtime |
