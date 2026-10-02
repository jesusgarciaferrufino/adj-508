"""TEMPORARY bridge until Track A's merge.py lands: Pa11y JSON -> findings file.

    npx pa11y --standard WCAG2AA --include-warnings --include-notices --reporter json \
        https://www.w3.org/WAI/demos/bad/before/home.html > data/scans/pa11y_before_home.json
    python scripts/pa11y_to_findings.py data/scans/pa11y_before_home.json before home
"""
import json
import sys
from pathlib import Path

src, version, page = sys.argv[1], sys.argv[2], sys.argv[3]
issues = json.loads(Path(src).read_text())
issues = sorted(issues, key=lambda i: (i.get("selector", ""), i.get("code", "")))  # stable IDs
seen, out = set(), []
for i in issues:
    key = (i.get("selector"), i.get("code"))
    if key in seen:
        continue
    seen.add(key)
    out.append({
        "finding_id": f"F-{version}-{page}-{len(out) + 1:04d}",
        "page": page, "version": version,
        "selector": i.get("selector", ""), "html_snippet": i.get("context", ""),
        "scanner_message": i.get("message", ""),
        "scanner_status": "violation" if i.get("type") == "error" else "needs_review",
        "sources": [{"scanner": "pa11y", "code": i.get("code", "")}],
    })
dest = Path(f"data/findings/{version}_{page}.json")
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, indent=2))
print(f"{len(out)} findings -> {dest}")
