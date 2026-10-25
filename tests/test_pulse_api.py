"""Track F: the PULSE review API. Every test uses a temp data folder and made-up tokens."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pulse import api

ROOT = Path(__file__).resolve().parent.parent
RUN = "20261001-120000-aaaa"
OLDER_RUN = "20260930-120000-bbbb"
FINDING = "F-before-home-el0210-2.4.4"
OTHER_FINDING = "F-before-home-el0081-1.1.1"
DANIEL = "fake-token-for-daniel-0001"  # made up for these tests; not real credentials
JESUS = "fake-token-for-jesus-00002"
EXTENSION = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


def local_client():
    """A test client that calls the API as http://localhost, the only kind of host name it answers to."""
    return TestClient(api.create_app(), base_url="http://localhost")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def body(**changes):
    return {"finding_id": FINDING, "run_id": RUN, "decision": "fail", "comment": "Link text is 'Click here'.",
            **changes}


def write_run(data, run_id, version="before", page="home", finding_ids=(FINDING, OTHER_FINDING), finished=True):
    """A run folder shaped like agents/graph.py's output, built from the real example finding."""
    example = json.loads((ROOT / "docs" / "finding.example.json").read_text())
    folder = data / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps(
        {"run_id": run_id, "version": version, "page": page, "total": len(finding_ids),
         "buckets": {"grey": len(finding_ids)}, "stubs": ["router", "evidence"]}))
    if finished:
        findings = [{**example, "finding_id": fid, "site_version": version, "page": page, "bucket": "grey"}
                    for fid in finding_ids]
        (folder / "output_findings.json").write_text(json.dumps(findings))


def snapshot(folder):
    """Every file under a folder with its bytes, to prove nothing was changed."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A temp data folder with one finished run and one screenshot, and fake reviewer tokens."""
    data = tmp_path / "data"
    write_run(data, RUN)
    shots = data / "findings" / "shots"
    shots.mkdir(parents=True)
    (shots / "before_home_el0210.jpg").write_bytes(b"\xff\xd8 fake jpeg")
    (data / "secret.jpg").write_bytes(b"outside the shots folder")
    monkeypatch.setattr(api, "DATA_DIR", data)
    # From here on the API reads no real .env. (Importing pulse.api has already read the CORS origin
    # from it once; create_app() below reads it again from the fake environment set here.)
    monkeypatch.setattr(api, "ENV_FILE", tmp_path / "no-such.env")
    monkeypatch.setenv("ADJ_REVIEWER_TOKENS", f"daniel:{DANIEL},jesus:{JESUS}")
    monkeypatch.setenv("ADJ_EXTENSION_ORIGIN", EXTENSION)
    monkeypatch.delenv("ADJ_PULSE_DOCS", raising=False)
    return data


@pytest.fixture
def client(data):
    return local_client()


def review_lines(data, name="before_home.jsonl"):
    path = data / "reviews" / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------- POST /decisions: sign-in


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer not-a-real-token-at-all"},
    {"Authorization": "Bearer "},
    {"Authorization": "Bearer daniel"},  # the name is not the token
    {"Authorization": f"Basic {DANIEL}"},
    {"Authorization": DANIEL},
], ids=["no header", "unknown token", "empty token", "name as token", "wrong scheme", "no scheme"])
def test_decision_without_a_valid_token_is_401_and_writes_nothing(client, data, headers):
    r = client.post("/decisions", json=body(), headers=headers)
    assert r.status_code == 401
    assert not (data / "reviews").exists()


def test_401_leaves_an_existing_review_file_unchanged(client, data):
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    before = snapshot(data / "reviews")
    assert client.post("/decisions", json=body(decision="pass")).status_code == 401
    assert client.post("/decisions", json=body(decision="pass"), headers=auth("x" * 30)).status_code == 401
    assert snapshot(data / "reviews") == before


def test_sign_in_is_checked_before_the_body(client, data):
    r = client.post("/decisions", json={"decision": "maybe"})
    assert r.status_code == 401
    assert not (data / "reviews").exists()


def test_short_placeholder_tokens_never_sign_in(client, data, monkeypatch):
    monkeypatch.setenv("ADJ_REVIEWER_TOKENS", "jesus:change-me,daniel:change-me-too")
    assert client.post("/decisions", json=body(), headers=auth("change-me")).status_code == 401
    assert not (data / "reviews").exists()


