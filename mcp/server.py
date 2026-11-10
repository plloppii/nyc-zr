#!/usr/bin/env python3
"""MCP server over the NYC Zoning Resolution index (data/zr.sqlite).

Tools:
  zr_search        full-text search (BM25) across sections, definitions, appendices; filter by article/chapter/district
  zr_get_section   one section as markdown (+ optional children / parent chain)
  zr_list_chapter  table of contents for an article+chapter
  zr_toc           articles and chapters (or sections of one article)
  zr_define        definitions of a term from 12-10 (general + chapter-specific)
  zr_district      sections that explicitly mention a zoning district (e.g. R6B)
  zr_history       live amendment history for a section from zr.planning.nyc.gov

The plain functions (search, get_section, list_chapter, toc, define, district_sections, history) return dicts and are shared
with scripts/zr.py; the render_* functions turn them into text; the zr_* tools wrap both for MCP clients. This file
is standard library only. The mcp package is needed only to serve.
"""
from __future__ import annotations

import html.parser
import json
import pathlib
import re
import sqlite3
import urllib.error
import urllib.request
from typing import Optional

try:  # mcp SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:  # mcp SDK 1.x
        from mcp.server.fastmcp import FastMCP
    except ImportError:  # no MCP SDK installed: the functions below still serve scripts/zr.py
        class FastMCP:  # minimal stand-in so the @mcp.tool() decorators are no-ops
            def __init__(self, *a, **k):
                pass

            def tool(self, *a, **k):
                return lambda fn: fn

            def run(self, *a, **k):
                raise SystemExit("the mcp package is not installed; run `uv sync` to serve over MCP, or use scripts/zr.py")

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "zr.sqlite"
TOC = ROOT / "data" / "toc.json"
BASE = "https://zr.planning.nyc.gov"
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11,
         "XII": 12, "XIII": 13, "XIV": 14}
SCOPES = ("sections", "definitions", "appendices", "all")
# bm25() weights follow the FTS5 column order: 0 = the unindexed id, 1 = title, 2 = text
TITLE_WEIGHT = 8.0

mcp = FastMCP("nyc-zr", instructions=(
    "NYC Zoning Resolution (text as published at zr.planning.nyc.gov). "
    "Use zr_search to find sections, zr_get_section to read one (include_children=true for a whole group), "
    "zr_define for italicized defined terms, zr_toc / zr_list_chapter to navigate. "
    "Section ids look like 23-344 (Article II = 2x-, Chapter 3 = 23-). Always cite the section id and url."
))


def db() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def article_num(article: str | int | None) -> int | None:
    if article is None or str(article).strip() == "":
        return None
    a = str(article).strip()
    if a.isdigit():
        n = int(a)
    else:
        n = ROMAN.get(a.upper())
    return n if n is not None and 1 <= n <= 14 else None


def resolve_article(article: str | int | None) -> tuple[Optional[int], Optional[str]]:
    """(article number, error message). No article -> (None, None); an unknown one -> (None, "unknown article ...")."""
    if article is None or str(article).strip() == "":
        return None, None
    n = article_num(article)
    if n is None:
        return None, f"unknown article {str(article)!r}: use a roman numeral I–XIV or a number 1–14"
    return n, None


def fts_query(q: str) -> str:
    """Turn a natural query into FTS5 syntax. Quoted phrases and AND/OR/NOT/NEAR pass through."""
    q = q.strip()
    if not q:
        return '""'
    if re.search(r'["()]|\b(AND|OR|NOT|NEAR)\b', q):
        return q
    toks = [t for t in re.split(r"\s+", q) if t]
    out = []
    for t in toks:
        pref = t.endswith("*")
        t = t.rstrip("*").replace('"', "")
        if not t:
            continue
        out.append(f'"{t}"' + ("*" if pref else ""))
    return " ".join(out)


_TERM_RE = re.compile(r'"[^"]*"\*?|\S+')
_OPERATOR_RE = re.compile(r"\b(AND|OR|NOT|NEAR)\b|[()]")


def split_terms(fq: str) -> list[str]:
    """Split an FTS5 query into terms, keeping "quoted phrases" whole."""
    return _TERM_RE.findall(fq)


def can_relax(fq: str) -> bool:
    """True when the query is only words and quoted phrases, so OR-ing its terms is a meaningful fallback."""
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


BRIEF_KEYS = ("id", "title", "article", "chapter", "chapter_title", "level", "parent", "last_amended", "url")


def row_section_brief(r) -> dict:
    return {k: r[k] for k in BRIEF_KEYS}


def _heading(s: dict) -> str:
    return f"_Last amended {s['last_amended'] or 'n/a'} · {s['url']}_"


