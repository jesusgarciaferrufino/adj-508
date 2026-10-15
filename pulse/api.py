"""PULSE review API (Track F): where a signed-in human records a decision on a finding.

    uvicorn pulse.api:app --port 8000        # run from the repo root

    GET  /runs/latest?version=before&page=home   newest run_id for that page
    GET  /findings?run_id=...                    that run's findings, each with its latest decision
    GET  /shots/{name}                           an image from data/findings/shots/
    POST /decisions                              record one decision (needs Authorization: Bearer <token>)

Hard rule (CONTEXT.md): AI never sets a final conformance status; only a signed-in human
reviewer decides. This API is the only code that writes a human decision, and it writes
only to data/reviews/{version}_{page}.jsonl, one JSON line per decision, append-only.
It never writes to data/findings/ or data/runs/ and never touches a finding's `review` field.

Settings come from environment variables, or from the repo's .env file when a variable is
not set (see pulse/.env.example):
    ADJ_REVIEWER_TOKENS    "name:token,name:token"; who may record decisions (tokens of 16+ characters)
    ADJ_EXTENSION_ORIGIN   the extension's origin, e.g. chrome-extension://<id>, allowed by CORS
    ADJ_PULSE_DOCS         set to 1 to serve FastAPI's /docs page (off by default)
Tokens are secrets: they are compared in constant time and never logged, printed or returned.
The bearer token is the access control; CORS only limits which web pages may read responses.

Saving the same reviewer's latest decision again (same run, same decision, same comment) records
nothing new and returns the existing line with 200 instead of 201.

Run a single worker (the default): the lock that keeps two saves from interleaving is per process.
"""

import hmac
import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, get_args

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from scanners.findings import PageName, Version  # the one list of site versions and pages

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
ENV_FILE = REPO / ".env"

LOCAL_ORIGIN = "http://localhost:8000"
# Host names the API answers to. Anything else is refused, so a web page that points its own
# domain at this machine (DNS rebinding) cannot read findings.
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
MIN_TOKEN_LENGTH = 16  # shorter tokens (such as the "change-me" placeholders) never sign anyone in
RUN_ID_PATTERN = r"^\d{8}-\d{6}-[0-9a-f]{4}$"  # agents/runlog.py: 20261013-010051-e473
RUN_ID_RE = re.compile(RUN_ID_PATTERN)
SHOT_SUFFIXES = {".jpg", ".jpeg", ".png"}
READ_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError)

Decision = Literal["fail", "pass", "not_applicable"]

_write_lock = threading.Lock()  # one decision line is written at a time


# ---------------------------------------------------------------- settings and sign-in


def _env_value(raw: str) -> str:
    """The value part of a KEY=value line: what is inside quotes, or the text before a " #" comment."""
    if raw[:1] in ("'", '"'):
        return raw[1:].partition(raw[0])[0]
    return raw.partition(" #")[0].strip()


def _env_file_values() -> dict[str, str]:
    """KEY=VALUE pairs from the repo's .env file; empty if there is no readable file."""
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    values = {}
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if not sep or not key.strip() or key.lstrip().startswith("#"):
            continue
        values[key.strip()] = _env_value(value.strip())
    return values


def setting(name: str) -> str:
    """An environment variable, or the same name from .env when the variable is not set."""
    return os.environ.get(name) or _env_file_values().get(name, "")


def reviewer_tokens() -> list[tuple[str, str]]:
    """(name, token) pairs from ADJ_REVIEWER_TOKENS. Entries with no name or a too-short token are dropped."""
    pairs = []
    for entry in setting("ADJ_REVIEWER_TOKENS").split(","):
        name, _, token = entry.strip().partition(":")
        if name.strip() and len(token.strip()) >= MIN_TOKEN_LENGTH:
            pairs.append((name.strip(), token.strip()))
    return pairs


def require_reviewer(authorization: Annotated[str | None, Header()] = None) -> str:
    """The signed-in reviewer's name, taken from the bearer token. 401 if it is missing or unknown."""
    scheme, _, presented = (authorization or "").partition(" ")
    presented = presented.strip()
    reviewer = None
    if scheme.lower() == "bearer" and presented:
        for name, token in reviewer_tokens():  # check every pair, so timing doesn't reveal which matched
            if hmac.compare_digest(presented.encode(), token.encode()):
                reviewer = name
    if reviewer is None:
        raise HTTPException(401, "Sign in with a valid reviewer token.", headers={"WWW-Authenticate": "Bearer"})
    return reviewer


