# Week 2 tracks: who owns which file

The rule that prevents merge conflicts: **only a file's owner creates or edits it.**
If you need a change in someone else's file, ask them or open an issue. Do not
create placeholder copies of another track's files on your branch.

## Branches

- Branch from an up-to-date `main`: `git checkout main && git pull`.
- Name it `yourname/short-task-name`. Never commit to `main` directly.
- Open a pull request when your checkpoint is met. One teammate reviews and merges.
- Track A merges first, because B, C and D read its output.

## Ownership

| Track | Owner (role) | Files only this track creates or edits |
| --- | --- | --- |
| Shared setup | Repo owner (Jesus) | `package.json`, `.gitignore`, `requirements.txt`, `CONTEXT.md`, `README.md`, `docs/TRACKS.md`, `*/__init__.py`, `settings.py`, `.env.example` |
| A. Capture + scanners | Scanning lead (Jesus) | `capture/`, `scanners/`, `data/findings/`, `tests/test_merge.py` |
| B. Knowledge base + mapper | Knowledge lead | `rag/`, `agents/mapper.py`, `docs/mapper_spotcheck.md`, `data/mapped/`, `tests/test_mapper.py` |
| C. Router + FOR/AGAINST | Orchestration lead | `agents/router.py`, `agents/evidence.py`, `prompts/`, `tests/test_router.py`, `tests/test_evidence.py`, `docs/evidence_samples.md` |
| D. Graph + run log | Reviewer-experience lead | `agents/graph.py`, `data/runs/` |
| Ground truth | Sheet owner | `data/ground-truth/ground_truth.csv`, `tests/test_ground_truth.py` |
| E. Outputs | Outputs lead | `reports/`, `tests/test_matrix.py` |
| F. PULSE | Reviewer-UI lead | `pulse/`, `extension/`, `data/reviews/`, `tests/test_pulse_api.py` |
| S. Scoring | Repo owner (Jesus) | `scripts/gt_candidates.py`, `scripts/score.py`, `docs/scores/`, `tests/test_score.py` |

Tests: each track owns `tests/test_<track or module>.py` for its own code (for example
`tests/test_mapper.py`, `tests/test_graph.py`). `tests/test_contracts.py`,
`tests/test_findings_data.py` and `tests/conftest.py` are shared setup.

Need a new Python package for your track? Tell the repo owner so it goes into
`requirements.txt` once, instead of three people editing the same line.

## The finding format

`docs/finding.example.json` is the contract every track codes against.
It changes only by team agreement, in its own small pull request.

Decided 2026-10-02 (Track A, A4). The example file is a real finding from
`data/findings/before_home.json`. All changes are additive to the week 1 format:

| Field | Meaning |
| --- | --- |
| `source` | Unchanged single string. `axe-core` whenever axe found it, else `pa11y`. |
| `sources` | NEW. Every scanner that found it, e.g. `["axe-core", "pa11y"]`. |
| `scanner_status` | NEW. `violation` only when axe reports a violation. axe "incomplete", every Pa11y error and warning: `needs_review`. Pa11y notice: `notice`. This is a scanner signal, not the bucket. |
| `scanner_details` | NEW. One entry per scanner rule that fired: `scanner`, `rule_id`, `type`, `message`. Use `message` for the prompts' `SCANNER SAID`. |
| `bucket` | Always `null` from merge. Track C's router sets it. |
| `wcag` | Exactly one WCAG 2.0 A/AA criterion per finding. WCAG 2.1/2.2 results are dropped. |
| `evidence.screenshot` | Repo path, not S3: `data/findings/shots/{v}_{p}_el{NNNN}.jpg` (element plus 40px of page), or `{v}_{p}_page.jpg` for page-level findings. `null` when the element can't be photographed (e.g. `<title>`). |
| `evidence.dom_index` | NEW. Element position in document order; how duplicates across scanners are matched. |
| `evidence.context_text` | NEW (Oct 15). Visible text around the element: its nearest block (paragraph, list item, cell, heading), or the nearest short block above it if that is empty, at most 300 characters centred on the element. Empty for page-level findings and for elements with no nearby text (e.g. spacer images). For FOR/AGAINST and reviewers. |
| `evidence.selector` | `html` for page-level findings (e.g. missing `lang`, or Pa11y "check this manually" notices). |

Other merge rules: W3C's wrapper (`#meta-header`, `#meta-footer`) is filtered out;
all Pa11y notices are kept.

### Reading findings in code

Don't parse the JSON by hand. Use the loader, which checks every finding against this
format and fails with a clear message naming the file, finding and field:

```python
from scanners.findings import load_findings

for f in load_findings("before", "home"):
    f.finding_id                          # "F-before-home-el0081-1.1.1"
    f.wcag[0]                             # "1.1.1"
    f.evidence.selector, f.evidence.html_snippet, f.evidence.screenshot
    [d.message for d in f.scanner_details]   # what the scanners said (for SCANNER SAID)
```

