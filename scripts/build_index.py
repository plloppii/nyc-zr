#!/usr/bin/env python3
"""Build data/zr.sqlite: full-text search (FTS5, BM25) over sections, definitions and appendices.

Standard library only. The database is written to a temporary file and moved into place, so an interrupted build
never leaves a half-written index that scripts/zr.py would trust. Plain text is derived from the markdown here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from zrtext import markdown_to_text  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = DATA / "zr.sqlite"

SCHEMA = """
CREATE TABLE sections (
  id TEXT PRIMARY KEY, node_id INTEGER, article TEXT, article_num INTEGER, article_title TEXT,
  chapter INTEGER, chapter_title TEXT, title TEXT, level INTEGER, parent TEXT, url TEXT,
  last_amended TEXT, defined_terms TEXT, cross_refs TEXT, images TEXT, districts TEXT,
  body_md TEXT, text TEXT, sort_key TEXT
);
CREATE INDEX sections_parent ON sections(parent);
CREATE INDEX sections_chapter ON sections(article_num, chapter);
-- column order matters for bm25()/snippet(): 0 = id (unindexed), 1 = title, 2 = text
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


def _text(rec: dict, md_key: str) -> str:
    return rec.get("text") or markdown_to_text(rec.get(md_key) or "")


def _jsonl(path: pathlib.Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def build(data: pathlib.Path = DATA, db: pathlib.Path = DB) -> dict:
    """Build the index at `db` from `data`/*.jsonl (+ meta.json). Returns the counts written to the meta table."""
    tmp = db.with_name(db.name + ".tmp")
    for p in (tmp, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if p.exists():
            p.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    n = 0
    seen = set()
    for r in _jsonl(data / "sections.jsonl"):
        sid = r["id"]
        if sid in seen:  # defensive: parse.py already drops the site's one duplicated id
            continue
        seen.add(sid)
        text = _text(r, "body_md")
        con.execute(
            "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, r["node_id"], r["article"], r["article_num"], r["article_title"], r["chapter"], r["chapter_title"],
             r["title"], r["level"], r["parent"], r["url"], r["last_amended"], json.dumps(r["defined_terms"]),
             json.dumps(r["cross_refs"]), json.dumps(r["images"]), json.dumps(r["districts"]), r["body_md"], text,
             sort_key(sid)))
        con.execute("INSERT INTO sections_fts (id, title, text) VALUES (?,?,?)", (sid, r["title"], text))
        con.executemany("INSERT INTO section_districts VALUES (?,?)", [(sid, d) for d in r["districts"]])
        n += 1

    nd = 0
    for i, d in enumerate(_jsonl(data / "definitions.jsonl"), 1):
        al = aliases_for(d["term"], d["variants"])
        text = _text(d, "definition_md")
        con.execute("INSERT INTO definitions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (i, d["term"], d["variants"], d["applicability"], d["scope"], d["last_amended"],
                     d["source_section"], d["definition_md"], text, d["url"], json.dumps(al)))
        con.execute("INSERT INTO definitions_fts (rowid_ref, term, text) VALUES (?,?,?)", (i, " / ".join(al), text))
        con.executemany("INSERT INTO definition_aliases VALUES (?,?)", [(i, a) for a in al])
        nd += 1

    na = 0
    for a in _jsonl(data / "appendices.jsonl"):
        text = _text(a, "body_md")
        con.execute("INSERT INTO appendices VALUES (?,?,?,?,?,?)",
                    (a["id"], a["title"], a["url"], a["last_amended"], a["body_md"], text))
        con.execute("INSERT INTO appendices_fts (id, title, text) VALUES (?,?,?)", (a["id"], a["title"], text))
        na += 1

    # provenance from parse.py (the fetch date is what "text as of" means; built_at is only when this clone indexed it)
    meta = {"source": "https://zr.planning.nyc.gov"}
    meta_path = data / "meta.json"
    if meta_path.exists():
        meta.update({k: v for k, v in json.loads(meta_path.read_text(encoding="utf-8")).items()
                     if k in ("source", "fetched_at", "fetched_earliest", "parsed_at", "latest_amendment")})
    meta.update({"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 "sections": n, "definitions": nd, "appendices": na})
    con.executemany("INSERT INTO meta VALUES (?,?)", [(k, "" if v is None else str(v)) for k, v in meta.items()])
    for t in ("sections_fts", "definitions_fts", "appendices_fts"):
        con.execute(f"INSERT INTO {t}({t}) VALUES ('optimize')")
    con.commit()
    con.close()
    os.replace(tmp, db)
    return {"sections": n, "definitions": nd, "appendices": na, "bytes": db.stat().st_size, **{k: meta[k] for k in meta if k in ("fetched_at", "latest_amendment")}}


def main():
    r = build()
    print(f"built {DB.name}: {r['sections']} sections, {r['definitions']} definitions, {r['appendices']} appendices, "
          f"{r['bytes']/1e6:.1f} MB" + (f"; text fetched {r['fetched_at'][:10]}" if r.get("fetched_at") else ""))


if __name__ == "__main__":
    main()
