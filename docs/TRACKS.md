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
| Shared setup | Repo owner (Jesus) | `package.json`, `.gitignore`, `requirements.txt`, `CONTEXT.md`, `README.md`, `docs/TRACKS.md`, `*/__init__.py` |
| A. Capture + scanners | Scanning lead (Jesus) | `capture/`, `scanners/`, `data/findings/` |
| B. Knowledge base + mapper | Knowledge lead | `rag/`, `agents/mapper.py`, `docs/mapper_spotcheck.md` |
| C. First agents | Orchestration lead | `agents/router.py`, `agents/evidence.py`, `prompts/`, `tests/prompt_set.json` |
| D. Graph + run log | Reviewer-experience lead | `agents/graph.py`, `data/runs/` |
| Ground truth | Sheet owner | `data/ground-truth/ground_truth.csv` |

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
| `evidence.selector` | `html` for page-level findings (e.g. missing `lang`, or Pa11y "check this manually" notices). |

Other merge rules: W3C's wrapper (`#meta-header`, `#meta-footer`) is filtered out;
all Pa11y notices are kept.

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

### One element can have several findings

There is one finding per (element, criterion). An element that fails two criteria
(e.g. an empty link fails 2.4.4 and 4.1.2) is two findings with the same
`evidence.dom_index`, the same screenshot, and IDs that differ only in the criterion.

## Data in git

| Folder | In git? | Why |
| --- | --- | --- |
| `data/findings/` | Yes | Tracks B, C and D read it without running capture |
| `data/ground-truth/` | Yes | The answer key |
| `data/runs/` | Track D decides | Replayable run log |
| `data/captures/` | No (gitignored) | Full HTML and screenshots, regenerated every run |
| `data/scans/` | No (gitignored) | Raw scanner JSON, regenerated every run |

## Known gotcha

`page.accessibility.snapshot()` no longer exists in Playwright 1.63 (the version in
`requirements.txt`). Use `page.aria_snapshot()` or a CDP session instead.
