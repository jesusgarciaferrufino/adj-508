# How human review works

ADJ separates what a machine can decide from what a person must decide. Scanners and code
rules find candidate issues and the AI writes arguments, but **only a signed-in human sets a
conformance status**. PULSE (`pulse/` and `extension/`) is the tool for this; this page is the
process it implements.

## 1. Which findings go to a human

The router (`agents/router.py`, policy v2, Oct 29; also in `CONTEXT.md`) puts every finding in
one bucket, in this order:

| Rule | Bucket | Why |
| --- | --- | --- |
| 1. `scanner_status` is `violation` (axe violation, or a deterministic ADJ code rule) | `machine` | The check is certain, so it is reported as an issue. A human still confirms it before it becomes a level (section 6). |
| 2. Page-level notice (Pa11y's "check this manually" reminder on `<html>`) | `human-only` | A reminder that applies to every page; no tool can answer it. |
| 2b. Every scanner detail on the finding is a check known to be unreliable for its criterion: HTML_CodeSniffer's `H43.IncorrectAttr` (1.3.1), a 4.1.2 `Placeholder` anchor (an `<a>` with no `href`), a 4.1.2 `Value` warning on a `select` | `human-only` | These warnings are often wrong on valid pages (the table-headers check guesses the table's structure; the select warning fires on a working default option), so a person judges them, not the AI. If the finding also has any other scanner check, it stays grey. |
| 2c. Criteria that need interaction: 3.3.1 Error Identification | `human-only` | It can only be judged by submitting a form and triggering an error, which a static scan never does. |
| 3. Criterion marked `human_only` in `rag/wcag20_aa.json` (2.4.3 Focus Order, 3.2.4 Consistent Identification) | `human-only` | These need judgement across the page or site, not one element. |
| 4. Everything else (`needs_review`, element-level notices) | `grey` | The tool can't be sure. FOR/AGAINST runs on these, and a person decides. |

Rules 2b and 2c were added after the Oct 29 run, where these patterns caused all 38 false alarms
on W3C's After site. Routed to a person, they caused 0 in the final run
([`docs/benchmark.md`](benchmark.md)).

A grey finding counts as an issue only when the AI leans `likely_fail` *and* its citations
were verified in code (`is_issue` in `agents/evidence.py`). Every other grey finding is
"flagged for a human". Criteria with no findings at all are shown as "No automated findings;
human check required".

## 2. Who may review

Only reviewers with a token in `ADJ_REVIEWER_TOKENS` (in the gitignored `.env`; placeholders in
`.env.example`). Each token is 16 or more characters, is compared in constant time, and is
never logged or returned. The API only answers requests addressed to `localhost` or `127.0.0.1`. **The reviewer's name
comes from the token, never from the request**, so no one can record a decision under someone
else's name.

## 3. What the reviewer sees

For each finding on the page, the PULSE side panel shows a card with:

- the criterion and its level, the finding ID, the scanner status, and what each scanner or rule said;
- the evidence: the element's selector, its HTML, and a screenshot crop of the element;
- when the card is opened, the element is scrolled into view on the live W3C page and outlined
  in red (page-level findings have nothing to outline, and the panel says so);
- a box labelled "AI suggestion: written by AI, not a decision" with the case FOR and the case
  AGAINST a failure and the AI's leaning in words. FOR/AGAINST runs only on grey findings, so
  on other findings the box reads "none given"; if the AI skipped a finding, it says so instead
  of showing a guess.

## 4. The three decisions

| Decision | Meaning |
| --- | --- |
| **Fail** | The element does not meet this criterion. |
| **Pass** | The element meets this criterion; the finding was a false alarm. |
| **Not applicable** | The criterion doesn't apply here (for example, no video on the page). |

A reviewer can add a comment to explain any decision.

## 5. Decisions are append-only and attributed

Each decision is one JSON line in `data/reviews/{version}_{page}.jsonl` with `finding_id`,
`run_id`, `reviewer`, `decision`, `comment` and a server-set `decided_at` time. Lines are
never edited or deleted, so the file is the audit trail. A finding decided twice keeps
both lines and **the last decision wins** (saving the same decision and comment
on the same run again writes nothing new). PULSE writes only to `data/reviews/`. It never
changes findings or runs, and never writes a final status itself. Finding IDs stay the same
between runs, so a decision still applies after a re-scan.

## 6. From decisions to ACR conformance levels

The matrix (`reports/matrix.py`, Track E) computes each criterion's status per page from the
human decisions on its findings, and the draft ACR uses these:

| Human decisions on the criterion's findings | Status |
| --- | --- |
| At least one Fail | Does Not Support (human-confirmed) |
| Some findings not yet decided | Pending human review |
| All decided, at least one Pass, no Fail | Supports (human-confirmed) |
| All decided Not applicable | Not Applicable (human-confirmed) |
| No findings at all | No automated findings; human check required |

When a row is still "Pending human review" but has machine findings (certain scanner or code rule
violations) that no one has decided yet, the ACR's Remarks also say
"Proposed by ADJ (machine-verified, awaiting human ratification):" followed by the level the row
would get if a person confirmed those findings (#54). It is a proposal for the reviewer, not a
level: **only a human decision sets the Conformance Level.** AI leanings never produce a
proposal, and a row a person has already decided has nothing to ratify.

## 7. AI never sets a status

The status function receives only finding IDs and human decisions. It can't see the bucket,
the scanner status or the AI's recommendation, so the AI's leaning is shown as advice, in its
own column labelled as AI, and never becomes a status. A machine finding is reported as an
issue, but it is still "Pending human review" until a person decides it.
