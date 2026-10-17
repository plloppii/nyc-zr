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
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from typing import Optional

try:  # mcp SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp SDK 1.x
    from mcp.server.fastmcp import FastMCP

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "zr.sqlite"
BASE = "https://zr.planning.nyc.gov"
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11,
         "XII": 12, "XIII": 13, "XIV": 14}

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
    if article is None or article == "":
        return None
    if isinstance(article, int) or str(article).isdigit():
        return int(article)
    return ROMAN.get(str(article).upper())


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


def row_section_brief(r) -> dict:
    return {"id": r["id"], "title": r["title"], "article": r["article"], "chapter": r["chapter"],
            "chapter_title": r["chapter_title"], "level": r["level"], "parent": r["parent"],
            "last_amended": r["last_amended"], "url": r["url"]}


@mcp.tool()
def zr_search(query: str, limit: int = 10, article: Optional[str] = None, chapter: Optional[int] = None,
              district: Optional[str] = None, scope: str = "sections") -> str:
    """Full-text search of the Zoning Resolution.

    query:    words (implicit AND), "quoted phrases", prefix* , or FTS5 operators (AND/OR/NOT/NEAR).
    article:  roman numeral like "II" or number; chapter: number within that article.
    district: a zoning district token such as "R6B", "C4-4A", "M1-5" to keep only sections that mention it
              or its parent family (R6B also matches sections that say "R6" or "R6 through R12").
    scope:    "sections" (default), "definitions", "appendices", or "all".
    Returns ranked hits with snippets. Falls back to OR-matching if the AND query finds nothing.
    """
    limit = max(1, min(int(limit), 50))
    con = db()
    out = {"query": query, "hits": []}
    anum = article_num(article)

    def run_sections(fq):
        sql = ["SELECT s.*, bm25(sections_fts, 8.0, 1.0) AS rank,",
               "snippet(sections_fts, 2, '[', ']', ' … ', 28) AS snip",
               "FROM sections_fts JOIN sections s ON s.id = sections_fts.id",
               "WHERE sections_fts MATCH ?"]
        params = [fq]
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
            "SELECT d.*, bm25(definitions_fts, 8.0, 1.0) AS rank, snippet(definitions_fts, 2, '[', ']', ' … ', 28) AS snip "
            "FROM definitions_fts JOIN definitions d ON d.rowid = definitions_fts.rowid_ref "
            "WHERE definitions_fts MATCH ? ORDER BY rank LIMIT ?", (fq, limit)).fetchall()

    def run_appendices(fq):
        return con.execute(
            "SELECT a.id, a.title, a.url, a.last_amended, bm25(appendices_fts, 8.0, 1.0) AS rank, "
            "snippet(appendices_fts, 2, '[', ']', ' … ', 28) AS snip "
            "FROM appendices_fts JOIN appendices a ON a.id = appendices_fts.id "
            "WHERE appendices_fts MATCH ? ORDER BY rank LIMIT ?", (fq, limit)).fetchall()

    fq = fts_query(query)
    try:
        if scope in ("sections", "all"):
            rows = run_sections(fq)
            if not rows and " " in fq and " OR " not in fq:
                rows = run_sections(" OR ".join(fq.split()))
                out["note"] = "no section matched all terms; showing OR matches"
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
        return json.dumps({"error": f"bad query syntax: {e}", "fts_query": fq})
    out["count"] = len(out["hits"])
    return json.dumps(out, ensure_ascii=False, indent=1)


def _children(con, sid: str, depth: int = 10) -> list:
    rows = con.execute("SELECT * FROM sections WHERE parent = ? ORDER BY sort_key", (sid,)).fetchall()
    out = []
    for r in rows:
        out.append(r)
        if depth > 0:
            out.extend(_children(con, r["id"], depth - 1))
    return out


