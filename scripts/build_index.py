#!/usr/bin/env python3
"""Build data/zr.sqlite: full-text search (FTS5, BM25) over sections, definitions and appendices."""
from __future__ import annotations

import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = DATA / "zr.sqlite"

SCHEMA = """
PRAGMA journal_mode = WAL;
DROP TABLE IF EXISTS sections;
DROP TABLE IF EXISTS sections_fts;
DROP TABLE IF EXISTS section_districts;
DROP TABLE IF EXISTS definitions;
DROP TABLE IF EXISTS definitions_fts;
DROP TABLE IF EXISTS appendices;
DROP TABLE IF EXISTS appendices_fts;
DROP TABLE IF EXISTS meta;

CREATE TABLE sections (
  id TEXT PRIMARY KEY, node_id INTEGER, article TEXT, article_num INTEGER, article_title TEXT,
  chapter INTEGER, chapter_title TEXT, title TEXT, level INTEGER, parent TEXT, url TEXT,
  last_amended TEXT, defined_terms TEXT, cross_refs TEXT, images TEXT, districts TEXT,
  body_md TEXT, text TEXT, sort_key TEXT
);
CREATE INDEX sections_parent ON sections(parent);
CREATE INDEX sections_chapter ON sections(article_num, chapter);
CREATE VIRTUAL TABLE sections_fts USING fts5(
  id UNINDEXED, title, text, tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TABLE section_districts (section_id TEXT, district TEXT);
CREATE INDEX sd_district ON section_districts(district);

CREATE TABLE definitions (
  rowid INTEGER PRIMARY KEY, term TEXT, variants TEXT, applicability TEXT, scope TEXT,
  last_amended TEXT, source_section TEXT, definition_md TEXT, text TEXT, url TEXT, aliases TEXT
);
CREATE INDEX definitions_term ON definitions(term);
CREATE TABLE definition_aliases (rowid_ref INTEGER, alias TEXT);
CREATE INDEX da_alias ON definition_aliases(alias);
CREATE VIRTUAL TABLE definitions_fts USING fts5(
  rowid_ref UNINDEXED, term, text, tokenize = 'porter unicode61 remove_diacritics 2'
);

CREATE TABLE appendices (id TEXT PRIMARY KEY, title TEXT, url TEXT, last_amended TEXT, body_md TEXT, text TEXT);
CREATE VIRTUAL TABLE appendices_fts USING fts5(id UNINDEXED, title, text, tokenize = 'porter unicode61 remove_diacritics 2');

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def aliases_for(term: str, variants: str) -> list[str]:
    """'accessory use, or accessory' -> ['accessory', 'accessory use', 'accessory use, or accessory']."""
    import re
    al = {term.lower().strip()}
    for p in re.split(r",\s*or\s+|\s+or\s+|,\s*", term):
        if p.strip():
            al.add(p.strip().lower())
    for v in re.split(r"[;,]", variants or ""):
        if v.strip():
            al.add(v.strip().lower())
    return sorted(al)


def sort_key(sid: str) -> str:
    head, tail = sid.split("-", 1)
    return f"{int(head):03d}-{tail.ljust(4, '0')}"


def main():
    if DB.exists():
        DB.unlink()
    for suffix in ("-wal", "-shm"):
        p = DB.with_name(DB.name + suffix)
        if p.exists():
            p.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    n = 0
    seen = set()
    with (DATA / "sections.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            sid = r["id"]
            if sid in seen:  # the site has one duplicated id; keep the first occurrence
                continue
            seen.add(sid)
            con.execute(
                "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, r["node_id"], r["article"], r["article_num"], r["article_title"], r["chapter"], r["chapter_title"],
                 r["title"], r["level"], r["parent"], r["url"], r["last_amended"], json.dumps(r["defined_terms"]),
                 json.dumps(r["cross_refs"]), json.dumps(r["images"]), json.dumps(r["districts"]), r["body_md"], r["text"],
                 sort_key(sid)))
            con.execute("INSERT INTO sections_fts (id, title, text) VALUES (?,?,?)", (sid, r["title"], r["text"]))
            con.executemany("INSERT INTO section_districts VALUES (?,?)", [(sid, d) for d in r["districts"]])
            n += 1

    nd = 0
    with (DATA / "definitions.jsonl").open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            d = json.loads(line)
            al = aliases_for(d["term"], d["variants"])
            con.execute("INSERT INTO definitions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (i, d["term"], d["variants"], d["applicability"], d["scope"], d["last_amended"],
                         d["source_section"], d["definition_md"], d["text"], d["url"], json.dumps(al)))
            con.execute("INSERT INTO definitions_fts (rowid_ref, term, text) VALUES (?,?,?)", (i, " / ".join(al), d["text"]))
            con.executemany("INSERT INTO definition_aliases VALUES (?,?)", [(i, a) for a in al])
            nd += 1

    na = 0
    with (DATA / "appendices.jsonl").open(encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            con.execute("INSERT INTO appendices VALUES (?,?,?,?,?,?)",
                        (a["id"], a["title"], a["url"], a["last_amended"], a["body_md"], a["text"]))
            con.execute("INSERT INTO appendices_fts (id, title, text) VALUES (?,?,?)", (a["id"], a["title"], a["text"]))
            na += 1

    import datetime as dt
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
        ("sections", str(n)), ("definitions", str(nd)), ("appendices", str(na)),
        ("source", "https://zr.planning.nyc.gov"),
    ])
    con.execute("INSERT INTO sections_fts(sections_fts) VALUES ('optimize')")
    con.commit()
    con.close()
    print(f"built {DB.name}: {n} sections, {nd} definitions, {na} appendices, {DB.stat().st_size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
