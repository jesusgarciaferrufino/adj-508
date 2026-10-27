# Triage-speed study: protocol

**Question.** Does ADJ make a person's triage of scanner findings at least 30% faster than
reviewing the same findings manually, without losing accuracy? This is the brief's stretch
goal: "triage at least 30% faster than a person reviewing the same list manually".

**When.** Sat Oct 31 to Sun Nov 1, 2026, before the feature freeze (Mon Nov 2). About 75 minutes
per reviewer, plus 30 minutes of setup.

## 1. Design at a glance

| | Half A (15 findings) | Half B (15 findings) | Goes first |
| --- | --- | --- | --- |
| Reviewer R1 | Manual | ADJ | Manual |
| Reviewer R2 | ADJ | Manual | ADJ |

- **Crossover.** Every finding is triaged once in each condition, by different reviewers, and
  each reviewer uses both conditions. So neither a fast reviewer nor an easy half can make one
  condition look better. R1 starts with Manual and R2 with ADJ, so getting faster with practice
  doesn't favour either condition.
- **Manual:** the reviewer gets the scanner output only (page, criterion, selector, scanner
  message) and the live W3C page with Chrome DevTools.
- **ADJ:** the reviewer uses PULSE: evidence, screenshot crop, the element outlined on the live
  page, and the FOR/AGAINST case.
- **Decisions** are the three PULSE uses in both conditions: Fail, Pass, Not applicable
  (docs/human_review.md, section 4).

## 2. People

