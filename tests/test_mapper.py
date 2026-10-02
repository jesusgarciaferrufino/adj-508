"""Track B: the corpus and the deterministic mapper. No network, no Bedrock."""

import hashlib
import json
import os
from datetime import date

import pytest

from agents import mapper
from agents.mapper import NotInCorpusError, map_finding, map_page, read_ground_truth, score_spotcheck
from rag import corpus_tools
from rag.corpus_tools import CANONICAL, CONTENT, CorpusError, canonical_errors, corpus_errors, load_corpus
from scanners.findings import REPO, Finding, FindingsFileError, load_findings, validate_findings
from scanners.wcag import WCAG20_A, WCAG20_A_AA, WCAG20_AA


@pytest.fixture
def example():
    row = json.loads((REPO / "docs" / "finding.example.json").read_text())
    return validate_findings([row])[0]


def test_canonical_table_matches_scanners_wcag():
    assert set(CANONICAL) == WCAG20_A_AA
    assert {sc for sc, (_, level, _) in CANONICAL.items() if level == "A"} == WCAG20_A
    assert {sc for sc, (_, level, _) in CANONICAL.items() if level == "AA"} == WCAG20_AA
    assert set(CONTENT) == set(CANONICAL)
    assert canonical_errors() == []


def test_committed_corpus_is_valid():
    rows = json.loads((REPO / "rag" / "wcag20_aa.json").read_text())
    assert corpus_errors(rows) == []


def test_corpus_check_catches_changed_content():
    rows = json.loads((REPO / "rag" / "wcag20_aa.json").read_text())
    rows[0]["level"] = "AAA"
    rows[1]["text"] = ""
    errors = corpus_errors(rows + [{"sc": "2.5.3"}])
    assert any("level" in e for e in errors)
    assert any("'text' is empty" in e for e in errors)
    assert any("2.5.3" in e and "not a WCAG 2.0" in e for e in errors)


def test_maps_a_real_finding(example):
    m = map_finding(example, load_corpus())
    sc = example.wcag[0]
    assert m == {"sc": sc, "name": CANONICAL[sc][0], "level": CANONICAL[sc][1],
                 "spec_url": f"https://www.w3.org/TR/WCAG20/#{CANONICAL[sc][2]}"}


def test_rejects_criterion_not_in_corpus(example):
    # WCAG 2.1's 2.5.3 can't get past the loader, so build the finding without validation.
    wcag21 = Finding.model_construct(**{**example.__dict__, "wcag": ["2.5.3"],
                                        "finding_id": "F-before-home-el0001-2.5.3"})
    with pytest.raises(NotInCorpusError, match="2.5.3"):
        map_finding(wcag21, load_corpus())
    mapped, rejected = map_page([example, wcag21], load_corpus())
    assert list(mapped) == [example.finding_id]
    assert list(rejected) == ["F-before-home-el0001-2.5.3"]


def test_spotcheck_scoring_is_seeded_and_counts_matches():
    mapped = {f"F-{i}": "1.1.1" for i in range(30)}
    truth = {f"F-{i}": ({"1.1.1"} if i % 2 == 0 else {"4.1.2"}) for i in range(5, 40)}
    first = score_spotcheck(mapped, truth, size=10, seed=508)
    assert first == score_spotcheck(mapped, truth, size=10, seed=508)
    assert len(first) == 10
    assert all(fid in mapped and fid in truth for fid, *_ in first)
    assert all(ok == (int(fid[2:]) % 2 == 0) for fid, _, _, ok in first)


def test_spotcheck_with_fewer_overlaps_than_sample_size():
    results = score_spotcheck({"A": "1.1.1", "B": "2.4.4"}, {"A": {"1.1.1"}, "B": {"1.3.1", "2.4.4"}, "C": {"1"}})
    assert results == [("A", "1.1.1", {"1.1.1"}, True), ("B", "2.4.4", {"1.3.1", "2.4.4"}, True)]


def test_read_ground_truth(tmp_path):
    csv_file = tmp_path / "ground_truth.csv"
    csv_file.write_text("finding_id,wcag,notes\nF-1,1.1.1,x\nF-2,\"1.3.1; 4.1.2\",\n,2.4.4,\nF-3,,\n")
    assert read_ground_truth(csv_file) == {"F-1": {"1.1.1"}, "F-2": {"1.3.1", "4.1.2"}}
    bad = tmp_path / "bad.csv"
    bad.write_text("id,criterion\nF-1,1.1.1\n")
    with pytest.raises(ValueError, match="missing column"):
        read_ground_truth(bad)


def test_spotcheck_without_ground_truth_appends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(mapper, "GROUND_TRUTH", REPO / "data" / "ground-truth" / "does_not_exist.csv")
    monkeypatch.setattr(mapper, "SPOTCHECK_LOG", tmp_path / "log.md")
    assert mapper.spotcheck({"F-1": "1.1.1"}) == 1
    assert not (tmp_path / "log.md").exists()