`f.model_dump()` gives a plain dict. Run scripts from the repo root as modules
(`python -m agents.mapper`). Element details live under `evidence`, and scanner messages
under `scanner_details`; there is no top-level `selector`, `html_snippet` or `message`.

**Where your output goes:** `data/findings/` holds only Track A's eight page files, and
CI fails if anything else appears there. Write derived results to your own track's folder
(for example `data/mapped/` for Track B), keyed by `finding_id`.

### Finding IDs (changed 2026-10-02, before any track depended on them)

`F-{version}-{page}-el{dom_index:04d}-{criterion}`, e.g. `F-before-home-el0081-1.1.1`.
The ID says *what* the finding is (which element, which criterion), not its position
in the list, so re-running the scanners or changing a filter never renames an existing
finding. Use `finding_id` as the key anywhere you store decisions, run logs or
ground-truth matches. The old numbered IDs (`F-before-home-0032`) are gone.

### Values Tracks B, C and D can rely on

| Field | Exact values |
| --- | --- |
| `scanner_status` | `violation`, `needs_review`, `notice` |
| `scanner_details[].type` | axe: `violations`, `incomplete` (axe's own names, plural). Pa11y: `error`, `warning`, `notice` |
| `source`, `sources[]` | `axe-core`, `pa11y` |

Check `scanner_status`, not `scanner_details[].type`, to know if a finding is a violation.

### Fields later tracks add

These are added by the pipeline (`python -m agents.graph`) and are never written into
`data/findings/`. The loader keeps them, so they survive validation.

| Field | Added by | Shape |
| --- | --- | --- |
| `mapping` | Track B, `map_finding(finding)` | `{"sc", "name", "level", "spec_url", "mapper_version", "corpus_sha256"}`. If the criterion is not in the corpus: `{"sc", "rejected": reason, "mapper_version", "corpus_sha256"}`. Never changes `wcag`. |
| `bucket`, `route_reason` | Track C, `route_finding(finding)` | Bucket values to be fixed by Track C (currently `machine`, `grey`, `human-only` in the graph). |
| `ai.case_for`, `ai.case_against`, `ai.model_id`, `ai.prompt_version`, `ai.usage` | Track C, `build_cases(finding)` | Grey findings only. `usage` is `{"input_tokens", "output_tokens"}`. |

### One element can have several findings

There is one finding per (element, criterion). An element that fails two criteria
(e.g. an empty link fails 2.4.4 and 4.1.2) is two findings with the same
`evidence.dom_index`, the same screenshot, and IDs that differ only in the criterion.

## Data in git

| Folder | In git? | Why |
| --- | --- | --- |
| `data/findings/` | Yes | Tracks B, C and D read it without running capture |
| `data/ground-truth/` | Yes | The answer key |
| `data/mapped/` | Yes | Track B's mapper output; regenerate with `python -m agents.mapper` |
| `data/runs/` | No (gitignored by Track D) | Replayable run log, written by every pipeline run |
| `data/reviews/` | Yes | Human decisions from PULSE; they are the evidence behind every ACR status |
| `data/reports/` | No (gitignored) | Matrix, ACR and backlog; regenerated from a run plus `data/reviews/` |
| `data/captures/` | No (gitignored) | Full HTML and screenshots, regenerated every run |
| `data/scans/` | No (gitignored) | Raw scanner JSON, regenerated every run |

## Human decisions: `data/reviews/{version}_{page}.jsonl`

Written only by the PULSE API (`pulse/api.py`, `record_decision`), one JSON object per line,
append-only. Nothing else writes here; Track E reads it to set a status.

```json
{"finding_id": "F-before-home-el0210-2.4.4", "run_id": "20261013-010051-e473", "reviewer": "jesus", "decision": "fail", "comment": "", "decided_at": "2026-10-15T22:04:31Z"}
```

| Field | Value |
| --- | --- |
| `finding_id` | A finding in that run's `output_findings.json` (the API rejects others with 422) |
| `run_id` | `YYYYMMDD-HHMMSS-xxxx`, the run folder under `data/runs/` |
| `reviewer` | Name from the bearer token in `ADJ_REVIEWER_TOKENS`, never from the request body |
| `decision` | `fail`, `pass` or `not_applicable` |
| `comment` | Always a string; `""` when the reviewer left none |
| `decided_at` | UTC, seconds precision, ending in `Z` |

Keys are written in the order above, as ASCII-only JSON (non-ASCII is `\u`-escaped), so each
decision is exactly one line. Existing lines are never changed. **The last line for a
finding_id wins.** Re-saving a reviewer's latest decision unchanged (same run, decision and
comment) writes nothing. Readers skip lines that are not valid JSON or lack these fields.

## Known gotcha

`page.accessibility.snapshot()` no longer exists in Playwright 1.63 (the version in
`requirements.txt`). Use `page.aria_snapshot()` or a CDP session instead.