def test_tokens_can_come_from_the_env_file(client, data, tmp_path, monkeypatch):
    monkeypatch.delenv("ADJ_REVIEWER_TOKENS")
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 401
    env_file = tmp_path / ".env"
    env_file.write_text(f"# reviewers\nADJ_REVIEWER_TOKENS=\"daniel:{DANIEL}\"\n")
    monkeypatch.setattr(api, "ENV_FILE", env_file)
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    assert client.post("/decisions", json=body(), headers=auth(JESUS)).status_code == 401


# ---------------------------------------------------------------- POST /decisions: what is written


def test_valid_token_appends_exactly_one_line_with_the_reviewer_from_the_token(client, data):
    r = client.post("/decisions", json=body(reviewer="mallory", final_status="fail"), headers=auth(DANIEL))
    assert r.status_code == 201
    lines = review_lines(data)
    assert len(lines) == 1
    line = lines[0]
    assert set(line) == {"finding_id", "run_id", "reviewer", "decision", "comment", "decided_at"}
    assert (line["finding_id"], line["run_id"], line["decision"]) == (FINDING, RUN, "fail")
    assert line["reviewer"] == "daniel", "the reviewer comes from the token, never from the body"
    assert line["comment"] == "Link text is 'Click here'."
    assert line["decided_at"].endswith("Z") and "T" in line["decided_at"]
    assert r.json() == line
    assert [p.name for p in (data / "reviews").iterdir()] == ["before_home.jsonl"]


def test_tokens_are_never_written_or_returned(client, data):
    r = client.post("/decisions", json=body(), headers=auth(JESUS))
    assert review_lines(data)[0]["reviewer"] == "jesus"
    saved = (data / "reviews" / "before_home.jsonl").read_text()
    assert JESUS not in saved and JESUS not in r.text and DANIEL not in saved


def test_decisions_are_append_only_and_the_latest_one_is_attached(client, data):
    assert client.post("/decisions", json=body(decision="fail"), headers=auth(DANIEL)).status_code == 201
    first = (data / "reviews" / "before_home.jsonl").read_bytes()
    assert client.post("/decisions", json=body(decision="pass", comment=None), headers=auth(JESUS)).status_code == 201
    saved = (data / "reviews" / "before_home.jsonl").read_bytes()
    assert saved.startswith(first), "earlier lines are never changed"
    assert [(d["reviewer"], d["decision"], d["comment"]) for d in review_lines(data)] == [
        ("daniel", "fail", "Link text is 'Click here'."), ("jesus", "pass", "")]

    findings = {f["finding_id"]: f for f in client.get("/findings", params={"run_id": RUN}).json()["findings"]}
    assert findings[FINDING]["decision"]["decision"] == "pass"
    assert findings[FINDING]["decision"]["reviewer"] == "jesus"
    assert findings[OTHER_FINDING]["decision"] is None


def test_saving_the_same_decision_again_records_nothing_new(client, data):
    first = client.post("/decisions", json=body(), headers=auth(DANIEL))
    again = client.post("/decisions", json=body(), headers=auth(DANIEL))
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json(), "the line already on file is returned"
    assert len(review_lines(data)) == 1

    # Another reviewer agreeing, a changed comment and a changed decision are all new records.
    assert client.post("/decisions", json=body(), headers=auth(JESUS)).status_code == 201
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201  # jesus is now the latest
    assert client.post("/decisions", json=body(comment="Checked again."), headers=auth(DANIEL)).status_code == 201
    assert client.post("/decisions", json=body(comment="Checked again.", decision="pass"),
                       headers=auth(DANIEL)).status_code == 201
    assert client.post("/decisions", json=body(comment="Checked again.", decision="pass"),
                       headers=auth(DANIEL)).status_code == 200
    assert [(d["reviewer"], d["decision"]) for d in review_lines(data)] == [
        ("daniel", "fail"), ("jesus", "fail"), ("daniel", "fail"), ("daniel", "fail"), ("daniel", "pass")]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\x0b", "\x0c", "\x1c"],
                         ids=["U+2028", "U+2029", "U+0085", "vertical tab", "form feed", "file separator"])
def test_a_comment_with_an_unusual_line_break_character_stays_visible(client, data, separator):
    comment = f"Pasted from a document{separator}second part"
    assert client.post("/decisions", json=body(comment=comment), headers=auth(DANIEL)).status_code == 201
    assert (data / "reviews" / "before_home.jsonl").read_bytes().count(b"\n") == 1
    findings = client.get("/findings", params={"run_id": RUN}).json()["findings"]
    assert findings[0]["decision"]["comment"] == comment


