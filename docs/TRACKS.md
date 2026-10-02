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

Open questions to settle as a team before Track A's merge step (A4):

1. The example has `"source": "axe-core"` (one string). The week 2 merge rule says
   findings found by both scanners list both in `sources`. Which one?
2. The merge rule tags axe "incomplete" results `scanner_status: "needs_review"`.
   That field is not in the example. Add it?
3. The router (Track C) decides `bucket`. Should merge leave it `null`?
4. `evidence.screenshot` is an `s3://` path in the example. Week 2 has no S3,
   so is a repo-relative path like `data/findings/shots/...` acceptable for now?

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