| Role | Who | Does |
| --- | --- | --- |
| Reviewers R1, R2 | Two people who did not build PULSE (Track F, #14) and have not seen the sample | Triage the findings |
| Adjudicator | A third person, not R1 or R2 | Writes the reference answers before the study, without seeing anyone's decisions |
| Facilitator | Ershad | Setup, timing, results |

## 3. The sample

30 Before findings, drawn by `python -m scripts.triage_sample` (fixed seed
`adj-triage-2026-10-31`) from `data/findings/` as regenerated with ADJ's code rules
(PR #40, commit `ea41667`).

- **Eligible:** findings the router (policy v1) puts in **grey** or **human-only**, except
  Pa11y's page-level notices (a generic reminder on `<html>`, which can't be decided per
  element and which decision 3 treats as not an issue) and the three Before findings in
  `docs/evidence_samples.md`, which reviewers have seen. 498 findings are eligible.
- **Stratified:** 8, 8, 7 and 7 findings from home, news, tickets and survey. Within a page,
  criteria are visited in a fixed shuffled order, one finding each, round after round.
- **No repeats:** the header, navigation and `<title>` are on every page, so a finding is
  skipped when an earlier pick has the same criterion and selector.
- **Halves:** picks alternate between A and B within each page, giving 15 and 15 (A: 4, 4, 4,
  3 per page; B: 4, 4, 3, 4).
- The draw is a SHA-256 of the seed and the finding ID, so it gives the same 30 on any machine.
  Rerunning it after later regenerations can give a different list; **this list is the
  sample**.

All 30 are grey. No human-only finding survives the rules above: on the Before pages every
human-only finding is a page-level notice, except 3.2.4 hits from the page-comparison check,
which were not in `data/findings/` when the sample was drawn. 11 criteria are covered: 1.1.1,
1.3.1 and 2.4.4 (5 each), 1.4.3 and 2.1.1 (4 each), 3.2.1 (2), and 1.4.1, 2.4.1, 2.4.2, 2.4.6
and 3.3.4 (1 each).

| # | Half | Finding | What it is | Scanner said | Reference answer from |
| --- | --- | --- | --- | --- | --- |
| 1 | A | `F-before-home-el0085-3.2.1` | QUICKMENU select | Pa11y G107 (notice) | adjudicator |
| 2 | B | `F-before-home-el0122-1.3.1` | 780px layout table | Pa11y LayoutTable (notice) | adjudicator |
| 3 | A | `F-before-home-el0000-2.4.1` | page (no skip link) | Pa11y + adj no-skip-link (needs_review) | W3C report: 2.4.1 |
| 4 | B | `F-before-home-el0000-2.4.6` | page (no headings) | Pa11y + adj no-headings (needs_review) | W3C report: 2.4.6 |
| 5 | A | `F-before-home-el0155-2.1.1` | nav cell "survey" | Pa11y MouseOver/MouseOut (needs_review) | adjudicator |
| 6 | B | `F-before-home-el0002-2.4.2` | <title> | Pa11y H25 (notice) | W3C report: 2.4.2 F25 |
| 7 | A | `F-before-home-el0172-2.4.4` | link "Heat wave linked to temperatures" | Pa11y H77-H81 (notice) | adjudicator |
| 8 | B | `F-before-home-el0198-1.1.1` | morearrow.gif (alt="") | Pa11y G73/G74 (notice) | adjudicator |
| 9 | B | `F-before-news-el0265-1.3.1` | footer <font> with links | Pa11y H49 font (needs_review) | adjudicator |
| 10 | A | `F-before-news-el0201-1.1.1` | smzappa2.jpg "picture of Clara F." | Pa11y G94 + G73/G74 (notice) | adjudicator |
| 11 | B | `F-before-news-el0071-1.4.3` | header table with logo | Pa11y F24 BGColour (needs_review) | adjudicator |
| 12 | A | `F-before-news-el0141-2.1.1` | nav cell "news" | Pa11y MouseOver/MouseOut (needs_review) | adjudicator |
| 13 | B | `F-before-news-el0175-1.4.1` | link "trombone forgery debacle" | adj link-color-only (needs_review) | adjudicator |
| 14 | A | `F-before-news-el0270-2.4.4` | footer link "MIT" | Pa11y H77-H81 (notice) | adjudicator |
| 15 | B | `F-before-news-el0057-1.3.1` | layout cell with border image | Pa11y H49 align (needs_review) | adjudicator |
| 16 | A | `F-before-news-el0086-1.1.1` | QUICKMENU ----> option | adj ascii-lookalike (needs_review) | W3C report: 1.1.1 F71 |
| 17 | A | `F-before-tickets-el0337-2.4.4` | footer link "W3C" | Pa11y H77-H81 (notice) | adjudicator |
| 18 | B | `F-before-tickets-el0141-1.4.3` | nav cell "news" | Pa11y F24 BGColour (needs_review) | adjudicator |
| 19 | A | `F-before-tickets-el0198-1.1.1` | telefon_lightgrey_bg.gif "music line phone number" | Pa11y G94 + G73/G74 (notice) | W3C report: 1.1.1 F30 |
| 20 | B | `F-before-tickets-el0134-2.1.1` | nav cell "home" | Pa11y MouseOver/MouseOut (needs_review) | W3C report: 2.1.1 F54 |
| 21 | A | `F-before-tickets-el0226-1.3.1` | bold "Obelisks - Monkey Monkey" | Pa11y H49 b (needs_review) | adjudicator |
| 22 | B | `F-before-tickets-el0336-2.4.4` | footer link "Copyright" | Pa11y H77-H81 (notice) | adjudicator |
| 23 | A | `F-before-tickets-el0122-1.4.3` | 780px layout table | Pa11y F24 BGColour (needs_review) | adjudicator |
| 24 | B | `F-before-survey-el0148-2.1.1` | nav cell "tickets" | Pa11y MouseOver/MouseOut (needs_review) | adjudicator |
| 25 | A | `F-before-survey-el0054-1.4.3` | outer page table | Pa11y F24 BGColour (needs_review) | W3C report: 1.4.3 F24 |
| 26 | B | `F-before-survey-el0115-1.3.1` | bold "Today:" | Pa11y H49 b (needs_review) | adjudicator |
| 27 | A | `F-before-survey-el0514-3.2.1` | name text input | Pa11y G107 (notice) | adjudicator |
| 28 | B | `F-before-survey-el0168-3.3.4` | survey form | Pa11y LegalForms (notice) | adjudicator |
| 29 | A | `F-before-survey-el0585-2.4.4` | footer link "ERCIM" | Pa11y H77-H81 (notice) | adjudicator |
| 30 | B | `F-before-survey-el0493-1.1.1` | gif.gif spacer (alt="") | Pa11y + G73/G74 (needs_review) | adjudicator |

"Reference answer from": 7 findings are linked to a row in `data/ground-truth/ground_truth.csv`,
so W3C's report says Fail. The adjudicator decides the other 23 (section 4, step 6) and also
checks the 7.

## 4. Before the session (facilitator, about 30 minutes)

1. **Use a separate copy of the repo.** PULSE writes every decision to `data/reviews/`, which is
   the ACR's evidence, so study decisions must never reach main:
   ```bash
   git worktree add ../adj-triage main
   cd ../adj-triage
   ```
   Never commit from this copy. Section 7 says where the results go.
2. **Run the pipeline** on the 4 Before pages, so each grey finding has its FOR/AGAINST case:
   ```bash
   for p in home news tickets survey; do python -m agents.graph --version before --page $p; done
   ```
   Check that `stubs` is empty in each run's `summary.json`, and that all 30 sampled IDs are in
   the runs' `output_findings.json`. If the evidence step skipped a sampled finding, keep the
   finding and write it down: PULSE then says the AI gave no case, which is how ADJ really
   behaves.
3. **Give each reviewer a token.** Put `R1:<token>,R2:<token>` (16+ characters each) in this
   copy's `.env` as `ADJ_REVIEWER_TOKENS`. Start the API (`uvicorn pulse.api:app --port 8000`)
   and load the extension. Check that a test decision works on an **After** finding, then delete
   that copy's `data/reviews/after_*.jsonl`.
4. **Print the Manual packet:** `python -m scripts.triage_sample --packet > manual_packet.txt`.
   It gives the page URL, criterion, selector and scanner message for each finding, and nothing
   else.
5. **Share the timing sheet** (`docs/triage_timing_sheet.csv`) as a Google Sheet, one tab per
   reviewer, filtered to their rows.
6. **Reference answers.** The adjudicator decides all 30 findings (Fail, Pass or Not applicable,
   with a one-line reason) in a separate sheet, before the session, without seeing any reviewer's
   decisions. For the 7 linked findings, if the adjudicator disagrees with W3C's report, W3C wins
   and the disagreement is noted.
7. **Warm-up findings:** two grey After findings that are not in the sample,
   `F-after-home-el0099-2.4.4` (Manual) and `F-after-news-el0097-1.4.3` (ADJ), so the
   reviewers learn the tools before the clock matters. Run the pipeline on after/home and
   after/news too, so they appear in PULSE.

## 5. Session (each reviewer, about 75 minutes)

Each reviewer works alone, with no other tabs open. The facilitator stays in the room or on a
call but doesn't help with decisions.

1. **Warm-up (5 min):** the two warm-up findings, one in each condition. Not timed.
2. **Block 1:** your first condition and half (section 1).
   1. In the timing sheet's START row for the block, press **Ctrl+Shift+;** (Cmd+Shift+; on a
      Mac) in `end_time`. This stamps the time to the second.
   2. Take the findings in the sheet's order. For each one, open it (Manual: the packet and the
      live page; ADJ: its card in PULSE), decide Fail, Pass or Not applicable, and record it:
      - **Manual:** type the decision and press Ctrl+Shift+; in `end_time`.
      - **ADJ:** save the decision in PULSE. PULSE stamps `decided_at`. Also press Ctrl+Shift+;
        in `end_time` as a backup.
   3. If anything interrupts you, put `yes` in `interrupted` and say what in `notes`.
   4. Don't go back to change an earlier decision.
