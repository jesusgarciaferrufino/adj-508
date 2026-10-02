# ADJ — AI-Assisted Documentation & Judgement

Section 508 / WCAG 2.0 A & AA accessibility assessment with AI-built evidence
and human-controlled conformance decisions. Built for the Unissant Challenge X
use case: *GenAI-Driven Section 508 Compliance Orchestration and VPAT/ACR Automation*.

## What it does

1. Captures a web page (DOM, accessibility tree, screenshots)
2. Runs automated scanners and merges their findings
3. Sorts each finding into machine-verified, grey area, or human-only
4. For grey areas, builds a case FOR and a case AGAINST the violation
5. Sends every grey area to a human reviewer, who makes the final call
6. Produces a compliance matrix, a draft ACR, and a prioritized fix list

Test site: [W3C Before and After Demonstration](https://www.w3.org/WAI/demos/bad/)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
npm install
```

AWS credentials are needed for Bedrock. Run `aws configure` with region `us-east-1`.

## Layout

| Folder | Contents |
| --- | --- |
| `capture/` | Page capture with Playwright |
| `scanners/` | Scanner runners and the merge step |
| `agents/` | LangGraph agents and the graph |
| `prompts/` | Versioned prompt files |
| `rag/` | WCAG and Section 508 reference data |
| `reports/` | ACR, matrix and backlog templates |
| `data/` | Ground truth, scans, captures, run logs |

## Working on this repo

Read `docs/TRACKS.md` before you start: it says which files each track owns,
how to name branches, and what goes in git. The shared finding format is
`docs/finding.example.json`.

## Team

- Jesus Garcia-Ferrufino
- Daniel Etuk (POC)
- Abel
