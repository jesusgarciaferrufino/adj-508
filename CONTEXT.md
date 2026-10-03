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
Bedrock region: us-east-1. Model ID: us.anthropic.claude-sonnet-4-6 (Claude Sonnet 4.6).
Target model is Claude Fable 5.1 (us.anthropic.claude-fable-5-1). Access for our account is pending
with AWS support. Switch to it once granted. Bedrock Claude models must be called via the us. inference profile ID.

## Current phase
Week 2: one grey-area finding end to end (capture, scanners, mapper, router, FOR/AGAINST).
- Track A output is ready: data/findings/{before,after}_{home,news,tickets,survey}.json
  (format: docs/finding.example.json). Tracks B, C and D read these files and do not
  need to run the scanners. Findings on the same element share evidence.dom_index.

## Phase history
Kept so anyone catching up can see what earlier weeks built and what is still open.

### Week 1 (Sep 16 to 25): setup, ground truth, baseline scan
Goal: accounts and tools for everyone, one shared repo and AWS account,
and "before AI" baseline numbers. Full steps: Week 1 Setup Guide.
- Repo, folder layout, .gitignore, CONTEXT.md: done
- Finding format (Step 8a): done, committed in week 2 as docs/finding.example.json
- Bedrock model ID (Step 4e): done in week 2 by Abel (PR #2): Claude Sonnet 4.6, see Stack above.
  Smoke test: scripts/hello_bedrock.py
- Ground truth (Step 6): data/ground-truth/ground_truth.csv not in the repo yet
- axe baseline (Step 7): baseline_axe.py was never committed; replaced in week 2
  by scanners/axe_scan.py. Baseline recall numbers not recorded yet

## Team conventions
- Who owns which file, branch rules, and the agreed finding format decisions: docs/TRACKS.md
- Finding format everyone codes against: docs/finding.example.json. Read findings with
  `from scanners.findings import load_findings`, not by parsing the JSON by hand.
- Playwright 1.63 has no page.accessibility.snapshot(). Use page.aria_snapshot() or a CDP session.
