"""Build a tiny index from synthetic records and exercise the shared functions against it."""
import json

import pytest

import build_index
import server as S

FILLER = " ".join(["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit"] * 12)


def _section(sid, title, body, **kw):
    head, tail = sid.split("-")
    level = 1 if len(tail) == 2 and tail.endswith("0") else 2 if len(tail) == 2 else len(tail)
    parent = kw.pop("parent", None if level == 1 else f"{head}-{tail[0]}0" if level == 2 else f"{head}-{tail[:-1]}")
    rec = {"id": sid, "node_id": int(head + tail), "article": "II", "article_num": 2, "article_title": "Residence",
           "chapter": 3, "chapter_title": "Bulk", "title": title, "level": level, "parent": parent,
           "url": f"https://zr.planning.nyc.gov/article-ii/chapter-3/{sid}", "last_amended": "2024-12-05",
           "defined_terms": [], "cross_refs": [], "images": [], "districts": [], "body_md": body}
    rec.update(kw)
    return rec


SECTIONS = [
    _section("23-30", "YARDS", ""),  # bare group heading
    _section("23-34", "Rear Yard Requirements", "In all districts a **_rear yard_** shall be provided. " + FILLER,
             districts=["R6", "R7", "R8"]),
    _section("23-35", "Court Regulations", "rear yard rear yard rear yard rear yard. " + FILLER, districts=["R6B"]),
    _section("23-36", "Lot Coverage", "Maximum lot coverage in R1 Districts. " + FILLER, districts=["R1"]),
    _section("23-341", "Minimum depth", "A depth of thirty feet. " + FILLER),
]
DEFINITIONS = [
    {"term": "corner lot", "variants": "", "applicability": "General Definition", "scope": "general", "last_amended": None,
     "source_section": "12-10", "definition_md": "see **_lot, corner_**", "url": "u1"},
    {"term": "lot, corner", "variants": "corner lots", "applicability": "General Definition", "scope": "general",
     "last_amended": "1965-05-20", "source_section": "12-10", "definition_md": "A corner lot is bounded by two streets.", "url": "u2"},
    {"term": "accessory use, or accessory", "variants": "", "applicability": "General Definition", "scope": "general",
     "last_amended": "2024-12-05", "source_section": "12-10", "definition_md": "An accessory use is incidental.", "url": "u3"},
]
APPENDICES = [{"id": "appendix-b", "title": "Index of Special Purpose Districts", "url": "ua", "last_amended": None,
               "body_md": "Special Purpose Districts and their rear yard rules."}]


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    for name, rows in (("sections.jsonl", SECTIONS), ("definitions.jsonl", DEFINITIONS), ("appendices.jsonl", APPENDICES)):
        (data / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (data / "meta.json").write_text(json.dumps({"fetched_at": "2026-10-14T19:55:00+00:00", "latest_amendment": "2025-11-12"}))
    db = data / "zr.sqlite"
    counts = build_index.build(data, db)
    assert counts["sections"] == 5 and counts["definitions"] == 3 and counts["appendices"] == 1
    assert not db.with_name(db.name + ".tmp").exists()
    old = S.DB
    S.DB = db
    yield db
    S.DB = old


def test_meta_carries_fetch_provenance(index):
    meta = dict(S.db().execute("SELECT key, value FROM meta"))
    assert meta["fetched_at"] == "2026-10-14T19:55:00+00:00"
    assert meta["latest_amendment"] == "2025-11-12"
    assert meta["built_at"] > meta["fetched_at"]
    assert S.db().execute("PRAGMA journal_mode").fetchone()[0] != "wal"


def test_text_is_derived_from_markdown(index):
    text = S.db().execute("SELECT text FROM sections WHERE id = '23-34'").fetchone()[0]
    assert text.startswith("In all districts a rear yard shall be provided.")


def test_title_match_outranks_repeated_body_words(index, monkeypatch):
    # 23-34 names the term once in its title and once in its body; 23-35 repeats it four times in a body of the
    # same length. The title boost must decide it, and the test must be sensitive to the boost being wired up.
    assert [h["id"] for h in S.search("rear yard")["hits"][:2]] == ["23-34", "23-35"]
    monkeypatch.setattr(S, "TITLE_WEIGHT", 1.0)
    assert [h["id"] for h in S.search("rear yard")["hits"][:2]] == ["23-35", "23-34"]


def test_or_fallback_keeps_quoted_phrase(index):
    r = S.search('"rear yard" zzzqqq')
    assert r["count"] >= 2 and "OR matches" in r["note"]
    assert {h["id"] for h in r["hits"]} >= {"23-34", "23-35"}


def test_no_match_at_all_is_not_an_error(index):
    r = S.search("zzzqqq yyyqqq")
    assert r["count"] == 0 and "error" not in r and "nor any single term" in r["note"]


def test_bad_syntax_is_a_clean_error(index):
    assert "bad query syntax" in S.search("yard AND")["error"]


def test_filters_are_validated(index):
    assert "unknown article" in S.search("yard", article="XV")["error"]
    assert "chapter requires article" in S.search("yard", chapter=3)["error"]
    assert "unknown scope" in S.search("yard", scope="everything")["error"]
    assert S.search("yard", article="II", chapter=3)["count"] > 0
    assert S.search("yard", article="II", chapter=4)["count"] == 0


def test_district_filter_uses_family(index):
    ids = {h["id"] for h in S.search("rear yard", district="R6B")["hits"]}
    assert ids == {"23-34", "23-35"}  # 23-34 says R6, 23-35 says R6B


def test_scope_all_mixes_types(index):
    types = {h["type"] for h in S.search("rear yard", scope="all")["hits"]}
    assert types == {"section", "appendix"}


def test_get_section_and_did_you_mean(index):
    d = S.get_section("23-34", include_parents=True)
    assert d["title"] == "Rear Yard Requirements" and d["parents"] == [{"id": "23-30", "title": "YARDS"}]
    miss = S.get_section("23-39")
    assert miss["error"] == "no section 23-39" and miss["did_you_mean"] == []
    assert [x["id"] for x in S.get_section("23-3")["did_you_mean"]][:2] == ["23-30", "23-34"]


def test_bare_heading_lists_subsections(index):
    d = S.get_section("23-30")
    assert [s["id"] for s in d["subsections"]] == ["23-34", "23-35", "23-36"]
    assert "Subsections" in S.render_section(d)
    full = S.get_section("23-30", include_children=True)
    assert "subsections" not in full and [c["id"] for c in full["children"]] == ["23-34", "23-341", "23-35", "23-36"]


def test_render_section_truncates(index):
    out = S.render_section(S.get_section("23-34"), max_chars=200)
    assert out.endswith("fetch children individually]") and len(out) < 400


def test_define_alias_and_see_pointer(index):
    d = S.define("corner lot")
    assert [x["term"] for x in d["definitions"]] == ["corner lot", "lot, corner"]
    assert S.define("accessory")["definitions"][0]["term"] == "accessory use, or accessory"
    assert S.define("corner lots")["definitions"][0]["term"] == "lot, corner"
    assert "no definition" in S.define("zzz")["error"]


def test_district_sections(index):
    d = S.district_sections("R6B")
    assert d["family"] == ["R6B", "R6"] and d["total"] == 2
    assert [(s["id"], s["exact"]) for s in d["sections"]] == [("23-35", True), ("23-34", False)]
    assert "unknown article" in S.district_sections("R6B", article="XV")["error"]


def test_list_chapter_and_toc_validate(index):
    d = S.list_chapter("II", 3)
    assert d["chapter_title"] == "Bulk" and [s["id"] for s in d["sections"]] == ["23-30", "23-34", "23-341", "23-35", "23-36"]
    assert "no chapter 9" in S.list_chapter("II", 9)["error"]
    assert "unknown article" in S.list_chapter("XV", 1)["error"]
    assert "unknown article" in S.toc("XV")["error"]
