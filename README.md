# ADJ: AI-Assisted Documentation and Judgement

ADJ assesses a website against Section 508 (WCAG 2.0 Level A and AA). Scanners and ADJ's own
code rules find the issues; for every judgement call, an AI argues FOR and AGAINST a failure
using quotes that code verifies against the page; and a named human decides. **The AI never sets
a conformance level.**

Built for the Unissant Challenge X use case, *GenAI-Driven Section 508 Compliance Orchestration
and VPAT/ACR Automation*. Test site: W3C's [Before and After Demonstration](https://www.w3.org/WAI/demos/bad/).

## Results (final run, Nov 4)

- **89.2%** of W3C's 130 Before issues reported or sent to a person (**98.3%** of the 118 on the served page)
- **59.2%** reported as issues by ADJ itself (65.3% of the 118 on the served page)
- **0** false alarms on the After site (every After finding evaluated)
- **$7.78** and about 2.5 hours for all 8 pages (1.2 M input, 0.27 M output tokens)

Method, per-criterion results, progress and limits: [`docs/benchmark.md`](docs/benchmark.md).

## Deliverables

| Output | File |
| --- | --- |
| Index of the evidence package | [`deliverables/index.html`](deliverables/index.html) |
| Draft ACR (Accessibility Conformance Report) | [`deliverables/acr.html`](deliverables/acr.html) |
| Compliance matrix, Before site | [`deliverables/site_matrix_before.md`](deliverables/site_matrix_before.md) |
| Prioritized remediation backlog | [`deliverables/backlog.md`](deliverables/backlog.md) |
| Evidence pages (finding, screenshot, decision) | [`deliverables/evidence/`](deliverables/evidence/) |

## How it works

1. **Capture** each page with Playwright (DOM, accessibility tree, screenshots).
2. **Scan** it with axe-core and Pa11y, plus ADJ's code rules, an HTML validator and a cross-page comparison.
3. **Merge** everything into one finding per element and criterion, each with its selector, HTML and a screenshot.
4. **Route** each finding: certain violations to `machine`, judgement calls to `grey`, and what no tool can judge to `human-only`.
5. **FOR/AGAINST**: two separate AI calls argue each grey finding; code checks every quote against the evidence.
6. **Human review** in PULSE, the reviewer panel: a signed-in person records Fail, Pass or Not applicable.
7. **Reports**: compliance matrix, draft ACR and remediation backlog, where every level comes from a human decision.

The review process: [`docs/human_review.md`](docs/human_review.md).

## Run it

Setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
npm install
cp .env.example .env
aws configure
```

`aws configure` takes the access key, the secret key and region `us-east-1`. In `.env`, set
`ADJ_REVIEWER_TOKENS=yourname:<16+ characters>` to use PULSE.

One page, with FOR/AGAINST on every grey finding:

```bash
ADJ_MAX_BEDROCK_CALLS=400 ADJ_CALL_DELAY=2 python -m agents.graph --version before --page home
```

All 8 pages, then the scores and the report package (the run IDs are printed by each run):

```bash
for v in before after; do for p in home news tickets survey; do
  ADJ_MAX_BEDROCK_CALLS=400 ADJ_CALL_DELAY=2 python -m agents.graph --version $v --page $p
done; done
python -m scripts.score
python -m reports.package --runs <8 run ids>
```

`ADJ_MAX_BEDROCK_CALLS=0` gives a free dry run with no AI calls.

## Security and trust

- **Least-privilege AWS access:** a dedicated IAM user that can only invoke Bedrock models (it can't even read Service Quotas).
- **Reviewer tokens** of 16+ characters, kept in the gitignored `.env`, never committed; the reviewer's name comes from the token, never from the request.
- **Secret scanning:** gitleaks runs on every pull request in CI.
- **Append-only, attributed decisions** in `data/reviews/`: every decision names its reviewer and time, and none is ever edited.
- **The AI never sets a conformance level**; only a human decision does, and AI text is always labelled as AI.
- **A hard cap on Bedrock calls per run** (`ADJ_MAX_BEDROCK_CALLS`, default 20).

## Layout

| Folder | Contents |
| --- | --- |
| `capture/` | Page capture with Playwright |
| `scanners/` | Scanner runners, ADJ's code rules, validator, page comparison, keyboard walk, and the merge step |
| `agents/` | Mapper, router, FOR/AGAINST evidence and the LangGraph pipeline |
| `prompts/` | Versioned prompt files |
| `rag/` | WCAG 2.0 and Section 508 reference data |
| `pulse/` | PULSE review API (where human decisions are recorded) |
| `extension/` | PULSE browser extension (the reviewer panel) |
| `reports/` | Compliance matrix, draft ACR, backlog and evidence package |
| `scripts/` | Scoring against the ground truth, and helpers |
| `docs/` | Benchmark, human review process, team contract (`TRACKS.md`), scores |
| `data/` | Findings, ground truth, human decisions, and run logs |
| `deliverables/` | The final report package |

Working on the repo: read `docs/TRACKS.md` first (who owns which file, branches, the finding format).

## Team

- Jesus Garcia-Ferrufino
- Daniel Etuk (POC)
- Abel Teshome
- Ershad Sulaiman