def test_a_damaged_byte_hides_one_line_not_the_whole_file(client, data):
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    with (data / "reviews" / "before_home.jsonl").open("ab") as fh:
        fh.write(b'{"finding_id": "' + OTHER_FINDING.encode() + b'", "decision": "pa\xff\xfess"}\n\xff\xfe\n')
        # Damaged inside a value: it must be skipped, not read as daniel's decision with odd characters.
        fh.write(b'{"finding_id": "' + FINDING.encode() + b'", "reviewer": "jes\xffus", "decision": "pass"}\n')
    findings = {f["finding_id"]: f for f in client.get("/findings", params={"run_id": RUN}).json()["findings"]}
    assert findings[FINDING]["decision"]["decision"] == "fail"
    assert findings[FINDING]["decision"]["reviewer"] == "daniel"
    assert findings[OTHER_FINDING]["decision"] is None


@pytest.mark.parametrize("line", [
    "[" * 100_000,
    '{"finding_id": "F-before-home-el0210-2.4.4", "decision": "pass", "n": ' + "9" * 5000 + "}",
    '{"finding_id": "F-before-home-el0210-2.4.4", "decision": "approved"}',
    '{"finding_id": "F-before-home-el0210-2.4.4"}',
    '"just a string"',
    '{"finding_id": "F-before-home-el0210-2.4.4", "decision": "pass"}',
    '{"finding_id": "F-before-home-el0210-2.4.4", "run_id": "20261001-120000-aaaa", "reviewer": ["jesus"], '
    '"decision": "pass", "decided_at": "2026-10-01T00:00:00Z"}',
    '{"finding_id": "F-before-home-el0210-2.4.4", "run_id": "20261001-120000-aaaa", "reviewer": "jesus", '
    '"decision": "pass", "comment": "half an emoji \\ud800", "decided_at": "2026-10-01T00:00:00Z"}',
], ids=["deeply nested", "huge number", "unknown decision", "no decision", "not an object", "no reviewer",
        "reviewer not text", "half an emoji"])
def test_a_malformed_line_never_breaks_the_page(client, data, line):
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    with (data / "reviews" / "before_home.jsonl").open("a") as fh:
        fh.write(line + "\n")
    findings = client.get("/findings", params={"run_id": RUN})
    assert findings.status_code == 200
    assert findings.json()["findings"][0]["decision"]["decision"] == "fail"
    assert client.post("/decisions", json=body(decision="pass"), headers=auth(JESUS)).status_code == 201


def test_a_half_written_last_line_does_not_swallow_the_next_decision(client, data):
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    with (data / "reviews" / "before_home.jsonl").open("ab") as fh:
        fh.write(b'{"finding_id": "F-before-home-el0081-1.1.1", "run_id": "2026')  # a crash mid-write: no newline
    r = client.post("/decisions", json=body(finding_id=OTHER_FINDING, decision="pass"), headers=auth(JESUS))
    assert r.status_code == 201
    findings = {f["finding_id"]: f for f in client.get("/findings", params={"run_id": RUN}).json()["findings"]}
    assert findings[OTHER_FINDING]["decision"]["decision"] == "pass"
    assert findings[FINDING]["decision"]["decision"] == "fail"
    raw = (data / "reviews" / "before_home.jsonl").read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 3  # first line, the torn line closed off, the new line


def test_a_multi_line_comment_is_still_one_line_in_the_file(client, data):
    comment = "First line.\nSecond line.\r\n{\"decision\": \"pass\"}"
    assert client.post("/decisions", json=body(comment=comment), headers=auth(DANIEL)).status_code == 201
    assert len((data / "reviews" / "before_home.jsonl").read_text().splitlines()) == 1
    assert review_lines(data)[0]["comment"] == comment


def test_each_page_gets_its_own_file(client, data):
    write_run(data, "20261002-120000-cccc", version="after", page="news", finding_ids=("F-after-news-el0001-1.1.1",))
    r = client.post("/decisions", headers=auth(DANIEL),
                    json=body(run_id="20261002-120000-cccc", finding_id="F-after-news-el0001-1.1.1"))
    assert r.status_code == 201
    assert len(review_lines(data, "after_news.jsonl")) == 1
    assert review_lines(data) == []


# ---------------------------------------------------------------- POST /decisions: validation


