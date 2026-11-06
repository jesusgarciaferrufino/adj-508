# ADJ benchmark

How well ADJ finds the accessibility issues W3C documents on its Before and After demo site,
how we measured it, what a run costs, and where the numbers stop. Every figure here comes from
a file in this repo, named next to it, so anyone can re-run it (see "Reproduce" at the end).

> **Status, Nov 4: final.** Full FOR/AGAINST run on the frozen code (commit `e61565a`), all 8
> pages, scored against the 130-row ground truth.

## At a glance

Source: [`docs/scores/2026-11-04.md`](scores/2026-11-04.md), commit `e61565a`, runs `20261104-*`.

| Measure | Nov 4 (final) | Target |
| --- | --- | --- |
| Before recall (a): reported as an issue, of 130 | 59.2% (77) | 90% |
| Before recall (a), served page only, of 118 | 65.3% (77) | 90% |
| Before recall (b): reported or sent to a human, of 130 | **89.2% (116)** | 90% |
| Before recall (b), served page only, of 118 | **98.3% (116)** | 90% |
| Criterion coverage (c), detected | 96.1% (74 of 77) | n/a |
| After false alarms | **0** (exact: every After grey finding was evaluated) | near 0 |
| Grey findings evaluated by FOR/AGAINST | 781 of 784 | all |
| Cost of the full run (8 pages) | 1,220,955 input + 274,508 output tokens: **$7.78** at list price | under $25 a month |
| Mapper spot-check | 10 of 10 (16 of 17 on every before/home row) | 8 of 10 |

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

Capture each page (Playwright), then find candidate issues with five sources:

- **axe-core** and **Pa11y** (HTML_CodeSniffer), the two standard scanners;
- **ADJ code rules** (`scanners/rules_scan.py`, Daniel): 12 checks on the page's HTML and CSS that
  the scanners don't make, such as links that throw away focus with `blur()`, missing skip links,
  layout tables that don't make sense linearized, and images of text;
- **an HTML validator** (`scanners/validator_scan.py`, Ershad) for 4.1.1 parsing errors;
- **a page comparison** (`scanners/compare_scan.py`, Ershad) for 3.2.4, components that repeat
  across pages but are identified differently.

`scanners/merge.py` merges them into one finding per element and criterion, each with its
selector, HTML, surrounding text and a screenshot. Then:

1. **Mapper** (`agents/mapper.py`) looks the criterion up in the WCAG 2.0 corpus.
2. **Router** (`agents/router.py`, policy v2) sends each finding to `machine` (a scanner or code
   rule reported a certain violation), `human-only` (a page-level "check this manually" notice, a
   scanner check known to be unreliable for its criterion, a criterion that needs interaction,
   or a criterion the corpus marks as always needing a human), or `grey` (everything else).
3. **FOR/AGAINST** (`agents/evidence.py`): for each grey finding, two separate Claude calls
   argue that it fails and that it passes, from the same evidence. Code then checks every
   quoted citation against that evidence and sets a recommendation from the two strengths.
4. **PULSE**, the reviewer panel, is where a signed-in human records the decision. AI never
   sets a conformance level. The process is in [`docs/human_review.md`](human_review.md).

### Ground truth