3. **Break:** 10 minutes.
4. **Block 2:** the other condition and half, the same way.
5. **Afterwards (2 min):** one line in `notes` on the last row: what helped or got in the way.

## 6. Measures

- **Time per finding:** the time from the previous stamp to this finding's stamp. The first
  finding counts from the block's START. ADJ uses PULSE's `decided_at`; Manual uses `end_time`.
  Both measure the whole stretch from finishing one finding to finishing the next, including
  opening the next one. Findings marked `interrupted` are left out of the timing, and the
  report says how many.
- **Accuracy:** a decision is correct when it matches the reference answer. Also counted
  separately: Pass or Not applicable on a finding whose reference is Fail. That is a missed
  issue, the costliest kind of error.

## 7. Results and report (Sun Nov 1)

Put the raw files in `data/triage_study/`: each reviewer's timing tab as CSV, the worktree's
`data/reviews/before_*.jsonl` and the adjudicator's sheet. Then write `docs/triage_results.md`:

| | Manual | ADJ |
| --- | --- | --- |
| Median seconds per finding (all 30) | | |
| Median for R1 / R2 | | |
| Correct decisions (of 30) | | |
| Missed issues (Pass or N/A on a Fail) | | |

- **Speed-up** = 1 − (ADJ median ÷ Manual median). The target is **30% or more**.
- **Paired view:** each finding has one Manual and one ADJ time, so also report on how many of
  the 30 findings ADJ was faster.
- **Accuracy holds** if ADJ has at least as many correct decisions as Manual and no more missed
  issues.
- Report the result as it comes out, including a miss. Every number goes with the limits below.

## 8. Limits (say these in the report)

- 2 reviewers and 30 findings is small. The result shows a direction, not a precise effect size.
- Each finding is timed once per condition, by a different person, so reviewer speed still
  adds noise. The crossover balances it but doesn't remove it.
- All 30 are grey Before findings on one demo site. Human-only findings and other sites aren't
  covered.
- The adjudicator's answers are one person's judgement for 23 of the 30.
- Reviewers know they are being timed and know which condition they are in. That can't be
  hidden.
- PULSE needs a finished pipeline run, which is not counted in the ADJ time. The study measures
  a person's triage time, not ADJ's total time.