@pytest.mark.parametrize("changes", [
    {"decision": "maybe"},
    {"decision": "FAIL"},
    {"decision": None},
    {"finding_id": "F-before-home-el9999-1.1.1"},  # not in this run
    {"finding_id": ""},
    {"run_id": "20250101-000000-dddd"},  # no such run
    {"run_id": "../../findings"},
    {"comment": "x" * 5000},
], ids=["bad decision", "wrong case", "null decision", "unknown finding", "empty finding", "unknown run",
        "path as run id", "comment too long"])
def test_invalid_decision_is_422_and_writes_nothing(client, data, changes):
    r = client.post("/decisions", json=body(**changes), headers=auth(DANIEL))
    assert r.status_code == 422
    assert not (data / "reviews").exists()


def test_a_run_that_is_still_being_written_cannot_be_decided_on(client, data):
    write_run(data, "20261003-120000-eeee", finished=False)
    r = client.post("/decisions", json=body(run_id="20261003-120000-eeee"), headers=auth(DANIEL))
    assert r.status_code == 422
    assert not (data / "reviews").exists()


def test_the_api_never_changes_findings_or_runs_and_never_writes_final_status(client, data):
    before = snapshot(data / "runs"), snapshot(data / "findings")
    client.get("/runs/latest", params={"version": "before", "page": "home"})
    client.get("/findings", params={"run_id": RUN})
    client.get("/shots/before_home_el0210.jpg")
    assert client.post("/decisions", json=body(final_status="fail"), headers=auth(DANIEL)).status_code == 201
    assert (snapshot(data / "runs"), snapshot(data / "findings")) == before
    assert "final_status" not in (data / "reviews" / "before_home.jsonl").read_text()
    assert sorted(p.name for p in data.iterdir()) == ["findings", "reviews", "runs", "secret.jpg"]
    assert "final_status" not in (ROOT / "pulse" / "api.py").read_text()


# ---------------------------------------------------------------- GET /runs/latest and /findings


def test_latest_run_is_the_newest_finished_run_for_that_page(client, data):
    write_run(data, OLDER_RUN)
    write_run(data, "20261005-120000-ffff", page="news")  # newer, but another page
    write_run(data, "20261006-120000-0000", finished=False)  # newer, but no output yet
    (data / "runs" / "20261007-120000-1111").mkdir()  # newer, but empty
    (data / "runs" / "notes.txt").write_text("not a run")
    r = client.get("/runs/latest", params={"version": "before", "page": "home"})
    assert r.status_code == 200
    assert r.json() == {"run_id": RUN, "version": "before", "page": "home", "total": 2,
                        "buckets": {"grey": 2}, "stubs": ["router", "evidence"]}
    assert client.get("/runs/latest", params={"version": "before", "page": "news"}).json()["run_id"] \
        == "20261005-120000-ffff"


def test_latest_run_id_is_the_folder_name_even_if_the_summary_disagrees(client, data):
    write_run(data, "20261008-120000-9999")
    summary = data / "runs" / "20261008-120000-9999" / "summary.json"
    for wrong in ("../../x", None):
        summary.write_text(json.dumps({"run_id": wrong, "version": "before", "page": "home", "total": 2}))
        run_id = client.get("/runs/latest", params={"version": "before", "page": "home"}).json()["run_id"]
        assert run_id == "20261008-120000-9999"
        assert client.get("/findings", params={"run_id": run_id}).status_code == 200


def test_latest_run_404_when_there_is_none_and_422_for_an_unknown_page(client):
    assert client.get("/runs/latest", params={"version": "after", "page": "survey"}).status_code == 404
    assert client.get("/runs/latest", params={"version": "before", "page": "checkout"}).status_code == 422
    assert client.get("/runs/latest", params={"version": "before"}).status_code == 422


def test_findings_come_back_whole_with_no_decision_yet(client):
    r = client.get("/findings", params={"run_id": RUN})
    assert r.status_code == 200
    payload = r.json()
    assert (payload["run_id"], payload["version"], payload["page"]) == (RUN, "before", "home")
    assert [f["finding_id"] for f in payload["findings"]] == [FINDING, OTHER_FINDING]
    example = json.loads((ROOT / "docs" / "finding.example.json").read_text())
    for f in payload["findings"]:
        assert f["decision"] is None
        assert f["evidence"] == example["evidence"] and f["ai"] == example["ai"]
        assert f["review"]["final_status"] is None


def test_findings_404_for_an_unknown_run_and_422_for_a_bad_id(client):
    assert client.get("/findings", params={"run_id": "20250101-000000-dddd"}).status_code == 404
    assert client.get("/findings", params={"run_id": "../../etc"}).status_code == 422
    assert client.get("/findings").status_code == 422