# ----------------------------------------------------------------------------------------------------------------
# search

def search(query: str, limit: int = 10, article: Optional[str] = None, chapter: Optional[int] = None,
           district: Optional[str] = None, scope: str = "sections") -> dict:
    limit = max(1, min(int(limit), 50))
    anum, err = resolve_article(article)
    if err:
        return {"error": err}
    if chapter and anum is None:
        return {"error": "chapter requires article (chapter numbers repeat in every article)"}
    if scope not in SCOPES:
        return {"error": f"unknown scope {scope!r}: use one of {', '.join(SCOPES)}"}
    con = db()
    out: dict = {"query": query, "hits": []}

    def run_sections(fq):
        sql = [f"SELECT s.*, bm25(sections_fts, 0, {TITLE_WEIGHT}, 1.0) AS rank,",
               "snippet(sections_fts, 2, '[', ']', ' … ', 28) AS snip",
               "FROM sections_fts JOIN sections s ON s.id = sections_fts.id",
               "WHERE sections_fts MATCH ?"]
        params: list = [fq]
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
                out["hits"].append({"type": "section", **row_section_brief(r), "snippet": r["snip"],
                                    "districts": json.loads(r["districts"])[:12]})
        if scope in ("definitions", "all"):
            for r in run_definitions(fq):
                out["hits"].append({"type": "definition", "term": r["term"], "scope": r["scope"],
                                    "applicability": r["applicability"], "source_section": r["source_section"],
                                    "last_amended": r["last_amended"], "url": r["url"], "snippet": r["snip"]})
        if scope in ("appendices", "all"):
            for r in run_appendices(fq):
                out["hits"].append({"type": "appendix", "id": r["id"], "title": r["title"], "url": r["url"],
                                    "last_amended": r["last_amended"], "snippet": r["snip"]})
    except sqlite3.OperationalError as e:
        return {"error": f"bad query syntax: {e}", "fts_query": fq}
    out["count"] = len(out["hits"])
    return out


