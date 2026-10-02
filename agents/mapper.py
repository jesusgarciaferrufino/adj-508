"""Track B requirement mapper.

    python -m agents.mapper --in data/findings/before_home.json
    python -m agents.mapper --in data/findings/before_home.json --no-llm

Path 1 (deterministic): use the scanner's WCAG tags/codes, look them up in the corpus.
Path 2 (LLM fallback): only for findings with no usable tag. The model picks from a
shortlist; anything outside the shortlist is rejected in code.

Each finding gets two fields ADDED (nothing existing is rewritten):
    mapped_criteria: [{"sc", "name", "level"}]
    mapping: {"method": tag|llm|unmapped, "reason", "candidates", "rejected", ...}

Adjust FIELD_* below if the agreed docs/finding.example.json uses other key names.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from string import Template

CORPUS = Path("rag/wcag20_aa.json")
PROMPT_NAME = "mapper_v1"
PROMPT_PATH = Path("prompts") / f"{PROMPT_NAME}.md"
CACHE = Path("data/cache/mapper")
MODEL_ID = os.environ.get("MAPPER_MODEL_ID", "")  # small/cheap Bedrock model
REGION = os.environ.get("AWS_REGION", "us-east-1")

FIELD_CRITERIA = ["wcag", "wcag_criteria", "criteria", "tags"]
FIELD_SNIPPET, FIELD_SELECTOR, FIELD_MESSAGE = "html_snippet", "selector", "scanner_message"

DOTTED = re.compile(r"^\d\.\d\.\d$")
AXE_TAG = re.compile(r"^wcag(\d)(\d)(\d+)$")                      # wcag111 -> 1.1.1
PA11Y = re.compile(r"Guideline\d+_\d+\.(\d+)_(\d+)_(\d+)")         # ...Guideline1_1.1_1_1.H37
FIRST_TAG = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9]*)")

H = ["1.3.1", "2.4.6"]
FORM = ["1.3.1", "3.3.2", "4.1.2", "2.4.6"]
TEXTY = ["1.3.1", "1.4.3", "1.4.1"]
MEDIA = ["1.2.1", "1.2.2", "1.2.3", "1.4.2"]
TAG_HINTS = {
    "img": ["1.1.1", "1.4.5", "2.4.4"], "area": ["1.1.1", "2.4.4"],
    "a": ["2.4.4", "4.1.2", "1.4.1"], "button": ["4.1.2", "2.1.1", "2.4.6"],
    "input": FORM, "select": FORM, "textarea": FORM, "label": FORM, "fieldset": FORM,
    "table": ["1.3.1", "1.3.2"], "th": ["1.3.1"], "td": ["1.3.1"],
    **{f"h{i}": H for i in range(1, 7)},
    **{t: TEXTY for t in ["b", "strong", "font", "span", "div", "p", "i", "em"]},
    "html": ["3.1.1"], "title": ["2.4.2"], "frame": ["2.4.1", "4.1.2"], "iframe": ["4.1.2"],
    **{t: MEDIA for t in ["video", "audio", "object", "embed"]},
}
DEFAULT_CANDIDATES = ["1.3.1", "4.1.2", "1.1.1", "2.4.4", "1.4.3"]
MAX_CANDIDATES = 8


def load_corpus():
    return {r["sc"]: r for r in json.loads(CORPUS.read_text())}


def to_dotted(x):
    x = str(x).strip()
    if DOTTED.match(x):
        return x
    m = AXE_TAG.match(x) or PA11Y.search(x)
    return ".".join(m.groups()) if m else None


def raw_criteria(f):
    vals = []
    for k in FIELD_CRITERIA:
        v = f.get(k)
        vals += v if isinstance(v, list) else ([v] if v else [])
    vals.append(f.get("code", ""))
    for s in f.get("sources", []):
        if isinstance(s, dict):
            vals += [s.get("code", "")] + list(s.get("tags", []))
    return [d for d in (to_dotted(v) for v in vals if v) if d]


def shortlist(f, corpus):
    m = FIRST_TAG.search(f.get(FIELD_SNIPPET, "") or "")
    tag = m.group(1).lower() if m else ""
    out = []
    for sc in TAG_HINTS.get(tag, []) + DEFAULT_CANDIDATES:
        if sc in corpus and sc not in out:
            out.append(sc)
    return out[:MAX_CANDIDATES]


def call_llm(f, candidates, corpus):
    lines = [f"{sc} {corpus[sc]['name']} (Level {corpus[sc]['level']}): {corpus[sc].get('intent', '')}"
             for sc in candidates]
    prompt = Template(PROMPT_PATH.read_text()).safe_substitute(
        candidates="\n".join(lines),
        selector=f.get(FIELD_SELECTOR, ""),
        html_snippet=(f.get(FIELD_SNIPPET, "") or "")[:1500],
        scanner_message=f.get(FIELD_MESSAGE, ""),
    )
    key = hashlib.sha256((PROMPT_NAME + MODEL_ID + prompt).encode()).hexdigest()
    cache_file = CACHE / f"{key}.json"
    if cache_file.exists():
        return {**json.loads(cache_file.read_text()), "cached": True}

    import boto3  # imported here so --no-llm runs without AWS set up
    client = boto3.client("bedrock-runtime", region_name=REGION)
    resp = client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 300, "temperature": 0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    result = {"raw": text, "usage": resp.get("usage", {})}
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result))
    return {**result, "cached": False}


def parse_llm(text):
    text = re.sub(r"```(json)?", "", text).strip()
    try:
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return [to_dotted(c) for c in obj.get("criteria", [])], obj.get("reason", "")
    except (ValueError, AttributeError):
        return [], f"unparseable model output: {text[:120]}"


def map_one(f, corpus, use_llm):
    raw = raw_criteria(f)
    valid = list(dict.fromkeys(c for c in raw if c in corpus))
    rejected = sorted(set(c for c in raw if c not in corpus))  # e.g. WCAG 2.1 criteria
    mapping = {"rejected": rejected}

    if valid:
        mapping.update(method="tag", reason="scanner tag/code looked up in corpus")
    elif use_llm and MODEL_ID:
        cands = shortlist(f, corpus)
        out = call_llm(f, cands, corpus)
        picked, reason = parse_llm(out["raw"])
        valid = [c for c in picked if c in cands]                   # enforced in code
        mapping.update(
            method="llm" if valid else "unmapped", reason=reason, candidates=cands,
            rejected=rejected + [c for c in picked if c not in cands],
            prompt_version=PROMPT_NAME, model_id=MODEL_ID,
            usage=out.get("usage", {}), cached=out["cached"],
        )
    else:
        mapping.update(method="unmapped", reason="no usable tag; LLM fallback off")

    f["mapped_criteria"] = [{"sc": c, "name": corpus[c]["name"], "level": corpus[c]["level"]}
                            for c in valid]
    f["mapping"] = mapping
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    corpus = load_corpus()
    findings = json.loads(Path(a.inp).read_text())
    findings = [map_one(f, corpus, not a.no_llm) for f in findings]

    out = Path(a.out or a.inp.replace(".json", ".mapped.json"))
    out.write_text(json.dumps(findings, indent=2))
    counts = {}
    for f in findings:
        counts[f["mapping"]["method"]] = counts.get(f["mapping"]["method"], 0) + 1
    print(f"{len(findings)} findings -> {counts}. Wrote {out}")


if __name__ == "__main__":
    main()
