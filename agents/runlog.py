"""Run log: every pipeline run writes data/runs/{run_id}/ so any result can be traced and replayed.

Files written per run:
  input_findings.json   what the run started with
  events.jsonl          one line per node per finding: what that node added, or the error it hit
  output_findings.json  final findings after every node
  summary.json          counts, timings, model IDs, prompt versions, tokens
"""
import datetime
import json
import uuid

from scanners.findings import REPO

RUNS_DIR = REPO / "data" / "runs"  # absolute, so runs land here whatever folder you run from


def new_run_id():
    # Sortable by time, unique even if two people run at the same minute
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat()


class RunLog:
    def __init__(self, run_id):
        self.run_id = run_id
        self.dir = RUNS_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events = (self.dir / "events.jsonl").open("a")
        self.timings = {}
        self.errors = []

    def save(self, filename, obj):
        (self.dir / filename).write_text(json.dumps(obj, indent=2, default=str))

    def event(self, node, finding_id, added):
        """Record what one node added to one finding."""
        line = {"t": _now(), "node": node, "finding_id": finding_id, "added": added}
        self._events.write(json.dumps(line, default=str) + "\n")
        self._events.flush()  # so a crash mid-run still leaves a usable log

    def error(self, node, finding_id, exc):
        """Record that one node failed on one finding. The run carries on without it."""
        message = f"{type(exc).__name__}: {exc}"
        self.errors.append({"node": node, "finding_id": finding_id, "error": message})
        line = {"t": _now(), "node": node, "finding_id": finding_id, "error": message}
        self._events.write(json.dumps(line, default=str) + "\n")
        self._events.flush()

    def timing(self, node, seconds):
        self.timings[node] = round(seconds, 3)

    def close(self):
        self._events.close()
