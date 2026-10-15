# Project context for AI assistants

## What we're building
ADJ: an AI system that checks a website for Section 508 / WCAG 2.0 A & AA issues,
builds evidence for each finding, and produces a draft ACR, a compliance matrix and
a prioritized fix list. PULSE is the browser extension reviewers use to inspect
evidence and decide on grey-area findings.

## Hard rules
- AI never sets a final conformance status. Only a signed-in human reviewer can.
- Every finding has an ID and links to evidence (selector, HTML, screenshot, rule).
- Only cite WCAG 2.0 Level A/AA criteria. Never invent criteria.
- Never put API keys or passwords in code. Use environment variables.

## Test site
W3C Before and After Demo: https://www.w3.org/WAI/demos/bad/
Ground truth is built from the Before evaluation reports for home, news, tickets, survey.

## Stack
Python 3.13, Playwright, axe-core, Pa11y, LangGraph, Claude on Amazon Bedrock, FastAPI.
Model and region come from settings.py: ADJ_MODEL_ID (default Claude Sonnet 4.6) and
ADJ_AWS_REGION (default us-east-1). Never hard-code a model ID in code; tests/test_settings.py checks.
Bedrock Claude models must be called via the us. inference profile ID.
Target model is Claude Fable 5.1. Access for our account is pending with AWS support; once granted,
switch by setting ADJ_MODEL_ID (see .env.example). No code change needed.
Smoke test: python -m scripts.hello_bedrock

## Current phase
Week 2: one grey-area finding end to end (capture, scanners, mapper, router, FOR/AGAINST).
- Track A output is ready: data/findings/{before,after}_{home,news,tickets,survey}.json
  (format: docs/finding.example.json). Tracks B, C and D read these files and do not
  need to run the scanners. Findings on the same element share evidence.dom_index.

## Pipeline (Track D)
Run from the repo root: `python -m agents.graph --version before --page home`
(versions: before, after; pages: home, news, tickets, survey).
Graph: load findings -> mapper -> router -> FOR/AGAINST (grey only) -> write output.
Prints a bucket summary and writes data/runs/{run_id}/: input_findings.json,
events.jsonl (what each node added to each finding), output_findings.json, summary.json
(counts, timings, model IDs, prompt versions, tokens).
Run folders are not committed (data/runs/.gitignore).

Agent function contract: each function takes ONE finding dict (docs/finding.example.json
format) and returns ONLY the fields it adds. The graph merges them in; nested dicts like
`ai` are merged one level deep, so never return a rewritten copy of the finding.
- `agents/mapper.py`: `map_finding(finding) -> {"mapping": {"sc", "name", "level", "spec_url", ...}}`; a finding it can't map returns `{"mapping": {"sc", "rejected": reason}}` instead of raising (Track B)
- `agents/router.py`: `route_finding(finding) -> {"bucket": "machine"|"grey"|"human-only", "route_reason": str}` (Track C)
- `agents/evidence.py`: `build_cases(finding) -> {"ai": {"case_for", "case_against", "recommendation": "likely_fail"|"likely_pass"|"unsure", "confidence": None, "citations_verified": bool, "model_id", "prompt_version", "usage": {"input_tokens", "output_tokens"}}}` (Track C, grey findings only). A skipped finding has `case_for` starting `SKIPPED:` and `recommendation: "unsure"`; count it as not evaluated, not as a result.
Until a track's file is merged, agents/graph.py uses a stub in its place.

## Router policy v1 (decided Oct 9)
`route_finding` applies these in order and names the rule in `route_reason`:
1. `scanner_status == "violation"`: `machine`.
2. Page-level notice (`scanner_status == "notice"` and `evidence.selector == "html"`): `human-only`.
   These are human checks, not issues; reports collapse them to one check per page and criterion.
3. Criterion marked `human_only: true` in `rag/wcag20_aa.json`: `human-only`.
4. Everything else (`needs_review`, element-level notices): `grey`. FOR/AGAINST runs on these.

A grey finding counts as an **issue** only when `ai.recommendation == "likely_fail"`. A grey
finding that leans pass or is unsure is "flagged for a human", not an issue. Either way, AI
never sets the final status: only a human decision in `data/reviews/` does.

## Phase history
Kept so anyone catching up can see what earlier weeks built and what is still open.

### Week 1 (Sep 16 to 25): setup, ground truth, baseline scan
Goal: accounts and tools for everyone, one shared repo and AWS account,
and "before AI" baseline numbers. Full steps: Week 1 Setup Guide.
- Repo, folder layout, .gitignore, CONTEXT.md: done
- Finding format (Step 8a): done, committed in week 2 as docs/finding.example.json
- Bedrock model ID (Step 4e): done in week 2 by Abel. PR #2 set Claude Opus 4.6; PR #9 switched
  to Claude Sonnet 4.6, see Stack above.
  Smoke test: scripts/hello_bedrock.py, run from the repo root as python -m scripts.hello_bedrock
- Ground truth (Step 6): data/ground-truth/ground_truth.csv not in the repo yet
- axe baseline (Step 7): baseline_axe.py was never committed; replaced in week 2
  by scanners/axe_scan.py. Baseline recall numbers not recorded yet

## Team conventions
- Who owns which file, branch rules, and the agreed finding format decisions: docs/TRACKS.md
- Finding format everyone codes against: docs/finding.example.json. Read findings with
  `from scanners.findings import load_findings`, not by parsing the JSON by hand.
- Playwright 1.63 has no page.accessibility.snapshot(). Use page.aria_snapshot() or a CDP session.