@mcp.tool()
def zr_get_section(id: str, include_children: bool = False, include_parents: bool = False,
                   max_chars: int = 60000) -> str:
    """Read one section by id (e.g. "23-344") as markdown with metadata.

    include_children: also return every subsection beneath it, in order (use for a whole group like "23-30").
    include_parents:  prepend the parent chain (e.g. 23-30 > 23-34) so context headings are visible.
    Defined terms appear as **_term_**; cross-references are links. Figures are image URLs.
    """
    con = db()
    sid = id.strip()
    r = con.execute("SELECT * FROM sections WHERE id = ?", (sid,)).fetchone()
    if not r:
        like = con.execute("SELECT id, title FROM sections WHERE id LIKE ? ORDER BY sort_key LIMIT 15", (sid + "%",)).fetchall()
        return json.dumps({"error": f"no section {sid}", "did_you_mean": [dict(x) for x in like]})
    parts = []
    if include_parents:
        chain = []
        p = r["parent"]
        while p:
            pr = con.execute("SELECT id, title, parent FROM sections WHERE id = ?", (p,)).fetchone()
            if not pr:
                break
            chain.append(f"{pr['id']} {pr['title']}")
            p = pr["parent"]
        if chain:
            parts.append("Context: " + " > ".join(reversed(chain)))
    meta = row_section_brief(r)
    meta.update({"defined_terms": json.loads(r["defined_terms"]), "cross_refs": json.loads(r["cross_refs"]),
                 "districts": json.loads(r["districts"]), "node_id": r["node_id"]})
    parts.append("```json\n" + json.dumps(meta, ensure_ascii=False) + "\n```")
    parts.append(f"# {r['id']} {r['title']}\n_Last amended {r['last_amended'] or 'n/a'} · {r['url']}_\n\n{r['body_md']}")
    if include_children:
        for c in _children(con, sid):
            hashes = "#" * min(6, 1 + (c["level"] - r["level"]))
            parts.append(f"{hashes} {c['id']} {c['title']}\n_Last amended {c['last_amended'] or 'n/a'} · {c['url']}_\n\n{c['body_md']}")
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n… [truncated at {max_chars} chars; raise max_chars or fetch children individually]"
    return text


@mcp.tool()
def zr_list_chapter(article: str, chapter: int) -> str:
    """Table of contents for one chapter: every section id, title, level, last-amended date, in order."""
    con = db()
    anum = article_num(article)
    rows = con.execute("SELECT id, title, level, parent, last_amended, length(text) AS chars, chapter_title, article_title "
                       "FROM sections WHERE article_num = ? AND chapter = ? ORDER BY sort_key", (anum, int(chapter))).fetchall()
    if not rows:
        return json.dumps({"error": f"no chapter {chapter} in article {article}"})
    lines = [f"Article {article.upper() if isinstance(article, str) else article} — {rows[0]['article_title']}",
             f"Chapter {chapter} — {rows[0]['chapter_title']}", ""]
    for r in rows:
        indent = "  " * (r["level"] - 1)
        lines.append(f"{indent}{r['id']}  {r['title']}  ({r['last_amended'] or 'n/a'}; {r['chars']} chars)")
    return "\n".join(lines)


@mcp.tool()
def zr_toc(article: Optional[str] = None) -> str:
    """Outline of the Resolution: all articles and chapters; or, for one article, its chapters and top-level sections."""
    toc = json.loads((ROOT / "data" / "toc.json").read_text(encoding="utf-8"))
    anum = article_num(article)
    lines = []
    for a in toc["articles"]:
        if anum and a["article_num"] != anum:
            continue
        lines.append(f"Article {a['article']} — {a['article_title']}")
        for ch in a["chapters"]:
            lines.append(f"  Chapter {ch['chapter']} — {ch['chapter_title']}  ({len(ch['sections'])} sections)")
            if anum:
                for s in ch["sections"]:
                    if s["level"] == 1:
                        lines.append(f"      {s['id']}  {s['title']}")
    if not anum:
        lines.append("Appendices:")
        for ap in toc["appendices"]:
            lines.append(f"  {ap['id']}  — {ap['title']}")
    return "\n".join(lines)