`data/ground-truth/ground_truth.csv`, built by Ershad from W3C's evaluation reports for the four
Before pages (PR #23, Oct 20), and re-pointed at the new checks' findings as they landed (#44,
#46, #56).

- **130 rows**, one per failure cause per page: home 33, news 33, tickets 31, survey 33.
- Each row names W3C's report entry (`w3c_ref`), where it is on the page, and the matching
  finding's ID, or `missed` when no finding is that issue. Final: **116 matched, 14 missed.**
- **A generic reminder is not a match** (decision 3, Oct 17). Pa11y emits page-level "check
  this manually" notices for the same criteria on every page. A row whose only candidate is one
  of those is `missed`.
- **Cross-criterion matches are allowed** when the element is right but the scanner filed it
  under another criterion. In the final run there are none; the score report lists any.
- **Checked:** 12 rows (3 per page, 6 matched and 6 missed at the time) were re-checked against
  W3C's report and the served page, and recorded in the `verified_by` column. **All 12 held,
  unchanged.** Separately, Ershad's analysis of the missed rows
  ([`docs/missed_issues.md`](missed_issues.md)) found two `missed` rows that an existing finding
  already covered; both were corrected to matches.
- **12 rows aren't on the served page.** W3C's reports repeat some failures across pages, and
  for these 12 rows (news 3, tickets 4, survey 5) nothing in the page source or the rendered page
  matches. They stay in the ground truth (decision, Oct 21), so **the headline is out of all
  130**. The figure "out of 118 on the served page" is shown next to it, never instead of it.

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

## Results (final, Nov 4)

Source: [`docs/scores/2026-11-04.md`](scores/2026-11-04.md). One run per page, `20261104-201006-c7db`
(before/home) to `20261104-223514-c3a3` (after/survey), FOR/AGAINST on every grey finding.

### Recall

| Figure | All 130 rows (headline) | 118 rows on the served page |
| --- | --- | --- |
| (a) Reported as an issue | 59.2% (77) | 65.3% (77) |
| (b) Reported or flagged for a human | 89.2% (116) | 98.3% (116) |

| Coverage | Value |
| --- | --- |
| (c) Criterion coverage, detected | 96.1% (74 of 77) |
| (c+) Including page-level "check manually" reminders | 98.7% (76 of 77) |

| Page | Rows | missed | (a) | (b) | (c) |
| --- | --- | --- | --- | --- | --- |
| home | 33 | 0 | 60.6% (20) | 100.0% (33) | 100.0% (19 of 19) |
| news | 33 | 4 | 57.6% (19) | 87.9% (29) | 100.0% (19 of 19) |
| tickets | 31 | 4 | 64.5% (20) | 87.1% (27) | 94.7% (18 of 19) |
| survey | 33 | 6 | 54.5% (18) | 81.8% (27) | 90.0% (18 of 20) |

### After false alarms

**0 on all four After pages, and this is exact, not a lower bound:** every grey finding on the
After pages was evaluated by FOR/AGAINST. The 3 grey findings left unevaluated (out of 784) are all
on Before pages, one each on news, tickets and survey: AI replies that were malformed, most likely
cut off, and rejected by validation. They count as flagged for a human, so (a) could be at most 3
rows higher; it can't be lower.

### Router v2: from 38 After false alarms to 0

The first full AI run (Oct 29) had **38 After false alarms**. They traced to 4 repeatable scanner
patterns:

- 29 from one HTML_CodeSniffer check of the `headers` attribute on the After tickets table
  (H43.IncorrectAttr, 1.3.1), whose "expected" value is the tool's own guess at a complex
  table's header structure;
- 4 conditional 3.3.1 form reminders ("if an input error is automatically detected..."), which
  can only be judged by submitting the form and triggering an error;
- 1 anchor with no `href` (4.1.2), a placeholder rather than a link;
- 4 select-value warnings (4.1.2) on working selects whose first option is the default.

The AI leaned "likely fail" on all of them. Router v2 (#50) sends those patterns to a person
instead of the AI: a finding whose every scanner detail is one of these checks, or that is on
3.3.1, goes to `human-only`. The rules describe when a scanner warning can't be trusted, never
this site's content. A what-if on the Oct 29 run files predicted 38 to 0 with no recall lost, and
no Before ground-truth match moved (a test checks that on every run). **The Nov 4 run confirmed
0.**

### Recall by criterion

| Criterion | Rows | missed | (a) | (b) |
| --- | --- | --- | --- | --- |
| 1.1.1 Non-text content | 24 | 5 | 11 | 19 |
| 1.3.1 Info and relationships | 11 | 1 | 5 | 10 |
| 1.3.2 Meaningful sequence | 7 | 0 | 0 | 7 |
| 1.4.1 Use of color | 5 | 3 | 1 | 2 |
| 1.4.3 Contrast (minimum) | 7 | 2 | 1 | 5 |
| 1.4.5 Images of text | 4 | 0 | 2 | 4 |
| 2.1.1 Keyboard | 8 | 0 | 8 | 8 |
| 2.4.1 Bypass blocks | 4 | 0 | 4 | 4 |
| 2.4.2 Page titled | 4 | 0 | 0 | 4 |
| 2.4.3 Focus order | 1 | 1 | 0 | 0 |
| 2.4.4 Link purpose (in context) | 7 | 2 | 5 | 5 |
| 2.4.6 Headings and labels | 4 | 0 | 0 | 4 |
| 2.4.7 Focus visible | 8 | 0 | 8 | 8 |
| 3.1.1 Language of page | 4 | 0 | 4 | 4 |
| 3.2.1 On focus | 4 | 0 | 4 | 4 |
| 3.2.2 On input | 4 | 0 | 4 | 4 |
| 3.2.4 Consistent identification | 4 | 0 | 0 | 4 |
| 3.3.2 Labels or instructions | 4 | 0 | 4 | 4 |
| 4.1.1 Parsing | 4 | 0 | 4 | 4 |
| 4.1.2 Name, role, value | 12 | 0 | 12 | 12 |
| **Total** | **130** | **14** | **77** | **116** |

### The 14 missed rows

- **12 are not on the served page.** W3C's reports list them, but the page we fetch doesn't contain
  them (1.1.1 x5, 1.4.1 x3, 1.4.3 x2, 2.4.4 x2). They are the whole gap between 89.2% of 130 and
  98.3% of 118.
- **2 are real misses on the served page:**
  - news 1.3.1, structural markup used in a way that doesn't represent relationships;
  - survey 2.4.3, a menu or dialog not adjacent to its trigger in the focus order. The keyboard
    walkthrough (#48) tabs through every page and checks focus order against the layout, and it
    found 0 hits, so it doesn't catch this row.

## Progress

| Measure | Oct 23 (baseline, AI off) | Oct 29 (first AI run) | **Nov 4 (final)** |
| --- | --- | --- | --- |
| (a) reported as an issue, of 130 | 23.1% (30) | 53.8% (70) | **59.2% (77)** |
| (a) served page only, of 118 | 25.4% (30) | 59.3% (70) | **65.3% (77)** |
| (b) reported or flagged, of 130 | 46.9% (61) | 80.8% (105) | **89.2% (116)** |
| (b) served page only, of 118 | 51.7% (61) | 89.0% (105) | **98.3% (116)** |
| (c) criterion coverage, detected | 48.1% (37 of 77) | 85.7% (66 of 77) | **96.1% (74 of 77)** |
| After false alarms | 0 (AI off) | 38 | **0** |
| Grey findings evaluated by FOR/AGAINST | 0 | 799 of 801 | **781 of 784** |

Sources: [`2026-10-23.md`](scores/2026-10-23.md), [`2026-10-29.md`](scores/2026-10-29.md),
[`2026-11-04.md`](scores/2026-11-04.md). What moved the numbers:

- **Daniel's code rules** (#33, #52): checks the scanners don't make, from links that throw away
  focus with `blur()` (20 rows on their own) to layout tables and images of text.
- **Ershad's validator and page comparison** (#41, #42): 4.1.1 parsing errors and 3.2.4.
- **Ground truth re-pointed** at the new findings as each check landed (#44, #46, #56).
- **FOR/AGAINST on every grey finding** (#47): throttling waited out, so a full run finishes.
- **Router v2** (#50): four scanner warning patterns that a machine can't trust go to a person.
  This took the After false alarms from 38 to 0.
- **The `cited_sc` fix** (#51): an AI reply that leaves out the criterion it cites is kept but
  never counts as an issue, so it no longer goes unevaluated.

## Cost and speed

Model: `us.anthropic.claude-sonnet-4-6` on Amazon Bedrock, temperature 0 (`settings.py`). Only
FOR/AGAINST calls Bedrock: two calls per grey finding. Everything else runs locally.

| Measured, Nov 4 full run (8 pages) | Value |
| --- | --- |
| Grey findings | 784 (Before 531, After 253) |
| Tokens (`tokens_billed`, summed over the 8 `summary.json` files) | 1,220,955 input, 274,508 output |
| Cost | **$7.78** at $3 / $15 per million input / output tokens (list price). If the `us.` cross-region profile is billed at its 10% premium ($3.30 / $16.50), it is $8.56. The AWS bill is the authority. |
| Wall-clock time | about 2.5 hours, paced by Bedrock's per-minute limit (`ADJ_CALL_DELAY=2`, throttling waited out) |

Time per page, from one run's start to the next run's start (the run IDs are start times):

| Page | Grey findings | Minutes |
| --- | --- | --- |
| before/home | 111 | 21.8 |
| before/news | 91 | 18.1 |
| before/tickets | 182 | 36.2 |
| before/survey | 147 | 29.4 |
| after/home | 60 | 12.1 |
| after/news | 63 | 12.7 |
| after/tickets | 75 | 14.8 |
| after/survey | 55 | not measured (last run) |

That is about 12 seconds per grey finding, almost all of it waiting on Bedrock.

## Limits, stated honestly

- **Tuning set.** The ADJ code rules and router v2 were written while looking at this same site,
  so 89.2% and 0 false alarms are results on the tuning set, not on an unseen site. Two things
  guard against fitting the site: 8 of the 12 code rules implement a published W3C failure
  technique (F1, F3, F37, F46, F49, F55, F71, F73, F78, F79), as does the validator (F70), and the
  other 4 check the criterion's own requirement (skip link for 2.4.1, headings for 2.4.6, form
  labels for 3.3.2, images of text for 1.4.5); and every check was required to fire 0 times on
  the After pages before it merged. A second site is the real test (next step: the any-URL mode).
- **(a) vs (b).** (b) counts findings flagged for a person; (a) needs a machine-certain finding
  or an AI "likely fail" with verified citations. The AI is conservative: 31 grey findings lean
  "likely fail" but their citations didn't verify, so they stay flagged rather than reported.
  We report both, every time.
- **3 of 784 grey findings were not evaluated** (malformed AI replies, rejected by validation).
  They are on Before pages; they count as flagged.
- **Coverage is generous.** (c) checks the criterion, not the element; (a) and (b) check the
  element.
- **What still needs a human.** Every conformance level in the ACR comes from a human decision
  in PULSE. Criteria like 2.4.3 (focus order), 3.2.4 (consistent identification) and 3.3.1
  (error identification) are routed to a human by design.
- **The mapper spot-check measures the scanners too.** The mapper copies the criterion the
  scanner assigned and looks it up, so 10 of 10 says the scanners' assignment agrees with W3C on
  that sample.
- **One test site.** CityLights is built to fail in known ways. Results on a real agency site
  will differ.
- **The ground truth is ours.** It follows W3C's reports closely and 12 rows were re-checked, but
  matching a finding to a W3C row involves judgement (the 12 rows not on the served page). They
  are listed in the score report so a reader can disagree row by row.

## Triage study

Whether ADJ makes a reviewer at least 30% faster than manual review (the brief's stretch goal).
Protocol: [`docs/triage_study.md`](triage_study.md).

Results: <filled in by Jesus>

## Reproduce

From the repo root, with the venv active (setup in the README):

```bash
for v in before after; do for p in home news tickets survey; do
  ADJ_MAX_BEDROCK_CALLS=400 ADJ_CALL_DELAY=2 python -m agents.graph --version $v --page $p
done; done
python -m scripts.score --write
```

For a free dry run with no Bedrock calls, use `ADJ_MAX_BEDROCK_CALLS=0` instead: every grey
finding is then marked not evaluated, and the report says so.

## Changes to this page

| Date | Change |
| --- | --- |
| Oct 23 | Method, baseline results (FOR/AGAINST off), cost estimate |
| Nov 4 | Final numbers: full FOR/AGAINST run on the frozen code; progress table; router v2 (38 to 0); measured cost and time; tuning-set limit |