def allowed_origins() -> list[str]:
    """Origins CORS lets call the API: this server's own page plus the one extension origin. Never a wildcard."""
    extension = setting("ADJ_EXTENSION_ORIGIN").strip().rstrip("/")
    origins = [LOCAL_ORIGIN]
    if extension and "*" not in extension and extension != LOCAL_ORIGIN:
        origins.append(extension)
    return origins


# ---------------------------------------------------------------- reading runs (read-only)


def runs_dir() -> Path:
    return DATA_DIR / "runs"


def shots_dir() -> Path:
    return DATA_DIR / "findings" / "shots"


def reviews_path(version: str, page: str) -> Path:
    """The decision file for one page: data/reviews/{version}_{page}.jsonl."""
    return DATA_DIR / "reviews" / f"{version}_{page}.jsonl"


class RunNotFoundError(LookupError):
    """There is no usable run with this id."""


def read_summary(run_id: str) -> dict:
    """A run's summary.json, checked for the version and page it covers. Raises RunNotFoundError."""
    if not RUN_ID_RE.fullmatch(run_id):
        raise RunNotFoundError(f"{run_id!r} is not a run id")
    try:
        summary = json.loads((runs_dir() / run_id / "summary.json").read_text(encoding="utf-8"))
    except READ_ERRORS as e:
        raise RunNotFoundError(f"run {run_id} has no readable summary.json") from e
    ok = (isinstance(summary, dict) and summary.get("version") in get_args(Version)
          and summary.get("page") in get_args(PageName))
    if not ok:
        raise RunNotFoundError(f"run {run_id}: summary.json does not name a known version and page")
    return summary


def read_findings(run_id: str) -> list[dict]:
    """The findings in a run's output_findings.json. Entries without a finding_id are left out."""
    if not RUN_ID_RE.fullmatch(run_id):
        raise RunNotFoundError(f"{run_id!r} is not a run id")
    try:
        rows = json.loads((runs_dir() / run_id / "output_findings.json").read_text(encoding="utf-8"))
    except READ_ERRORS as e:
        raise RunNotFoundError(f"run {run_id} has no readable output_findings.json") from e
    if not isinstance(rows, list):
        raise RunNotFoundError(f"run {run_id}: output_findings.json is not a list of findings")
    return [f for f in rows if isinstance(f, dict) and isinstance(f.get("finding_id"), str) and f["finding_id"]]


def latest_run(version: str, page: str) -> tuple[str, dict] | None:
    """(run_id, summary) of the newest finished run for this page, or None. Run ids sort by time.

    The run_id is the folder's name, which is what every other endpoint looks runs up by.
    """
    try:
        names = sorted((p.name for p in runs_dir().iterdir() if RUN_ID_RE.fullmatch(p.name)), reverse=True)
    except OSError:
        return None
    for run_id in names:
        try:
            summary = read_summary(run_id)
            read_findings(run_id)  # a run still being written has no output yet
        except RunNotFoundError:
            continue
        if (summary["version"], summary["page"]) == (version, page):
            return run_id, summary
    return None


# ---------------------------------------------------------------- decisions (the only writes)


def _is_decision_record(record: object) -> bool:
    """True for a line shaped like the ones this API writes: who decided what, on which run, and when."""
    return (isinstance(record, dict)
            and all(isinstance(record.get(k), str) and record[k] for k in ("finding_id", "run_id", "reviewer",
                                                                           "decided_at"))
            and isinstance(record.get("comment", ""), str)
            and record.get("decision") in get_args(Decision))


def latest_decisions(version: str, page: str) -> dict[str, dict]:
    """{finding_id: its most recent decision line} for one page. Unreadable lines are ignored."""
    try:
        raw = reviews_path(version, page).read_bytes()
    except OSError:
        return {}
    latest = {}
    # Split the bytes on "\n" only (splitlines would also break on characters such as U+2028), and
    # parse each line on its own, so a damaged line is skipped whole instead of being half-read.
    for line in raw.split(b"\n"):
        try:
            record = json.loads(line)
            json.dumps(record, ensure_ascii=False).encode("utf-8")  # text we could not send back (half an emoji)
        except (ValueError, RecursionError):  # not JSON, not UTF-8, unsendable, or absurdly nested
            continue
        if _is_decision_record(record):
            latest[record["finding_id"]] = record  # later lines replace earlier ones
    return latest


