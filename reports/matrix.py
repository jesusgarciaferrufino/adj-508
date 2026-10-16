"""Track E compliance matrix: one row per WCAG 2.0 A/AA criterion for one page of a pipeline run.

    python -m reports.matrix --run <run_id>

Run it as a module from the repo root; `python reports/matrix.py` can't import agents/, rag/ or scanners/.

Reads:
    data/runs/{run_id}/summary.json            which page the run covers, and any stub nodes
    data/runs/{run_id}/output_findings.json    the run's findings (bucket, evidence, ai, ...)
    data/reviews/{version}_{page}.jsonl        human decisions from PULSE (Track F); the last line per finding wins
    rag/wcag20_aa.json, rag/section508_map.json  name, level and Section 508 reference for all 38 criteria

Writes data/reports/{run_id}/matrix_{version}_{page}.csv and .md.

Hard rule (CONTEXT.md): AI never sets a conformance status. `status` comes ONLY from human decisions,
never from bucket, scanner_status or ai.recommendation. AI leaning is shown in its own column, labelled as AI.
"""

from agents import runlog
from scanners.findings import REPO

RUNS_DIR = runlog.RUNS_DIR  # where pipeline runs live; tests point this at a temp folder
REVIEWS_DIR = REPO / "data" / "reviews"
REPORTS_DIR = REPO / "data" / "reports"

# A finding's human decision: the only values PULSE may record
DECISIONS = {"fail", "pass", "not_applicable"}

# A criterion's status, worded exactly as in the Track E handoff. The Week 5 ACR reads these.
STATUS_FAIL = "Does Not Support (human-confirmed)"
STATUS_SUPPORTS = "Supports (human-confirmed)"
STATUS_PENDING = "Pending human review"
STATUS_NO_FINDINGS = "No automated findings; human check required"