def test_a_decision_follows_the_finding_into_a_newer_run(client, data):
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    newer = "20261009-120000-2222"
    write_run(data, newer)  # the pipeline is run again later
    findings = client.get("/findings", params={"run_id": newer}).json()["findings"]
    decision = next(f for f in findings if f["finding_id"] == FINDING)["decision"]
    assert decision["decision"] == "fail" and decision["run_id"] == RUN  # says which run it was made on

    # Confirming the same decision against the newer run is a new record, not a repeat.
    assert client.post("/decisions", json=body(run_id=newer), headers=auth(DANIEL)).status_code == 201
    assert client.post("/decisions", json=body(run_id=newer), headers=auth(DANIEL)).status_code == 200
    assert [d["run_id"] for d in review_lines(data)] == [RUN, newer]


def test_a_damaged_line_in_the_review_file_is_skipped(client, data):
    assert client.post("/decisions", json=body(), headers=auth(DANIEL)).status_code == 201
    with (data / "reviews" / "before_home.jsonl").open("a") as fh:
        fh.write("{not json\n[]\n")
    findings = client.get("/findings", params={"run_id": RUN}).json()["findings"]
    assert findings[0]["decision"]["decision"] == "fail"


# ---------------------------------------------------------------- GET /shots/{name}


def test_shot_is_served(client):
    r = client.get("/shots/before_home_el0210.jpg")
    assert r.status_code == 200
    assert r.content == b"\xff\xd8 fake jpeg"
    assert r.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("path", [
    "/shots/../secret.jpg",
    "/shots/..%2Fsecret.jpg",
    "/shots/%2e%2e%2fsecret.jpg",
    "/shots/%2e%2e",
    "/shots/..%5Csecret.jpg",
    "/shots/....//secret.jpg",
    "/shots/%2Fetc%2Fpasswd",
    "/shots/.hidden.jpg",
    "/shots/missing.jpg",
    "/shots/notes.txt",
    "/shots/a%00.jpg",
    "/shots/before_home_el0210.jpg%00.txt",
], ids=["dot-dot", "encoded slash", "encoded dots", "just dot-dot", "backslash", "doubled", "absolute", "hidden",
        "missing", "not an image", "null byte", "null byte suffix"])
def test_shots_rejects_path_tricks_and_unknown_files_with_404(client, data, path):
    (data / "findings" / "shots" / ".hidden.jpg").write_bytes(b"hidden")
    (data / "findings" / "shots" / "notes.txt").write_text("not an image")
    r = client.get(path)
    assert r.status_code == 404
    assert b"outside the shots folder" not in r.content


def test_shots_does_not_follow_a_link_out_of_the_folder(client, data):
    link = data / "findings" / "shots" / "link.jpg"
    try:
        link.symlink_to(data / "secret.jpg")
    except OSError:
        pytest.skip("symlinks not available here")
    assert client.get("/shots/link.jpg").status_code == 404


# ---------------------------------------------------------------- CORS


