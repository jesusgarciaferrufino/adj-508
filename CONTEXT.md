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
Week 1: setup, ground truth, baseline scan.