def test_bad_corpus_outside_repo_raises_corpus_error(tmp_path):
    bad = tmp_path / "corpus.json"
    bad.write_text("[{")
    with pytest.raises(CorpusError, match="not valid JSON"):
        load_corpus(bad)


def test_unreadable_page_is_skipped_and_its_stale_output_removed(tmp_path, monkeypatch, example):
    monkeypatch.setattr(mapper, "MAPPED_DIR", tmp_path)
    (tmp_path / "before_news.json").write_text("{}")  # last run's output for the page that will fail

    def fake_load(version, page):
        if (version, page) == ("before", "news"):
            raise FindingsFileError("data/findings/before_news.json is not valid JSON")
        return [example] if (version, page) == ("before", "home") else []

    monkeypatch.setattr(mapper, "load_findings", fake_load)
    mapped, problems = mapper.map_all(load_corpus(), "abc123")
    assert mapped == {example.finding_id: example.wcag[0]}
    assert len(problems) == 1 and "skipped before/news" in problems[0] and "removed stale" in problems[0]
    assert not (tmp_path / "before_news.json").exists()
    home = json.loads((tmp_path / "before_home.json").read_text())
    assert home["corpus_sha256"] == "abc123" and home["mapper_version"] == mapper.MAPPER_VERSION
    assert list(home["findings"]) == [example.finding_id]
    assert len(list(tmp_path.glob("*.json"))) == 7  # every other page still written


def test_spotcheck_appends_dated_result(tmp_path, monkeypatch):
    truth = tmp_path / "ground_truth.csv"
    truth.write_text("finding_id,wcag\nF-1,1.1.1\nF-2,4.1.2\n")
    log = tmp_path / "mapper_spotcheck.md"
    monkeypatch.setattr(mapper, "GROUND_TRUTH", truth)
    monkeypatch.setattr(mapper, "SPOTCHECK_LOG", log)
    assert mapper.spotcheck({"F-1": "1.1.1", "F-2": "1.3.1", "F-9": "2.4.4"}) == 0
    assert mapper.spotcheck({"F-1": "1.1.1"}) == 0
    text = log.read_text()
    assert text.startswith("# Mapper spot-check log")
    assert text.count(f"## {date.today().isoformat()}: all pages, {mapper.MAPPER_VERSION}, seed 508") == 2
    assert "**1/2 correct** (target 8/10)." in text and "**1/1 correct** (target 8/10)." in text
    assert "| F-2 | 1.3.1 | 4.1.2 | NO |" in text
    assert text.count("### Misses and why") == 2
    assert "- F-2: mapped 1.3.1, ground truth 4.1.2. Why:\n" in text
    assert text.rstrip().endswith("No misses.")


def test_spotcheck_command_end_to_end_with_fake_ground_truth(tmp_path, monkeypatch):
    """`python -m agents.mapper --spotcheck --version before --page home` against a small fake CSV."""
    ids = [f.finding_id for f in load_findings("before", "home")][:12]
    wrong = set(ids[:3])  # the fake sheet disagrees with the mapper on these three
    rows = ["finding_id,wcag,notes"] + [f"{fid},{'9.9.9' if fid in wrong else fid.rsplit('-', 1)[1]},"
                                        for fid in ids]
    rows.append("F-after-news-el0001-1.1.1,1.1.1,")  # another page: outside --version/--page, never sampled
    truth = tmp_path / "ground_truth.csv"
    truth.write_text("\n".join(rows) + "\n")
    log = tmp_path / "docs" / "mapper_spotcheck.md"
    monkeypatch.setattr(mapper, "GROUND_TRUTH", truth)
    monkeypatch.setattr(mapper, "SPOTCHECK_LOG", log)
    monkeypatch.setattr(mapper, "MAPPED_DIR", tmp_path / "mapped")

    assert mapper.main(["--spotcheck", "--version", "before", "--page", "home"]) == 0
    assert [p.name for p in (tmp_path / "mapped").iterdir()] == ["before_home.json"]

    expected = score_spotcheck({fid: fid.rsplit("-", 1)[1] for fid in ids},
                               {fid: {"9.9.9" if fid in wrong else fid.rsplit("-", 1)[1]} for fid in ids})
    assert len(expected) == 10
    misses = [fid for fid, _, _, ok in expected if not ok]
    text = log.read_text()
    assert f"## {date.today().isoformat()}: before/home, {mapper.MAPPER_VERSION}, seed 508" in text
    assert f"**{10 - len(misses)}/10 correct** (target 8/10)." in text
    assert "F-after-news" not in text
    section = text.split("### Misses and why", 1)[1]
    assert [ln.split(":")[0][2:] for ln in section.strip().splitlines()] == misses
    assert all(ln.endswith("Why:") for ln in section.strip().splitlines())

    # A second run adds a second dated entry rather than replacing the first.
    assert mapper.main(["--spotcheck", "--version", "before", "--page", "home"]) == 0
    assert log.read_text().count("### Misses and why") == 2