def test_cors_allows_only_the_extension_and_localhost(client):
    for origin in (EXTENSION, "http://localhost:8000"):
        r = client.get("/findings", params={"run_id": RUN}, headers={"Origin": origin})
        assert r.headers.get("access-control-allow-origin") == origin
    for origin in ("https://evil.example", "chrome-extension://someotherextensionidsomeotherext", "http://localhost:3000"):
        r = client.get("/findings", params={"run_id": RUN}, headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers

    preflight = client.options("/decisions", headers={
        "Origin": EXTENSION, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == EXTENSION


@pytest.mark.parametrize("value", ["*", "", "chrome-extension://*", "https://*.example.com"])
def test_cors_never_uses_a_wildcard(data, monkeypatch, value):
    monkeypatch.setenv("ADJ_EXTENSION_ORIGIN", value)
    assert api.allowed_origins() == ["http://localhost:8000"]
    r = local_client().get("/findings", params={"run_id": RUN}, headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("example", [".env.example", "pulse/.env.example"])
def test_env_example_has_placeholders_only(example):
    text = (ROOT / example).read_text()
    tokens = [pair.partition(":")[2] for line in text.splitlines() if line.startswith("ADJ_REVIEWER_TOKENS=")
              for pair in line.partition("=")[2].split(",")]
    assert tokens and all(len(t) < api.MIN_TOKEN_LENGTH for t in tokens), "placeholders must not work as tokens"


# ---------------------------------------------------------------- settings


@pytest.mark.parametrize(("line", "works"), [
    ("ADJ_REVIEWER_TOKENS=daniel:{t}", True),
    ("export ADJ_REVIEWER_TOKENS=daniel:{t}", True),
    ('ADJ_REVIEWER_TOKENS="daniel:{t}"  # my token', True),
    ("ADJ_REVIEWER_TOKENS='daniel:{t}'", True),
    ("ADJ_REVIEWER_TOKENS=daniel:{t} # my token", True),
    ("  ADJ_REVIEWER_TOKENS = daniel:{t}  ", True),
    ("# ADJ_REVIEWER_TOKENS=daniel:{t}", False),
    ("ADJ_REVIEWER_TOKENS=", False),
], ids=["plain", "export", "quoted with comment", "single quotes", "inline comment", "spaces", "commented out",
        "empty"])
def test_env_file_syntax(client, data, tmp_path, monkeypatch, line, works):
    monkeypatch.delenv("ADJ_REVIEWER_TOKENS")
    env_file = tmp_path / ".env"
    env_file.write_text("# settings\n\n" + line.format(t=DANIEL) + "\nOTHER=1\n")
    monkeypatch.setattr(api, "ENV_FILE", env_file)
    r = client.post("/decisions", json=body(), headers=auth(DANIEL))
    assert r.status_code == (201 if works else 401)


def test_docs_pages_are_off_unless_asked_for(client, data, monkeypatch):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    monkeypatch.setenv("ADJ_PULSE_DOCS", "1")
    assert local_client().get("/docs").status_code == 200


def test_versions_and_pages_come_from_the_shared_list():
    from scanners import findings

    assert api.Version is findings.Version and api.PageName is findings.PageName


@pytest.mark.parametrize(("host", "status"), [
    ("localhost:8000", 200), ("127.0.0.1:8000", 200), ("localhost", 200),
    ("evil.example:8000", 400), ("evil.example", 400), ("localhost.evil.example:8000", 400), ("testserver", 400),
])
def test_only_local_host_names_are_answered(client, host, status):
    """A web page that points its own domain at this machine must not be able to read findings."""
    assert client.get("/findings", params={"run_id": RUN}, headers={"Host": host}).status_code == status


def test_a_422_says_what_is_wrong_without_echoing_the_request(client, data):
    r = client.post("/decisions", json=body(decision="maybe", comment="private note"), headers=auth(DANIEL))
    assert r.status_code == 422
    problems = r.json()["detail"]
    assert problems[0]["loc"] == ["body", "decision"] and "fail" in problems[0]["msg"]
    assert "private note" not in r.text and "maybe" not in r.text.replace(problems[0]["msg"], "")

    # Text that can't be sent back as JSON (half an emoji) must still get a clean answer, never a 500.
    raw = json.dumps(body()).replace("Link text", "half an emoji \\ud800")
    r = client.post("/decisions", content=raw, headers={**auth(DANIEL), "Content-Type": "application/json"})
    assert r.status_code == 422 and not (data / "reviews").exists()
    assert client.get("/findings", params={"run_id": RUN}).status_code == 200


# ---------------------------------------------------------------- the extension's manifest


def test_extension_asks_for_no_more_than_it_needs():
    """MV3 rules from the Track F handoff: two hosts only, the page script only on the demo, no remote code."""
    folder = ROOT / "extension"
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["sidePanel", "storage"]
    assert manifest["host_permissions"] == ["https://www.w3.org/WAI/demos/bad/*", "http://localhost:8000/*"]
    assert [(c["matches"], c["js"]) for c in manifest["content_scripts"]] == [
        (["https://www.w3.org/WAI/demos/bad/*"], ["content.js"])]
    scripts = [manifest["background"]["service_worker"], *manifest["content_scripts"][0]["js"], "sidepanel.js"]
    for name in scripts:
        assert (folder / name).is_file(), f"manifest names {name}, which is not in extension/"
        code = (folder / name).read_text()
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert banned not in code, f"{name} uses {banned}"
        assert all(url.startswith(("http://localhost:8000", "https://www.w3.org/WAI/demos/bad/"))
                   for url in re.findall(r"https?://[^\s\"'`)]+", code)), f"{name} talks to another site"
    html = (folder / "sidepanel.html").read_text()
    assert re.findall(r"<script[^>]*src=\"([^\"]+)\"", html) == ["sidepanel.js"], "only the bundled panel script"