def record_decision(version: str, page: str, record: dict) -> tuple[dict, bool]:
    """Append one JSON line to the page's decision file. Returns (the line now on file, was it new).

    Existing lines are never changed. If the latest decision on the finding is already this
    reviewer's, on this run, with the same decision and comment, nothing is written and that
    existing line is returned.
    """
    path = reviews_path(version, page)
    line = json.dumps(record) + "\n"  # ASCII-only JSON: always exactly one line, whatever the comment holds
    with _write_lock:
        last = latest_decisions(version, page).get(record["finding_id"])
        if last and all(last.get(k) == record[k] for k in ("reviewer", "run_id", "decision", "comment")):
            return last, False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab+") as fh:
            if fh.tell() > 0:  # a crash can leave a half-written last line: start ours on a fresh one
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())  # on disk before we answer 201
    return record, True


class DecisionIn(BaseModel):
    """The body of POST /decisions. Anything else in the body (such as a reviewer name) is ignored."""

    finding_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    decision: Decision
    comment: str | None = Field(default=None, max_length=4000)


# ---------------------------------------------------------------- the app


def create_app() -> FastAPI:
    """Build the API. CORS origins are read once, here."""
    docs = setting("ADJ_PULSE_DOCS") == "1"  # the docs pages load scripts from a CDN, so they are opt-in
    app = FastAPI(title="PULSE review API", version="0.1.0", docs_url="/docs" if docs else None,
                  redoc_url=None, openapi_url="/openapi.json" if docs else None)
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins(), allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Say which field is wrong and why, without echoing back what was sent.
        problems = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
                    for e in exc.errors()]
        return JSONResponse({"detail": problems}, status_code=422)

    @app.get("/runs/latest")
    def get_latest_run(version: Version, page: PageName) -> dict:
        found = latest_run(version, page)
        if found is None:
            raise HTTPException(404, f"No finished run for {version}/{page}. Run: python -m agents.graph "
                                     f"--version {version} --page {page}")
        run_id, summary = found
        return {"run_id": run_id, "version": version, "page": page,
                "total": summary.get("total"), "buckets": summary.get("buckets"), "stubs": summary.get("stubs")}

    @app.get("/findings")
    def get_findings(run_id: Annotated[str, Query(pattern=RUN_ID_PATTERN)]) -> dict:
        try:
            summary = read_summary(run_id)
            findings = read_findings(run_id)
        except RunNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        version, page = summary["version"], summary["page"]
        decisions = latest_decisions(version, page)
        return {"run_id": run_id, "version": version, "page": page, "stubs": summary.get("stubs"),
                "findings": [{**f, "decision": decisions.get(f["finding_id"])} for f in findings]}

    @app.get("/shots/{name}")
    def get_shot(name: str) -> FileResponse:
        folder = shots_dir().resolve()
        # Only a plain file name is accepted; the resolved path must still be inside the shots folder.
        plain = "\x00" not in name and name == Path(name).name and not name.startswith(".")
        if not plain or Path(name).suffix.lower() not in SHOT_SUFFIXES:
            raise HTTPException(404, "No such screenshot.")
        try:
            target = (folder / name).resolve()
            found = target.is_relative_to(folder) and target.is_file()
        except (OSError, ValueError):
            found = False
        if not found:
            raise HTTPException(404, "No such screenshot.")
        return FileResponse(target)

    @app.post("/decisions", status_code=201)
    def post_decision(body: DecisionIn, reviewer: Annotated[str, Depends(require_reviewer)],
                      response: Response) -> dict:
        try:
            summary = read_summary(body.run_id)
            known = {f["finding_id"] for f in read_findings(body.run_id)}
        except RunNotFoundError as e:
            raise HTTPException(422, str(e)) from None
        if body.finding_id not in known:
            raise HTTPException(422, f"{body.finding_id} is not a finding in run {body.run_id}")
        record = {
            "finding_id": body.finding_id,
            "run_id": body.run_id,
            "reviewer": reviewer,  # from the token, never from the request body
            "decision": body.decision,
            "comment": body.comment or "",
            "decided_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        try:
            saved, created = record_decision(summary["version"], summary["page"], record)
        except OSError as e:
            raise HTTPException(500, f"The decision could not be saved: {e.strerror or e}") from None
        if not created:
            response.status_code = 200  # already on file as this reviewer's latest decision
        return saved

    return app


app = create_app()