def render_search(d: dict) -> str:
    lines = [f"{d['count']} hits for {d['query']!r}" + (f"  ({d['note']})" if d.get("note") else "")]
    for h in d["hits"]:
        if h["type"] == "section":
            lines.append(f"\n{h['id']}  {h['title']}   [Art {h['article']} Ch {h['chapter']} · {h['last_amended']}]\n    {h['url']}\n    {h['snippet']}")
        elif h["type"] == "definition":
            lines.append(f"\n[def] {h['term']}  ({h['applicability']})\n    {h['snippet']}")
        else:
            lines.append(f"\n[appendix] {h['title']}\n    {h['url']}\n    {h['snippet']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# sections

def _children(con, sid: str, depth: int = 10) -> list:
    rows = con.execute("SELECT * FROM sections WHERE parent = ? ORDER BY sort_key", (sid,)).fetchall()
    out = []
    for r in rows:
        out.append(r)
        if depth > 0:
            out.extend(_children(con, r["id"], depth - 1))
    return out


def get_section(id: str, include_children: bool = False, include_parents: bool = False) -> dict:
    con = db()
    sid = id.strip()
    r = con.execute("SELECT * FROM sections WHERE id = ?", (sid,)).fetchone()
    if not r:
        like = con.execute("SELECT id, title FROM sections WHERE id LIKE ? ORDER BY sort_key LIMIT 15", (sid + "%",)).fetchall()
        return {"error": f"no section {sid}", "did_you_mean": [dict(x) for x in like]}
    d = row_section_brief(r)
    d.update({"defined_terms": json.loads(r["defined_terms"]), "cross_refs": json.loads(r["cross_refs"]),
              "districts": json.loads(r["districts"]), "node_id": r["node_id"], "body_md": r["body_md"]})
    if include_parents:
        chain = []
        p = r["parent"]
        while p:
            pr = con.execute("SELECT id, title, parent FROM sections WHERE id = ?", (p,)).fetchone()
            if not pr:
                break
            chain.append({"id": pr["id"], "title": pr["title"]})
            p = pr["parent"]
        d["parents"] = list(reversed(chain))
    if include_children:
        d["children"] = [{**row_section_brief(c), "body_md": c["body_md"]} for c in _children(con, sid)]
    elif not (r["body_md"] or "").strip():
        # a bare group heading: say what is under it so the reader need not guess at a second call
        d["subsections"] = [dict(x) for x in con.execute(
            "SELECT id, title, last_amended FROM sections WHERE parent = ? ORDER BY sort_key", (sid,)).fetchall()]
    return d


def render_section(d: dict, max_chars: int = 60000) -> str:
    parts = []
    if d.get("parents"):
        parts.append("Context: " + " > ".join(f"{p['id']} {p['title']}" for p in d["parents"]))
    meta = {k: d[k] for k in (*BRIEF_KEYS, "defined_terms", "cross_refs", "districts", "node_id")}
    parts.append("```json\n" + json.dumps(meta, ensure_ascii=False) + "\n```")
    parts.append(f"# {d['id']} {d['title']}\n{_heading(d)}\n\n{d['body_md']}".rstrip())
    if d.get("subsections"):
        parts.append("This heading has no text of its own. Subsections (pass --children / include_children=true to read them all):\n"
                     + "\n".join(f"- {s['id']}  {s['title']}  ({s['last_amended'] or 'n/a'})" for s in d["subsections"]))
    for c in d.get("children", []):
        hashes = "#" * min(6, 1 + (c["level"] - d["level"]))
        parts.append(f"{hashes} {c['id']} {c['title']}\n{_heading(c)}\n\n{c['body_md']}".rstrip())
    text = "\n\n".join(parts)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + f"\n\n… [truncated at {max_chars} chars; raise max_chars or fetch children individually]"
    return text


# ----------------------------------------------------------------------------------------------------------------
# navigation

def list_chapter(article: str, chapter: int) -> dict:
    anum, err = resolve_article(article)
    if err or anum is None:
        return {"error": err or "article is required"}
    con = db()
    rows = con.execute("SELECT id, title, level, parent, last_amended, length(text) AS chars, chapter_title, article_title, article "
                       "FROM sections WHERE article_num = ? AND chapter = ? ORDER BY sort_key", (anum, int(chapter))).fetchall()
    if not rows:
        return {"error": f"no chapter {chapter} in article {article}"}
    return {"article": rows[0]["article"], "article_title": rows[0]["article_title"], "chapter": int(chapter),
            "chapter_title": rows[0]["chapter_title"],
            "sections": [{k: r[k] for k in ("id", "title", "level", "parent", "last_amended", "chars")} for r in rows]}


def render_chapter(d: dict) -> str:
    lines = [f"Article {d['article']} — {d['article_title']}", f"Chapter {d['chapter']} — {d['chapter_title']}", ""]
    for r in d["sections"]:
        indent = "  " * (r["level"] - 1)
        lines.append(f"{indent}{r['id']}  {r['title']}  ({r['last_amended'] or 'n/a'}; {r['chars']} chars)")
    return "\n".join(lines)


def toc(article: Optional[str] = None) -> dict:
    anum, err = resolve_article(article)
    if err:
        return {"error": err}
    data = json.loads(TOC.read_text(encoding="utf-8"))
    if anum is None:
        return {"articles": [{"article": a["article"], "article_num": a["article_num"], "article_title": a["article_title"],
                              "chapters": [{"chapter": ch["chapter"], "chapter_title": ch["chapter_title"],
                                            "sections": len(ch["sections"])} for ch in a["chapters"]]}
                             for a in data["articles"]],
                "appendices": data["appendices"]}
    arts = [a for a in data["articles"] if a["article_num"] == anum]
    if not arts:
        return {"error": f"no article {article} in the table of contents"}
    return {"articles": arts}


def render_toc(d: dict) -> str:
    lines = []
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
# definitions

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
        # singular/plural fallback
        alt = t[:-1] if t.endswith("s") else t + "s"
        rows = con.execute("SELECT * FROM definitions WHERE lower(term) LIKE ? ORDER BY length(term) LIMIT ?",
                           (f"%{alt}%", limit)).fetchall()
    if not rows:
        return {"error": f"no definition matching '{term}'"}
    # follow "see **_lot, corner_**" style pointers one hop
    rows = list(rows)
    for r in list(rows):
        m = re.match(r"^see \*\*_(.+?)_\*\*", (r["definition_md"] or "").strip(), re.I)
        if m:
            have = {y["rowid"] for y in rows}
            rows.extend(x for x in con.execute(_BY_ALIAS, (m.group(1).strip().lower(), limit)).fetchall()
                        if x["rowid"] not in have)
    defs = [{k: r[k] for k in _DEF_KEYS} for r in rows[:limit + 3]]
    return {"term": term, "count": len(defs), "definitions": defs}


def render_definitions(d: dict) -> str:
    parts = []
    for r in d["definitions"]:
        parts.append(f"### {r['term']}\n_{r['applicability']}"
                     + (f" · last amended {r['last_amended']}" if r["last_amended"] else "")
                     + f" · source {r['source_section']} · {r['url']}_\n\n{r['definition_md']}")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------------------------------------------
# districts

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
           f"JOIN sections s ON s.id = sd.section_id WHERE sd.district IN ({ph})")
    params: list = [d, *fam]
    if anum:
        sql += " AND s.article_num = ?"; params.append(anum)
    sql += " GROUP BY s.id ORDER BY exact DESC, s.sort_key LIMIT ?"; params.append(max(1, min(int(limit), 500)))
    rows = con.execute(sql, params).fetchall()
    total = con.execute(f"SELECT count(DISTINCT section_id) FROM section_districts WHERE district IN ({ph})", fam).fetchone()[0]
    return {"district": d, "family": fam, "total": total,
            "sections": [{**dict(r), "exact": bool(r["exact"])} for r in rows]}


