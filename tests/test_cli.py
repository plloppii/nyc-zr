"""End-to-end: the no-install CLI against the committed data (builds data/zr.sqlite if needed)."""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ZR = [sys.executable, "-I", str(ROOT / "scripts" / "zr.py")]


def run(*args):
    return subprocess.run([*ZR, *args], capture_output=True, text=True, cwd=ROOT)


def test_search_json_and_text():
    r = run("search", "rear yard corner lot", "--district", "R6B", "--article", "II", "--limit", "3", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["count"] == 3 and all("R6" in h["districts"] or "R6B" in h["districts"] for h in d["hits"])
    t = run("search", "rear yard", "--limit", "1")
    assert t.returncode == 0 and t.stdout.startswith("1 hits for 'rear yard'")


def test_get_and_define():
    r = run("get", "23-344", "--parents", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["id"] == "23-344" and d["parents"][0]["id"] == "23-30" and "R8" in d["districts"]
    assert run("define", "corner lot").stdout.startswith("### corner lot")


def test_errors_exit_nonzero_on_stderr():
    r = run("get", "99-999")
    assert r.returncode == 1 and r.stdout == "" and r.stderr.startswith("error: no section 99-999")
    r = run("get", "99-999", "--json")
    assert r.returncode == 1 and json.loads(r.stderr)["error"] == "no section 99-999"
    r = run("search", "yard", "--article", "XV")
    assert r.returncode == 2 and "unknown article" in r.stderr
    r = run("search", "yard", "--chapter", "3")
    assert r.returncode == 2 and "--chapter requires --article" in r.stderr


def test_range_expansion_reaches_the_index():
    # 22-133 covers R8 only through a range such as "R6 through R12"; it never names R8 itself
    d = json.loads(run("district", "R8", "--article", "II", "--limit", "500", "--json").stdout)
    assert "22-133" in {s["id"] for s in d["sections"]}
    body = (ROOT / "sections" / "article-ii" / "chapter-2" / "22-133.md").read_text().split("---", 2)[2]
    assert "R3 through R12" in body and not re.search(r"\bR8\b", body)  # the range, not a literal token, carries it


def test_info_reports_fetch_date():
    d = json.loads(run("info", "--json").stdout)
    assert d["text_fetched_at"] and d["latest_amendment"] and d["index_built_at"] >= d["text_fetched_at"]
