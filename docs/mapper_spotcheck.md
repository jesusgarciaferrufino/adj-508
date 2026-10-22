# Mapper spot-check log

Appended by `python -m agents.mapper --spotcheck`: a fixed-seed sample of mapped findings compared with the `wcag` column of `data/ground-truth/ground_truth.csv`.

## 2026-10-22: before/home, mapper-v2-corpus-lookup, seed 508

**10/10 correct** (target 8/10).

| finding_id | Mapped | Ground truth | Match |
| --- | --- | --- | --- |
| F-before-home-el0002-2.4.2 | 2.4.2 | 2.4.2 | yes |
| F-before-home-el0077-1.1.1 | 1.1.1 | 1.1.1 | yes |
| F-before-home-el0081-1.1.1 | 1.1.1 | 1.1.1 | yes |
| F-before-home-el0085-4.1.2 | 4.1.2 | 3.3.2, 4.1.2 | yes |
| F-before-home-el0134-2.1.1 | 2.1.1 | 2.1.1 | yes |
| F-before-home-el0193-2.4.4 | 2.4.4 | 2.4.4 | yes |
| F-before-home-el0209-1.1.1 | 1.1.1 | 1.1.1 | yes |
| F-before-home-el0210-2.4.4 | 2.4.4 | 2.4.4 | yes |
| F-before-home-el0217-1.1.1 | 1.1.1 | 1.1.1 | yes |
| F-before-home-el0231-1.4.3 | 1.4.3 | 1.4.3 | yes |

### Misses and why

No misses.

### Notes

- **What this measures.** The mapper copies the one criterion Track A's merge gives each finding and looks it up in the corpus, so this score checks Track A's criterion assignment as much as the mapper.
- **Outside the sample.** Scoring all 17 before/home findings that the ground truth names gives 16/17. The one mismatch is `F-before-home-el0085-3.2.1` (the QUICKMENU select): Pa11y files it under 3.2.1 (G107 notice), while W3C's Before report lists the change of context on input under 3.2.2. The ground-truth row notes it as cross-criterion. A mapper that only copies the scanner's criterion can't correct this; it would need the human review step.
- **How the sheet is read.** Rows marked `missed` (issues no scanner found) can't be matched to a finding and are skipped. A finding listed on more than one row (here `F-before-home-el0085-4.1.2`, under 3.3.2 and 4.1.2) counts as correct for any of those criteria.