def test_write_atomic_leaves_no_temp_file_when_it_fails(tmp_path, monkeypatch):
    target = tmp_path / "out.json"
    target.write_text("old")

    def broken_replace(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(corpus_tools.os, "replace", broken_replace)
    with pytest.raises(OSError, match="No space"):
        corpus_tools.write_atomic(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_corpus_check_reports_odd_shapes_instead_of_crashing():
    rows = json.loads((REPO / "rag" / "wcag20_aa.json").read_text())
    rows[0]["common_failures"] = 5
    errors = corpus_errors(rows + [{"sc": ["1.1.1"]}])
    assert any("common_failures must be a list" in e for e in errors)
    assert any("['1.1.1']" in e and "not a WCAG 2.0" in e for e in errors)


def test_commands_report_read_and_write_errors(tmp_path, monkeypatch, capsys):
    not_utf8 = tmp_path / "corpus.json"
    not_utf8.write_bytes(b"\xff\xfe not utf-8")
    monkeypatch.setattr(corpus_tools, "CORPUS", not_utf8)
    assert corpus_tools.validate() == 1
    monkeypatch.setattr(corpus_tools, "MAP508", not_utf8)
    assert corpus_tools.map508() == 1

    def disk_full(path, text):
        raise OSError("No space left on device")

    monkeypatch.setattr(corpus_tools, "MAP508", tmp_path / "map.json")
    monkeypatch.setattr(corpus_tools, "write_atomic", disk_full)
    assert corpus_tools.map508() == 1
    assert capsys.readouterr().out.count("ERROR") == 3


def test_corpus_hash_comes_from_the_same_read():
    corpus, sha = corpus_tools.load_corpus_with_hash()
    assert corpus == load_corpus()
    assert sha == hashlib.sha256((REPO / "rag" / "wcag20_aa.json").read_bytes()).hexdigest()[:12]
    assert all(json.loads(p.read_text())["corpus_sha256"] == sha for p in (REPO / "data" / "mapped").glob("*.json"))


@pytest.fixture
def locked_dir(tmp_path):
    """A folder nobody can list or enter (chmod 000). Skips the test where that has no effect, e.g. as root."""
    folder = tmp_path / "locked"
    folder.mkdir()
    (folder / "file.json").write_text("[]")
    folder.chmod(0o000)
    try:
        if os.access(folder, os.R_OK | os.X_OK):
            pytest.skip("chmod 000 doesn't block this user (running as root?)")
        yield folder
    finally:
        folder.chmod(0o755)


def test_missing_or_locked_corpus_raises_corpus_error(tmp_path, locked_dir):
    with pytest.raises(CorpusError, match="not found"):
        load_corpus(tmp_path / "missing.json")
    with pytest.raises(CorpusError, match="cannot read"):
        load_corpus(locked_dir / "file.json")


def test_locked_output_folder_skips_the_page_instead_of_crashing(monkeypatch, locked_dir, capsys):
    monkeypatch.setattr(mapper, "MAPPED_DIR", locked_dir)
    assert mapper.main(["--version", "before", "--page", "home"]) == 1
    assert "SKIPPED before/home: could not write output" in capsys.readouterr().err


def test_locked_ground_truth_folder_is_reported(monkeypatch, tmp_path, locked_dir, capsys):
    monkeypatch.setattr(mapper, "GROUND_TRUTH", locked_dir / "ground_truth.csv")
    monkeypatch.setattr(mapper, "SPOTCHECK_LOG", tmp_path / "log.md")
    assert mapper.spotcheck({"F-1": "1.1.1"}) == 1
    assert "nothing appended" in capsys.readouterr().err
    assert not (tmp_path / "log.md").exists()


def test_corpus_commands_report_locked_folders(monkeypatch, locked_dir, capsys):
    def no_network():
        raise AssertionError("build must stop before fetching the spec")

    monkeypatch.setattr(corpus_tools, "fetch_spec", no_network)
    monkeypatch.setattr(corpus_tools, "MAP508", locked_dir / "section508_map.json")
    assert corpus_tools.map508() == 1
    monkeypatch.setattr(corpus_tools, "CORPUS", locked_dir / "wcag20_aa.json")
    assert corpus_tools.build(force=False) == 1  # stops at the existence check, before any network call
    out = capsys.readouterr().out
    assert "cannot read" in out and "cannot check" in out