@mcp.tool()
def zr_define(term: str, limit: int = 10) -> str:
    """Definitions from Section 12-10 for a defined term (italicized in the text), e.g. "corner lot", "floor area".

    Returns general definitions first, then chapter-specific ones (with their applicability). Partial matches allowed.
    """
    con = db()
    t = term.strip().lower().strip("*_ ")
    rows = con.execute(
        "SELECT d.* FROM definition_aliases a JOIN definitions d ON d.rowid = a.rowid_ref WHERE a.alias = ? "
        "ORDER BY CASE d.scope WHEN 'general' THEN 0 ELSE 1 END, d.term LIMIT ?", (t, limit)).fetchall()
    if not rows and t.endswith("s"):
        rows = con.execute(
            "SELECT d.* FROM definition_aliases a JOIN definitions d ON d.rowid = a.rowid_ref WHERE a.alias = ? "
            "ORDER BY CASE d.scope WHEN 'general' THEN 0 ELSE 1 END, d.term LIMIT ?", (t[:-1], limit)).fetchall()
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
        return json.dumps({"error": f"no definition matching '{term}'"})
    # follow "see **_lot, corner_**" style pointers one hop
    rows = list(rows)
    for r in list(rows):
        m = re.match(r"^see \*\*_(.+?)_\*\*", (r["definition_md"] or "").strip(), re.I)
        if m:
            target = m.group(1).strip().lower()
            rows.extend(x for x in con.execute(
                "SELECT d.* FROM definition_aliases a JOIN definitions d ON d.rowid = a.rowid_ref WHERE a.alias = ? "
                "ORDER BY CASE d.scope WHEN 'general' THEN 0 ELSE 1 END", (target,)).fetchall()
                if x["rowid"] not in {y["rowid"] for y in rows})
    parts = []
    for r in rows[:limit + 3]:
        parts.append(f"### {r['term']}\n_{r['applicability']}"
                     + (f" · last amended {r['last_amended']}" if r["last_amended"] else "")
                     + f" · source {r['source_section']} · {r['url']}_\n\n{r['definition_md']}")
    return "\n\n".join(parts)


@mcp.tool()
def zr_district(district: str, article: Optional[str] = None, limit: int = 100) -> str:
    """List sections whose text names a zoning district token (e.g. "R6B", "C6-4", "M1-5") or its parent family.

    exact=true in the result marks sections naming the token itself; the rest name the family (e.g. "R6").
    """
    con = db()
    d = district.strip().upper()
    fam = district_family(d)
    ph = ",".join("?" * len(fam))
    sql = (f"SELECT s.id, s.title, s.article, s.chapter, s.chapter_title, s.last_amended, s.url, "
           f"max(sd.district = ?) AS exact FROM section_districts sd "
           f"JOIN sections s ON s.id = sd.section_id WHERE sd.district IN ({ph})")
    params: list = [d, *fam]
    anum = article_num(article)
    if anum:
        sql += " AND s.article_num = ?"; params.append(anum)
    sql += " GROUP BY s.id ORDER BY exact DESC, s.sort_key LIMIT ?"; params.append(max(1, min(int(limit), 500)))
    rows = con.execute(sql, params).fetchall()
    total = con.execute(f"SELECT count(DISTINCT section_id) FROM section_districts WHERE district IN ({ph})", fam).fetchone()[0]
    return json.dumps({"district": d, "family": fam, "total": total,
                       "sections": [{**dict(r), "exact": bool(r["exact"])} for r in rows]}, ensure_ascii=False, indent=1)


@mcp.tool()
def zr_history(id: str) -> str:
    """Amendment history of a section, fetched live from zr.planning.nyc.gov (dates and CPC/Council actions)."""
    import requests
    from bs4 import BeautifulSoup
    con = db()
    r = con.execute("SELECT node_id, title, url FROM sections WHERE id = ?", (id.strip(),)).fetchone()
    if not r:
        return json.dumps({"error": f"no section {id}"})
    try:
        # the site's "History" link is a Drupal AJAX modal: /ajax/... returns a JSON list of commands,
        # one of which inserts an HTML table (Effective Date, ULURP/CPC Report, Project Name, Action, Notes, Description)
        resp = requests.get(f"{BASE}/ajax/get/amendment/section/{r['node_id']}", timeout=30,
                            headers={"User-Agent": "nyc-zr-mcp/0.1", "X-Requested-With": "XMLHttpRequest"})
        resp.raise_for_status()
        commands = resp.json()
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"fetch failed: {e}", "url": r["url"]})
    html = next((c.get("data") for c in commands if c.get("command") == "insert" and c.get("data")), "")
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        text = re.sub(r"\n{2,}", "\n", soup.get_text("\n")).strip()
        return f"Amendment history for {id} {r['title']} ({r['url']}):\n\n{text or '(no history rows)'}"
    headers = [re.sub(r"\s+", " ", th.get_text(" ")).strip() for th in table.find_all("th")]
    rows = []
    for tr in table.find_all("tr"):
        cells = [re.sub(r"\s+", " ", td.get_text(" ")).strip().replace("|", "/") for td in tr.find_all("td")]
        if cells:
            rows.append(cells)
    lines = [f"Amendment history for {id} {r['title']} ({r['url']}):", "",
             "| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(c + [""] * (len(headers) - len(c))) + " |" for c in rows]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
