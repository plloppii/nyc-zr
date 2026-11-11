#!/usr/bin/env python3
"""NYC building law from the command line: the Zoning Resolution and the codes that govern what can be built inside it.

  python3 zr.py search "rear yard corner lot" [--code zr] [--district R6B] [--article II [--chapter 3]] [--scope all]
  python3 zr.py get 23-344 [--children] [--parents]      a Zoning Resolution section (bare ids are ZR)
  python3 zr.py get mdl:31                                a section of another code: code:id
  python3 zr.py define "corner lot"                       definitions: ZR 12-10, then BC 202, MDL § 4, HMC § 27-2004
  python3 zr.py toc [II | mdl | mdl:article-3]            outline of the Resolution, of an article, or of another code
  python3 zr.py chapter II 3                              one Zoning Resolution chapter
  python3 zr.py district R6B [--article II]               Zoning Resolution sections naming a district
  python3 zr.py topic ["room size"] [--text]              which sections of which code govern a floorplan question
  python3 zr.py history 23-344                            amendment history, live from zr.planning.nyc.gov
  python3 zr.py info                                      which codes are loaded and what date each is as of

Codes: zr = NYC Zoning Resolution, mdl = NY Multiple Dwelling Law, bc = NYC Building Code, hmc = NYC Housing
Maintenance Code (whichever have data under data/<code>/). Add --json to any command for machine-readable output.
Errors go to stderr with exit status 1 (usage errors: 2). Needs only Python 3.11+ with SQLite FTS5; the search index
(data/index.sqlite) is built from data/ on first use and rebuilt whenever the data changes, e.g. after `git pull`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html.parser
import json
import os
import pathlib
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "index.sqlite"
TOPICS = DATA / "topics.json"
ZR_BASE = "https://zr.planning.nyc.gov"

# code -> (name, how a section is cited)
CODES = {
    "zr": ("NYC Zoning Resolution", "ZR § {id}"),
    "mdl": ("NY Multiple Dwelling Law", "MDL § {id}"),
    "bc": ("NYC Building Code", "BC {id}"),
    "hmc": ("NYC Housing Maintenance Code", "HMC § {id}"),
}
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11,
         "XII": 12, "XIII": 13, "XIV": 14}
SCOPES = ("sections", "definitions", "appendices", "all")
FENCE_RE = re.compile(r"```.*?```", re.S)  # fixed-width table blocks (Building Code PDFs): shown by get, left out of the search text
_NUMBERED_RE = re.compile(r"^[A-Z]?\d")  # 23-344, 1208.1, D101.1 are sections; article-3, chapter-10, appendix-d are headings
TITLE_WEIGHT = 8.0  # bm25() weights follow the FTS5 column order: code, id (unindexed), title, text
SCHEMA_VERSION = "4"  # bump when SCHEMA or what build_index stores changes; clones then rebuild on the next run


# ----------------------------------------------------------------------------------------------------------------
# the index: data/<code>/*.jsonl -> data/index.sqlite (FTS5, BM25)

SCHEMA = """
CREATE TABLE sections (
  code TEXT NOT NULL, id TEXT NOT NULL, title TEXT, level INTEGER, parent TEXT, url TEXT, last_amended TEXT,
  place TEXT, body_md TEXT, text TEXT, sort_key TEXT,
  -- Zoning Resolution only
  node_id INTEGER, article TEXT, article_num INTEGER, article_title TEXT, chapter INTEGER, chapter_title TEXT,
  defined_terms TEXT, cross_refs TEXT, images TEXT, districts TEXT,
  PRIMARY KEY (code, id)
);
CREATE INDEX sections_parent ON sections(code, parent);
CREATE INDEX sections_chapter ON sections(article_num, chapter);
CREATE VIRTUAL TABLE sections_fts USING fts5(
  code UNINDEXED, id UNINDEXED, title, text, tokenize = 'porter unicode61 remove_diacritics 2'
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

CREATE TABLE codes (code TEXT PRIMARY KEY, name TEXT, source TEXT, fetched_at TEXT, latest_amendment TEXT, sections INTEGER, note TEXT);
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


def zr_sort_key(sid: str) -> str:
    head, tail = sid.split("-", 1)
    return f"{int(head):03d}-{tail.ljust(4, '0')}"


def _jsonl(path: pathlib.Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _meta(code: str) -> dict:
    p = DATA / code / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def data_files() -> list[pathlib.Path]:
    """Every file the index is built from (the mtimes decide when to rebuild)."""
    out = []
    for code in CODES:
        for name in ("sections.jsonl", "definitions.jsonl", "appendices.jsonl", "meta.json"):
            p = DATA / code / name
            if p.exists():
                out.append(p)
    return out


def build_index(db: pathlib.Path = DB) -> dict:
    """Write the index to a temporary file and move it into place, so an interrupted build never leaves a half index."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from zrtext import markdown_to_text  # plain text for the FTS tables is derived from the markdown

    tmp = db.with_name(db.name + ".tmp")
    for p in (tmp, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if p.exists():
            p.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    counts: dict[str, int] = {}

    for code, (name, _) in CODES.items():
        src = DATA / code / "sections.jsonl"
        if not src.exists():
            continue
        n = 0
        seen = set()
        for r in _jsonl(src):
            sid = r["id"]
            if sid in seen:
                continue
            seen.add(sid)
            text = r.get("text") or markdown_to_text(FENCE_RE.sub(" ", r.get("body_md") or ""))
            if code == "zr":
                place = f"Art {r['article']} Ch {r['chapter']}"
                con.execute(
                    "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?,?,?)",
                    (code, sid, r["title"], r["level"], r["parent"], r["url"], r["last_amended"], place, r["body_md"],
                     text, zr_sort_key(sid), r["node_id"], r["article"], r["article_num"], r["article_title"],
                     r["chapter"], r["chapter_title"], json.dumps(r["defined_terms"]), json.dumps(r["cross_refs"]),
                     json.dumps(r["images"]), json.dumps(r["districts"])))
                con.executemany("INSERT INTO section_districts VALUES (?,?)", [(sid, d) for d in r["districts"]])
            else:
                con.execute(
                    "INSERT INTO sections (code, id, title, level, parent, url, last_amended, place, body_md, text, sort_key) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (code, sid, r["title"], r["level"], r.get("parent"), r.get("url"), r.get("last_amended"),
                     r.get("place"), r.get("body_md") or "", text, r.get("sort_key") or sid))
            con.execute("INSERT INTO sections_fts (code, id, title, text) VALUES (?,?,?,?)", (code, sid, r["title"], text))
            n += 1
        m = _meta(code)
        notes = [m["edition"] if m.get("edition") else None,
                 f"document dated {m['document_date']}, no per-section amendment dates" if m.get("document_date") else None,
                 f"read via {m['mirror']}" if m.get("mirror") else None]
        con.execute("INSERT INTO codes VALUES (?,?,?,?,?,?,?)",
                    (code, name, m.get("source"), m.get("fetched_at"), m.get("latest_amendment"), n, "; ".join(x for x in notes if x) or None))
        counts[code] = n

    zr = DATA / "zr"
    if (zr / "definitions.jsonl").exists():
        for i, d in enumerate(_jsonl(zr / "definitions.jsonl"), 1):
            al = aliases_for(d["term"], d["variants"])
            text = d.get("text") or markdown_to_text(d.get("definition_md") or "")
            con.execute("INSERT INTO definitions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (i, d["term"], d["variants"], d["applicability"], d["scope"], d["last_amended"],
                         d["source_section"], d["definition_md"], text, d["url"], json.dumps(al)))
            con.execute("INSERT INTO definitions_fts (rowid_ref, term, text) VALUES (?,?,?)", (i, " / ".join(al), text))
            con.executemany("INSERT INTO definition_aliases VALUES (?,?)", [(i, a) for a in al])
            counts["zr definitions"] = i
    if (zr / "appendices.jsonl").exists():
        for na, a in enumerate(_jsonl(zr / "appendices.jsonl"), 1):
            text = a.get("text") or markdown_to_text(a.get("body_md") or "")
            con.execute("INSERT INTO appendices VALUES (?,?,?,?,?,?)",
                        (a["id"], a["title"], a["url"], a["last_amended"], a["body_md"], text))
            con.execute("INSERT INTO appendices_fts (id, title, text) VALUES (?,?,?)", (a["id"], a["title"], text))
            counts["zr appendices"] = na

    con.execute("INSERT INTO meta VALUES ('built_at', ?)", (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),))
    con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    for t in ("sections_fts", "definitions_fts", "appendices_fts"):
        con.execute(f"INSERT INTO {t}({t}) VALUES ('optimize')")
    con.commit()
    con.close()
    os.replace(tmp, db)
    return counts


def ensure_index() -> None:
    """Build the index when it is missing or older than the data files (first run, or after a pull)."""
    files = data_files()
    if not files:
        sys.exit("error: no data under data/<code>/; clone the whole repository")
    if DB.exists() and all(f.stat().st_mtime <= DB.stat().st_mtime for f in files):
        try:
            row = db().execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row and row[0] == SCHEMA_VERSION:
                return
        except sqlite3.Error:
            pass
    print("building data/index.sqlite from data/ (first run, or the data or the tool changed) ...", file=sys.stderr)
    counts = build_index()
    print("  " + ", ".join(f"{v} {k}" for k, v in counts.items()), file=sys.stderr)


def db() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ----------------------------------------------------------------------------------------------------------------
# helpers

def resolve_article(article: str | int | None) -> tuple[Optional[int], Optional[str]]:
    """(article number, error). No article -> (None, None); an unknown one -> (None, 'unknown article ...')."""
    if article is None or str(article).strip() == "":
        return None, None
    a = str(article).strip()
    n = int(a) if a.isdigit() else ROMAN.get(a.upper())
    if n is None or not 1 <= n <= 14:
        return None, f"unknown article {a!r}: use a roman numeral I–XIV or a number 1–14"
    return n, None


def split_ref(ref: str) -> tuple[str, str]:
    """'mdl:31' -> ('mdl', '31'); '23-344' -> ('zr', '23-344')."""
    ref = ref.strip()
    code, sep, rest = ref.partition(":")
    if sep and code.lower() in CODES:
        rest = rest.strip()
        if re.match(r"^[a-z]\d", rest):  # bc:d101.1 -> D101.1 (Building Code appendix ids carry a capital letter)
            rest = rest[0].upper() + rest[1:]
        return code.lower(), rest
    return "zr", ref


def numbered(sid: str) -> bool:
    """A section the law numbers (23-344, 1208.1, D101.1) as opposed to a heading this repo names (article-3, appendix-d)."""
    return bool(_NUMBERED_RE.match(sid))


def cite(code: str, sid: str, title: Optional[str] = None) -> str:
    """'MDL § 31' for a numbered section; just 'MDL' for a heading such as article-3 (its title follows)."""
    tmpl = CODES[code][1]
    if numbered(sid):
        return tmpl.format(id=sid)
    return tmpl.split(" ")[0]  # a heading such as article-3: the title is printed after it


def ref(code: str, sid: str) -> str:
    """The form that `get` accepts: bare for the Zoning Resolution, code:id otherwise."""
    return sid if code == "zr" else f"{code}:{sid}"


def fts_query(q: str) -> str:
    """Natural query -> FTS5 syntax. Quoted phrases, prefix* and AND/OR/NOT/NEAR pass through."""
    q = q.strip()
    if not q:
        return '""'
    if re.search(r'["()]|\b(AND|OR|NOT|NEAR)\b', q):
        return q
    out = []
    for t in re.split(r"\s+", q):
        pref = t.endswith("*")
        t = t.rstrip("*").replace('"', "")
        if t:
            out.append(f'"{t}"' + ("*" if pref else ""))
    return " ".join(out)


_TERM_RE = re.compile(r'"[^"]*"\*?|\S+')
_OPERATOR_RE = re.compile(r"\b(AND|OR|NOT|NEAR)\b|[()]")


def split_terms(fq: str) -> list[str]:
    return _TERM_RE.findall(fq)


def can_relax(fq: str) -> bool:
    """Only words and phrases, more than one: OR-ing them is a meaningful fallback."""
    return not _OPERATOR_RE.search(fq) and len(split_terms(fq)) > 1


def district_family(d: str) -> list[str]:
    """R6B -> [R6B, R6]; C4-4A -> [C4-4A, C4-4, C4]; M1-5 -> [M1-5, M1]. Sections often cite the parent district."""
    d = d.strip().upper()
    fam = [d]
    m = re.match(r"^([RCM]\d{1,2})(-\d{1,2})?([A-Z])?$", d)
    if m:
        base, dash, suffix = m.groups()
        if dash and suffix:
            fam.append(base + dash)
        if dash or suffix:
            fam.append(base)
    return list(dict.fromkeys(fam))


BRIEF_KEYS = ("code", "id", "title", "level", "parent", "place", "last_amended", "url")


def brief(r) -> dict:
    d = {k: r[k] for k in BRIEF_KEYS}
    d["cite"] = cite(r["code"], r["id"], r["title"])
    return d


def _heading(s: dict) -> str:
    when = f"Last amended {s['last_amended']}" if s.get("last_amended") else "Amendment date not recorded"
    return f"_{when} · {s['url']}_"


# ----------------------------------------------------------------------------------------------------------------
# search

def search(query: str, limit: int = 10, code: Optional[str] = None, article: Optional[str] = None,
           chapter: Optional[int] = None, district: Optional[str] = None, scope: str = "sections") -> dict:
    limit = max(1, min(int(limit), 50))
    anum, err = resolve_article(article)
    if err:
        return {"error": err}
    if chapter and anum is None:
        return {"error": "--chapter requires --article (chapter numbers repeat in every article)"}
    if code and code not in CODES:
        return {"error": f"unknown code {code!r}: use one of {', '.join(CODES)}"}
    if (anum or district) and code not in (None, "zr"):
        return {"error": "--article, --chapter and --district apply to the Zoning Resolution only"}
    if anum or district:
        code = "zr"
    if scope not in SCOPES:
        return {"error": f"unknown scope {scope!r}: use one of {', '.join(SCOPES)}"}
    con = db()
    out: dict = {"query": query, "code": code or "all", "hits": []}

    def run_sections(fq):
        sql = [f"SELECT s.*, bm25(sections_fts, 0, 0, {TITLE_WEIGHT}, 1.0) AS rank,",
               "snippet(sections_fts, 3, '[', ']', ' … ', 28) AS snip",
               "FROM sections_fts JOIN sections s ON s.code = sections_fts.code AND s.id = sections_fts.id",
               "WHERE sections_fts MATCH ?"]
        params: list = [fq]
        if code:
            sql.append("AND s.code = ?"); params.append(code)
        if anum:
            sql.append("AND s.article_num = ?"); params.append(anum)
        if chapter:
            sql.append("AND s.chapter = ?"); params.append(int(chapter))
        if district:
            fam = district_family(district)
            sql.append(f"AND s.id IN (SELECT section_id FROM section_districts WHERE district IN ({','.join('?' * len(fam))}))")
            params.extend(fam)
        sql.append("ORDER BY rank LIMIT ?"); params.append(limit)
        return con.execute(" ".join(sql), params).fetchall()

    def run_definitions(fq):
        return con.execute(
            f"SELECT d.*, bm25(definitions_fts, 0, {TITLE_WEIGHT}, 1.0) AS rank, snippet(definitions_fts, 2, '[', ']', ' … ', 28) AS snip "
            "FROM definitions_fts JOIN definitions d ON d.rowid = definitions_fts.rowid_ref "
            "WHERE definitions_fts MATCH ? ORDER BY rank LIMIT ?", (fq, limit)).fetchall()

    def run_appendices(fq):
        return con.execute(
            f"SELECT a.id, a.title, a.url, a.last_amended, bm25(appendices_fts, 0, {TITLE_WEIGHT}, 1.0) AS rank, "
            "snippet(appendices_fts, 2, '[', ']', ' … ', 28) AS snip "
            "FROM appendices_fts JOIN appendices a ON a.id = appendices_fts.id "
            "WHERE appendices_fts MATCH ? ORDER BY rank LIMIT ?", (fq, limit)).fetchall()

    fq = fts_query(query)
    try:
        if scope in ("sections", "all"):
            rows = run_sections(fq)
            if not rows and can_relax(fq):
                rows = run_sections(" OR ".join(split_terms(fq)))
                out["note"] = ("no section matched all terms; showing OR matches" if rows
                               else "no section matched all terms, nor any single term")
            for r in rows:
                h = {"type": "section", **brief(r), "snippet": r["snip"]}
                if r["code"] == "zr":
                    h["districts"] = json.loads(r["districts"])[:12]
                out["hits"].append(h)
        if scope in ("definitions", "all") and code in (None, "zr"):
            for r in run_definitions(fq):
                out["hits"].append({"type": "definition", "term": r["term"], "scope": r["scope"],
                                    "applicability": r["applicability"], "source_section": r["source_section"],
                                    "last_amended": r["last_amended"], "url": r["url"], "snippet": r["snip"]})
        if scope in ("appendices", "all") and code in (None, "zr"):
            for r in run_appendices(fq):
                out["hits"].append({"type": "appendix", "id": r["id"], "title": r["title"], "url": r["url"],
                                    "last_amended": r["last_amended"], "snippet": r["snip"]})
    except sqlite3.OperationalError as e:
        return {"error": f"bad query syntax: {e}", "fts_query": fq}
    out["count"] = len(out["hits"])
    return out


def render_search(d: dict) -> str:
    lines = [f"{d['count']} hits for {d['query']!r}" + (f" in {d['code']}" if d["code"] != "all" else "")
             + (f"  ({d['note']})" if d.get("note") else "")]
    for h in d["hits"]:
        if h["type"] == "section":
            tag = " · ".join(x for x in (h["cite"] if h["id"][:1].isdigit() else "heading", h["place"], h["last_amended"]) if x)
            lines.append(f"\n{ref(h['code'], h['id'])}  {h['title']}   [{tag}]\n    {h['url']}\n    {h['snippet']}")
        elif h["type"] == "definition":
            lines.append(f"\n[def] {h['term']}  ({h['applicability']})\n    {h['snippet']}")
        else:
            lines.append(f"\n[appendix] {h['title']}\n    {h['url']}\n    {h['snippet']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# sections

def _children(con, code: str, sid: str, depth: int = 10) -> list:
    rows = con.execute("SELECT * FROM sections WHERE code = ? AND parent = ? ORDER BY sort_key", (code, sid)).fetchall()
    out = []
    for r in rows:
        out.append(r)
        if depth > 0:
            out.extend(_children(con, code, r["id"], depth - 1))
    return out


def get_section(reference: str, include_children: bool = False, include_parents: bool = False) -> dict:
    con = db()
    code, sid = split_ref(reference)
    r = con.execute("SELECT * FROM sections WHERE code = ? AND id = ?", (code, sid)).fetchone()
    if not r:
        like = con.execute("SELECT code, id, title FROM sections WHERE code = ? AND id LIKE ? ORDER BY sort_key LIMIT 15",
                           (code, sid + "%")).fetchall()
        if not like and ":" not in reference:  # a bare id that lives in another code
            like = con.execute("SELECT code, id, title FROM sections WHERE id = ? ORDER BY code", (sid,)).fetchall()
        return {"error": f"no section {ref(code, sid)} in the {CODES[code][0]}",
                "did_you_mean": [{"ref": ref(x["code"], x["id"]), "title": x["title"]} for x in like]}
    d = brief(r)
    d["body_md"] = r["body_md"]
    if code == "zr":
        d.update({"article": r["article"], "chapter": r["chapter"], "chapter_title": r["chapter_title"],
                  "defined_terms": json.loads(r["defined_terms"]), "cross_refs": json.loads(r["cross_refs"]),
                  "districts": json.loads(r["districts"]), "node_id": r["node_id"]})
    if include_parents:
        chain = []
        p = r["parent"]
        while p:
            pr = con.execute("SELECT id, title, parent FROM sections WHERE code = ? AND id = ?", (code, p)).fetchone()
            if not pr:
                break
            chain.append({"id": pr["id"], "title": pr["title"]})
            p = pr["parent"]
        d["parents"] = list(reversed(chain))
    if include_children:
        d["children"] = [{**brief(c), "body_md": c["body_md"]} for c in _children(con, code, sid)]
    elif not (r["body_md"] or "").strip():
        # a bare heading: list what is under it so the reader need not guess at a second call
        d["subsections"] = [dict(x) for x in con.execute(
            "SELECT id, title, last_amended FROM sections WHERE code = ? AND parent = ? ORDER BY sort_key", (code, sid)).fetchall()]
    return d


def render_section(d: dict, max_chars: int = 60000) -> str:
    parts = []
    if d.get("parents"):
        parts.append("Context: " + " > ".join(f"{p['id']} {p['title']}" for p in d["parents"]))
    meta = {k: v for k, v in d.items() if k not in ("body_md", "parents", "children", "subsections")}
    parts.append("```json\n" + json.dumps(meta, ensure_ascii=False) + "\n```")
    parts.append(f"# {d['cite']} {d['title']}\n{_heading(d)}\n\n{d['body_md']}".rstrip())
    if d.get("subsections"):
        parts.append("This heading has no text of its own. Under it (add --children to read them all):\n"
                     + "\n".join(f"- {ref(d['code'], s['id'])}  {s['title']}  ({s['last_amended'] or 'n/a'})" for s in d["subsections"]))
    for c in d.get("children", []):
        hashes = "#" * min(6, 1 + (c["level"] - d["level"]))
        parts.append(f"{hashes} {c['cite']} {c['title']}\n{_heading(c)}\n\n{c['body_md']}".rstrip())
    text = "\n\n".join(parts)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + f"\n\n… [truncated at {max_chars} chars; raise --max-chars or read the children one by one]"
    return text


# ----------------------------------------------------------------------------------------------------------------
# navigation

def list_chapter(article: str, chapter: int) -> dict:
    anum, err = resolve_article(article)
    if err or anum is None:
        return {"error": err or "article is required"}
    con = db()
    rows = con.execute("SELECT id, title, level, parent, last_amended, length(text) AS chars, chapter_title, article_title, article "
                       "FROM sections WHERE code = 'zr' AND article_num = ? AND chapter = ? ORDER BY sort_key", (anum, int(chapter))).fetchall()
    if not rows:
        return {"error": f"no chapter {chapter} in article {article}"}
    return {"article": rows[0]["article"], "article_title": rows[0]["article_title"], "chapter": int(chapter),
            "chapter_title": rows[0]["chapter_title"],
            "sections": [{k: r[k] for k in ("id", "title", "level", "parent", "last_amended", "chars")} for r in rows]}


def render_chapter(d: dict) -> str:
    lines = [f"Article {d['article']} — {d['article_title']}", f"Chapter {d['chapter']} — {d['chapter_title']}", ""]
    for r in d["sections"]:
        lines.append(f"{'  ' * (r['level'] - 1)}{r['id']}  {r['title']}  ({r['last_amended'] or 'n/a'}; {r['chars']} chars)")
    return "\n".join(lines)


def toc(what: Optional[str] = None) -> dict:
    """No argument or a ZR article: the Resolution's outline. A code: that code's headings. code:id: one subtree."""
    if what and (what.lower() in CODES or (":" in what and what.split(":", 1)[0].lower() in CODES)) and what.lower() != "zr":
        return code_outline(*split_ref(what)) if ":" in what else code_outline(what.lower(), None)
    anum, err = resolve_article(what)
    if err:
        return {"error": err}
    data = json.loads((DATA / "zr" / "toc.json").read_text(encoding="utf-8"))
    if anum is None:
        return {"code": "zr", "articles": [{"article": a["article"], "article_num": a["article_num"], "article_title": a["article_title"],
                                            "chapters": [{"chapter": ch["chapter"], "chapter_title": ch["chapter_title"],
                                                          "sections": len(ch["sections"])} for ch in a["chapters"]]}
                                           for a in data["articles"]],
                "appendices": data["appendices"]}
    arts = [a for a in data["articles"] if a["article_num"] == anum]
    if not arts:
        return {"error": f"no article {what} in the table of contents"}
    return {"code": "zr", "articles": arts}


def code_outline(code: str, heading: Optional[str]) -> dict:
    con = db()
    if not con.execute("SELECT 1 FROM codes WHERE code = ?", (code,)).fetchone():
        return {"error": f"no data for {code} ({CODES[code][0]}) in this clone"}
    if heading:
        top = con.execute("SELECT * FROM sections WHERE code = ? AND id = ?", (code, heading)).fetchone()
        if not top:
            return {"error": f"no heading {ref(code, heading)}"}
        rows = _children(con, code, heading)
        base = top["level"]
        return {"code": code, "heading": brief(top),
                "entries": [{**brief(r), "depth": r["level"] - base, "chars": len(r["text"] or "")} for r in rows]}
    rows = con.execute("SELECT s.*, (SELECT count(*) FROM sections c WHERE c.code = s.code AND c.parent = s.id) AS kids "
                       "FROM sections s WHERE code = ? AND (body_md IS NULL OR body_md = '') ORDER BY sort_key", (code,)).fetchall()
    return {"code": code, "name": CODES[code][0],
            "headings": [{**brief(r), "children": r["kids"]} for r in rows]}


def render_toc(d: dict) -> str:
    lines = []
    if "headings" in d:
        lines.append(f"{d['name']} ({d['code']}): headings, with how many entries each holds. `toc {d['code']}:<id>` opens one.")
        for h in d["headings"]:
            lines.append(f"{'  ' * (h['level'] - 1)}{ref(d['code'], h['id'])}  {h['title']}  ({h['children']})")
        return "\n".join(lines)
    if "entries" in d:
        h = d["heading"]
        lines.append(f"{h['cite']} {h['title']}")
        for e in d["entries"]:
            lines.append(f"{'  ' * e['depth']}{ref(d['code'], e['id'])}  {e['title']}  ({e['last_amended'] or 'n/a'}; {e['chars']} chars)")
        return "\n".join(lines)
    for a in d["articles"]:
        lines.append(f"Article {a['article']} — {a['article_title']}")
        for ch in a["chapters"]:
            secs = ch["sections"]
            if isinstance(secs, int):
                lines.append(f"  Chapter {ch['chapter']} — {ch['chapter_title']}  ({secs} sections)")
            else:
                lines.append(f"  Chapter {ch['chapter']} — {ch['chapter_title']}  ({len(secs)} sections)")
                lines.extend(f"      {s['id']}  {s['title']}" for s in secs if s["level"] == 1)
    if d.get("appendices"):
        lines.append("Appendices:")
        lines.extend(f"  {ap['id']}  — {ap['title']}" for ap in d["appendices"])
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# definitions (Zoning Resolution 12-10)

_DEF_KEYS = ("term", "variants", "applicability", "scope", "last_amended", "source_section", "definition_md", "url")
_BY_ALIAS = ("SELECT d.* FROM definition_aliases a JOIN definitions d ON d.rowid = a.rowid_ref WHERE a.alias = ? "
             "ORDER BY CASE d.scope WHEN 'general' THEN 0 ELSE 1 END, d.term LIMIT ?")


def define(term: str, limit: int = 10) -> dict:
    con = db()
    t = term.strip().lower().strip("*_ ")
    rows = con.execute(_BY_ALIAS, (t, limit)).fetchall()
    if not rows and t.endswith("s"):
        rows = con.execute(_BY_ALIAS, (t[:-1], limit)).fetchall()
    if not rows:
        rows = con.execute(
            "SELECT * FROM definitions WHERE lower(term) LIKE ? ORDER BY CASE scope WHEN 'general' THEN 0 ELSE 1 END, length(term), term LIMIT ?",
            (f"%{t}%", limit)).fetchall()
    if not rows:
        alt = t[:-1] if t.endswith("s") else t + "s"
        rows = con.execute("SELECT * FROM definitions WHERE lower(term) LIKE ? ORDER BY length(term) LIMIT ?",
                           (f"%{alt}%", limit)).fetchall()
    if not rows:
        return {"error": f"no 12-10 definition matching '{term}'"}
    rows = list(rows)
    for r in list(rows):  # follow "see **_lot, corner_**" pointers one hop
        m = re.match(r"^see \*\*_(.+?)_\*\*", (r["definition_md"] or "").strip(), re.I)
        if m:
            have = {y["rowid"] for y in rows}
            rows.extend(x for x in con.execute(_BY_ALIAS, (m.group(1).strip().lower(), limit)).fetchall()
                        if x["rowid"] not in have)
    defs = [{k: r[k] for k in _DEF_KEYS} for r in rows[:limit + 3]]
    return {"term": term, "count": len(defs), "definitions": defs}


DEFINITION_SECTIONS = (("bc", "202"), ("mdl", "4"), ("hmc", "27-2004"))  # where the other laws define their terms


def other_definitions(term: str, limit: int = 6) -> list[dict]:
    """Paragraphs of the other laws' definition sections that define the term (it appears at the paragraph's start)."""
    con = db()
    t = term.strip().lower()
    pat = re.compile(r"\b" + re.escape(t) + r"s?\b", re.I)
    out = []
    for code, sid in DEFINITION_SECTIONS:
        r = con.execute("SELECT * FROM sections WHERE code = ? AND id = ?", (code, sid)).fetchone()
        if not r:
            continue
        for para in (r["body_md"] or "").split("\n\n"):
            head = para.strip()[:45]  # the defined term opens the paragraph, and a definition is more than a list item
            if len(para.strip()) > 60 and pat.search(head):
                out.append({"code": code, "id": sid, "cite": cite(code, sid), "url": r["url"], "text": para.strip()})
                if len(out) >= limit:
                    return out
    return out


def define_all(term: str, limit: int = 10) -> dict:
    d = define(term, limit)
    others = other_definitions(term)
    if "error" in d and not others:
        return {"error": f"no definition of '{term}' in 12-10, BC 202, MDL § 4 or HMC § 27-2004"}
    if "error" in d:
        d = {"term": term, "count": 0, "definitions": []}
    d["other_laws"] = others
    return d


def render_definitions(d: dict) -> str:
    parts = []
    for r in d["definitions"]:
        parts.append(f"### {r['term']}\n_{r['applicability']}"
                     + (f" · last amended {r['last_amended']}" if r["last_amended"] else "")
                     + f" · source {r['source_section']} · {r['url']}_\n\n{r['definition_md']}")
    for o in d.get("other_laws", []):
        parts.append(f"### {o['cite']}\n_{o['url']}_\n\n{o['text']}")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------------------------------------------
# districts (Zoning Resolution)

def district_sections(district: str, article: Optional[str] = None, limit: int = 100) -> dict:
    anum, err = resolve_article(article)
    if err:
        return {"error": err}
    con = db()
    d = district.strip().upper()
    fam = district_family(d)
    ph = ",".join("?" * len(fam))
    sql = (f"SELECT s.id, s.title, s.article, s.chapter, s.chapter_title, s.last_amended, s.url, "
           f"max(sd.district = ?) AS exact FROM section_districts sd "
           f"JOIN sections s ON s.code = 'zr' AND s.id = sd.section_id WHERE sd.district IN ({ph})")
    params: list = [d, *fam]
    if anum:
        sql += " AND s.article_num = ?"; params.append(anum)
    sql += " GROUP BY s.id ORDER BY exact DESC, s.sort_key LIMIT ?"; params.append(max(1, min(int(limit), 500)))
    rows = con.execute(sql, params).fetchall()
    total = con.execute(f"SELECT count(DISTINCT section_id) FROM section_districts WHERE district IN ({ph})", fam).fetchone()[0]
    return {"district": d, "family": fam, "total": total,
            "sections": [{**dict(r), "exact": bool(r["exact"])} for r in rows]}


def render_district(d: dict) -> str:
    fam = d["family"][1:]
    head = f"{d['total']} sections name {d['district']}" + (f" or its family ({', '.join(fam)})" if fam else "")
    lines = [head + f"; showing {len(d['sections'])}", ""]
    for s in d["sections"]:
        lines.append(f"{'*' if s['exact'] else ' '} {s['id']}  {s['title']}   [Art {s['article']} Ch {s['chapter']} · {s['last_amended'] or 'n/a'}]")
    if fam:
        lines += ["", f"* = names {d['district']} itself; the rest name its family or a range such as \"R6 through R12\""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# topics: which sections of which code govern a question about a floorplan (data/topics.json, hand-kept)

def topics(name: Optional[str] = None, with_text: bool = False) -> dict:
    if not TOPICS.exists():
        return {"error": "data/topics.json is missing"}
    data = json.loads(TOPICS.read_text(encoding="utf-8"))
    if not name:
        return {"topics": [{"topic": k, "question": v["question"], "cites": len(v["cites"])}
                           for k, v in data.items() if not k.startswith("_")],
                "about": data.get("_about")}
    key = name.strip().lower()
    hit = data.get(key)
    if not hit:
        cands = [k for k, v in data.items() if not k.startswith("_") and (key in k or key in v["question"].lower()
                                                                         or any(key in a for a in v.get("aliases", [])))]
        if len(cands) == 1:
            key, hit = cands[0], data[cands[0]]
        else:
            return {"error": f"no topic {name!r}", "did_you_mean": cands or [k for k in data if not k.startswith("_")]}
    con = db()
    loaded = {r[0] for r in con.execute("SELECT code FROM codes")}
    out = {"topic": key, "question": hit["question"], "note": hit.get("note"), "cites": []}
    for c in hit["cites"]:
        code, sid = c["code"], c["id"]
        entry = {"ref": ref(code, sid), "cite": cite(code, sid), "code_name": CODES[code][0], "why": c.get("why")}
        if "term" in c:
            entry["term"] = c["term"]
        if code not in loaded:
            entry["status"] = "code not loaded in this clone"
        elif "term" in c:  # a Zoning Resolution 12-10 definition rather than a whole section
            d = define(c["term"], 1)
            if "error" in d:
                entry["status"] = "definition not found in the index"
            else:
                df = d["definitions"][0]
                entry.update({"title": f"definition of {df['term']}", "url": df["url"], "last_amended": df["last_amended"],
                              "place": "12-10", "open": f'define "{c["term"]}"'})
                if with_text:
                    entry["body_md"] = df["definition_md"]
        else:
            r = con.execute("SELECT * FROM sections WHERE code = ? AND id = ?", (code, sid)).fetchone()
            if not r:
                entry["status"] = "section not found in the index"
            else:
                is_heading = not (r["body_md"] or "").strip()
                entry.update({"title": r["title"], "url": r["url"], "last_amended": r["last_amended"], "place": r["place"],
                              "open": f"get {entry['ref']}" + (" --children" if is_heading else "")})
                if with_text:
                    entry["body_md"] = r["body_md"] if not is_heading else "\n\n".join(
                        f"**{cite(code, k['id'], k['title'])} {k['title']}**\n\n{k['body_md']}" for k in _children(con, code, sid) if k["body_md"])
        out["cites"].append(entry)
    return out


def render_topics(d: dict) -> str:
    if "topics" in d:
        lines = [d["about"] or "Topics:", ""]
        lines += [f"{t['topic']:<28} {t['question']}  ({t['cites']} citations)" for t in d["topics"]]
        lines += ["", 'Use: topic "<name>" [--text]']
        return "\n".join(lines)
    lines = [f"{d['topic']}: {d['question']}"]
    if d.get("note"):
        lines += ["", d["note"]]
    for c in d["cites"]:
        lines.append("")
        head = f"{c['cite']}" + (f" ({c['term']!r})" if c.get("term") else "") + f"  {c.get('title', '')}".rstrip()
        lines.append(head + f"   [{c['code_name']}" + (f" · {c['place']}" if c.get("place") else "") + (f" · {c['last_amended']}" if c.get("last_amended") else "") + "]")
        if c.get("why"):
            lines.append(f"    {c['why']}")
        if c.get("status"):
            lines.append(f"    ({c['status']})")
        elif c.get("url"):
            lines.append(f"    {c['url']}   ({c['open']})")
        if c.get("body_md"):
            lines.append("")
            lines.append(c["body_md"])
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# amendment history, live from the DCP site (Zoning Resolution)

class _FirstTable(html.parser.HTMLParser):
    """Header cells and rows of the first <table> in a document."""

    def __init__(self):
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._depth = 0
        self._done = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._is_th = False

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag in ("td", "th"):
            self._cell, self._is_th = [], tag == "th"
        elif self._cell is not None and tag == "br":
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if self._done:
            return
        if tag in ("td", "th") and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            (self.headers if self._is_th else self._row if self._row is not None else []).append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self._done = True

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def history(reference: str) -> dict:
    code, sid = split_ref(reference)
    if code != "zr":
        return {"error": "history is available for Zoning Resolution sections only"}
    con = db()
    r = con.execute("SELECT node_id, title, url FROM sections WHERE code = 'zr' AND id = ?", (sid,)).fetchone()
    if not r:
        return {"error": f"no section {sid}"}
    out = {"id": sid, "title": r["title"], "url": r["url"]}
    # the site's "History" link is a Drupal AJAX modal: the response is a list of commands, one of which inserts
    # an HTML table (Effective Date, ULURP/CPC Report, Project Name, Action, Notes, Description)
    req = urllib.request.Request(f"{ZR_BASE}/ajax/get/amendment/section/{r['node_id']}",
                                 headers={"User-Agent": "nyc-zr/0.3 (+https://github.com/plloppii/nyc-zr)",
                                          "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            commands = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return {**out, "error": f"fetch failed: {e}"}
    page = next((c.get("data") for c in commands if isinstance(c, dict) and c.get("command") == "insert" and c.get("data")), "")
    p = _FirstTable()
    p.feed(page)
    if not p.headers and not p.rows:
        text = re.sub(r"<[^>]+>", " ", page)
        out["text"] = re.sub(r"\s+", " ", html.unescape(text)).strip()
        return out
    out["columns"] = p.headers
    out["rows"] = [dict(zip(p.headers, row + [""] * (len(p.headers) - len(row)))) if p.headers else {"cells": row}
                   for row in p.rows]
    return out


def render_history(d: dict) -> str:
    head = f"Amendment history for {d['id']} {d['title']} ({d['url']}):"
    if "rows" not in d:
        return f"{head}\n\n{d.get('text') or '(no history rows)'}"
    cols = d["columns"]
    lines = [head, "", "| " + " | ".join(cols) + " |", "|" + "|".join(" --- " for _ in cols) + "|"]
    for row in d["rows"]:
        lines.append("| " + " | ".join((row.get(c) or "").replace("|", "/") for c in cols) + " |")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# info

def info() -> dict:
    con = db()
    codes = [dict(r) for r in con.execute("SELECT * FROM codes ORDER BY code")]
    extra = {}
    if any(c["code"] == "zr" for c in codes):
        extra["zr_definitions"] = con.execute("SELECT count(*) FROM definitions").fetchone()[0]
        extra["zr_appendices"] = con.execute("SELECT count(*) FROM appendices").fetchone()[0]
    built = con.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
    return {"codes": codes, **extra, "index_built_at": built[0] if built else None, "db": str(DB),
            "topics": TOPICS.exists()}


def render_info(d: dict) -> str:
    lines = []
    for c in d["codes"]:
        when = f"text fetched {c['fetched_at'][:10]}" if c.get("fetched_at") else "fetch date unknown"
        amended = f", latest amendment {c['latest_amendment']}" if c.get("latest_amendment") else ""
        lines.append(f"{c['code']:<4} {c['name']}: {c['sections']} entries; {when}{amended}; source {c['source']}"
                     + (f"\n     {c['note']}" if c.get("note") else ""))
    if "zr_definitions" in d:
        lines.append(f"     plus {d['zr_definitions']} 12-10 definitions and {d['zr_appendices']} appendices")
    lines.append(f"index built {d['index_built_at']} at {d['db']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# command line

def emit(result: dict, render, as_json: bool) -> None:
    if "error" in result:
        if as_json:
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        else:
            extras = {k: v for k, v in result.items() if k != "error" and v}
            print(f"error: {result['error']}" + (f"\n{json.dumps(extras, ensure_ascii=False)}" if extras else ""), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=1) if as_json else render(result))


def main():
    ap = argparse.ArgumentParser(prog="zr.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable JSON instead of text")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", parents=[common], help="full-text search across the loaded codes")
    p.add_argument("query"); p.add_argument("--limit", type=int, default=10)
    p.add_argument("--code", choices=list(CODES), help="one code only (default: all)")
    p.add_argument("--article", help="ZR article, roman numeral or number"); p.add_argument("--chapter", type=int, help="requires --article")
    p.add_argument("--district", help="ZR district, e.g. R6B, C4-4A, M1-5; also matches the parent family")
    p.add_argument("--scope", default="sections", choices=list(SCOPES), help="ZR definitions/appendices too")

    p = sub.add_parser("get", parents=[common], help="one section as markdown (23-344, or code:id such as mdl:31)")
    p.add_argument("id"); p.add_argument("--children", action="store_true", help="include everything under it")
    p.add_argument("--parents", action="store_true", help="show the headings above it")
    p.add_argument("--max-chars", type=int, default=60000, help="truncate text output (0 = never)")

    p = sub.add_parser("define", parents=[common], help="definitions of a term: ZR 12-10, then BC 202, MDL § 4, HMC § 27-2004"); p.add_argument("term"); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("toc", parents=[common], help="outline: ZR articles, one ZR article, a code, or code:heading"); p.add_argument("what", nargs="?")
    p = sub.add_parser("chapter", parents=[common], help="one ZR chapter"); p.add_argument("article"); p.add_argument("chapter", type=int)
    p = sub.add_parser("district", parents=[common], help="ZR sections naming a district"); p.add_argument("district"); p.add_argument("--article"); p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("topic", parents=[common], help="which sections govern a floorplan question; no name lists the topics")
    p.add_argument("name", nargs="?"); p.add_argument("--text", action="store_true", help="include the full text of each cited section")
    p = sub.add_parser("history", parents=[common], help="ZR amendment history, live from the DCP site"); p.add_argument("id")
    sub.add_parser("info", parents=[common], help="loaded codes and what date each is as of")

    a = ap.parse_args()
    if getattr(a, "article", None):
        _, err = resolve_article(a.article)
        if err:
            ap.error(err)
    if a.cmd == "search" and a.chapter and not a.article:
        ap.error("--chapter requires --article (chapter numbers repeat in every article)")

    try:
        ensure_index()
    except (OSError, sqlite3.Error, KeyError, json.JSONDecodeError, ImportError) as e:
        sys.exit(f"error: could not build data/index.sqlite: {e}")

    if a.cmd == "search":
        emit(search(a.query, a.limit, a.code, a.article, a.chapter, a.district, a.scope), render_search, a.json)
    elif a.cmd == "get":
        emit(get_section(a.id, a.children, a.parents), lambda d: render_section(d, a.max_chars), a.json)
    elif a.cmd == "define":
        emit(define_all(a.term, a.limit), render_definitions, a.json)
    elif a.cmd == "toc":
        emit(toc(a.what), render_toc, a.json)
    elif a.cmd == "chapter":
        emit(list_chapter(a.article, a.chapter), render_chapter, a.json)
    elif a.cmd == "district":
        emit(district_sections(a.district, a.article, a.limit), render_district, a.json)
    elif a.cmd == "topic":
        emit(topics(a.name, a.text), render_topics, a.json)
    elif a.cmd == "history":
        emit(history(a.id), render_history, a.json)
    elif a.cmd == "info":
        emit(info(), render_info, a.json)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # output piped into `head` and closed early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