# ----------------------------------------------------------------------------------------------------------------
# amendment history (live, standard library only)

class _FirstTable(html.parser.HTMLParser):
    """Collect the header cells and rows of the first <table> in a document."""

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


def history(id: str) -> dict:
    con = db()
    r = con.execute("SELECT node_id, title, url FROM sections WHERE id = ?", (id.strip(),)).fetchone()
    if not r:
        return {"error": f"no section {id}"}
    out = {"id": id.strip(), "title": r["title"], "url": r["url"]}
    # the site's "History" link is a Drupal AJAX modal: /ajax/... returns a JSON list of commands, one of which
    # inserts an HTML table (Effective Date, ULURP/CPC Report, Project Name, Action, Notes, Description)
    req = urllib.request.Request(f"{BASE}/ajax/get/amendment/section/{r['node_id']}",
                                 headers={"User-Agent": "nyc-zr/0.2 (+https://github.com/plloppii/nyc-zr)",
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
# MCP tools

def _dump(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1)


@mcp.tool()
def zr_search(query: str, limit: int = 10, article: Optional[str] = None, chapter: Optional[int] = None,
              district: Optional[str] = None, scope: str = "sections") -> str:
    """Full-text search of the Zoning Resolution.

    query:    words (implicit AND), "quoted phrases", prefix* , or FTS5 operators (AND/OR/NOT/NEAR).
    article:  roman numeral like "II" or number; chapter: number within that article (requires article).
    district: a zoning district token such as "R6B", "C4-4A", "M1-5" to keep only sections that mention it
              or its parent family (R6B also matches sections that say "R6" or "R6 through R12").
    scope:    "sections" (default), "definitions", "appendices", or "all".
    Returns ranked hits with snippets. Falls back to OR-matching if the AND query finds nothing.
    """
    return _dump(search(query, limit, article, chapter, district, scope))


@mcp.tool()
def zr_get_section(id: str, include_children: bool = False, include_parents: bool = False,
                   max_chars: int = 60000) -> str:
    """Read one section by id (e.g. "23-344") as markdown with metadata.

    include_children: also return every subsection beneath it, in order (use for a whole group like "23-30").
    include_parents:  prepend the parent chain (e.g. 23-30 > 23-34) so context headings are visible.
    Defined terms appear as **_term_**; cross-references are links. Figures are image URLs.
    """
    d = get_section(id, include_children, include_parents)
    return _dump(d) if "error" in d else render_section(d, max_chars)


@mcp.tool()
def zr_list_chapter(article: str, chapter: int) -> str:
    """Table of contents for one chapter: every section id, title, level, last-amended date, in order."""
    d = list_chapter(article, chapter)
    return _dump(d) if "error" in d else render_chapter(d)


@mcp.tool()
def zr_toc(article: Optional[str] = None) -> str:
    """Outline of the Resolution: all articles and chapters; or, for one article, its chapters and top-level sections."""
    d = toc(article)
    return _dump(d) if "error" in d else render_toc(d)


@mcp.tool()
def zr_define(term: str, limit: int = 10) -> str:
    """Definitions from Section 12-10 for a defined term (italicized in the text), e.g. "corner lot", "floor area".

    Returns general definitions first, then chapter-specific ones (with their applicability). Partial matches allowed.
    """
    d = define(term, limit)
    return _dump(d) if "error" in d else render_definitions(d)


@mcp.tool()
def zr_district(district: str, article: Optional[str] = None, limit: int = 100) -> str:
    """List sections whose text names a zoning district token (e.g. "R6B", "C6-4", "M1-5") or its parent family.

    exact=true in the result marks sections naming the token itself; the rest name the family (e.g. "R6").
    Ranges in the text ("R6 through R12") count as naming every district between their endpoints.
    """
    return _dump(district_sections(district, article, limit))


@mcp.tool()
def zr_history(id: str) -> str:
    """Amendment history of a section, fetched live from zr.planning.nyc.gov (dates and CPC/Council actions)."""
    d = history(id)
    return _dump(d) if "error" in d else render_history(d)


if __name__ == "__main__":
    mcp.run()
