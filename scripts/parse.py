#!/usr/bin/env python3
"""Parse the cached chapter/appendix HTML into:

  sections/<article>/<chapter>/<section-id>.md   one markdown file per section, YAML frontmatter
  sections/appendices/<slug>.md                   one file per appendix
  data/sections.jsonl                             one JSON record per section (same data)
  data/definitions.jsonl                          every defined term from 12-10 (one row per definition)
  data/toc.json                                   article -> chapter -> section tree
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from zrhtml import BASE, html_to_markdown, markdown_to_text, norm_ws  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_CH = ROOT / "raw/chapters"
RAW_AP = ROOT / "raw/appendices"
OUT = ROOT / "sections"
DATA = ROOT / "data"

# Zoning district tokens, e.g. R6B, R7-2, C4-4A, C1-3 overlays, M1-5/R7X, M1-1, PARK, BPC
DISTRICT_RE = re.compile(r"\b(R(?:1[0-2]|[1-9])(?:[A-Z]|-\d[A-Z]?)?|C[1-8](?:-\d{1,2}[A-Z]?)?|M[1-3](?:-\d[A-Z]?)?)\b")


def roman_to_int(s: str) -> int:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50}
    total = 0
    for i, ch in enumerate(s.lower()):
        v = vals[ch]
        if i + 1 < len(s) and vals[s[i + 1].lower()] > v:
            total -= v
        else:
            total += v
    return total


DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def parse_date(node) -> str | None:
    """ISO date from a <time datetime> if present, else from a visible m/d/yyyy string."""
    if node is None:
        return None
    t = node.select_one("time") if hasattr(node, "select_one") else None
    if t is not None and t.has_attr("datetime"):
        return t["datetime"][:10]
    m = DATE_RE.search(node.get_text(" "))
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def section_level(sid: str) -> int:
    tail = sid.split("-", 1)[1]
    if len(tail) == 2 and tail.endswith("0"):
        return 1
    if len(tail) == 2:
        return 2
    return len(tail)  # 3 -> level 3, 4 -> level 4


def numeric_parent(sid: str) -> str | None:
    head, tail = sid.split("-", 1)
    if len(tail) == 2:
        return None if tail.endswith("0") else f"{head}-{tail[0]}0"
    return f"{head}-{tail[:-1]}"


def parse_chapter(path: pathlib.Path):
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    m = re.match(r"(article-[ivx]+)__chapter-(\d+)", path.stem)
    article_slug, chapter_num = m.group(1), int(m.group(2))
    article_roman = article_slug.split("-")[1].upper()

    # titles
    art_node = soup.select_one("article.node--type-article.is-expanded .field--name-field-article-title")
    article_title = norm_ws(art_node.get_text()).strip() if art_node else ""
    ch_full = soup.select_one("article.node--type-chapter.node--view-mode-full")
    chapter_title = ""
    chapter_last_amended = None
    if ch_full:
        h = ch_full.select_one(".chapter-title, h1, h2")
        raw = norm_ws(h.get_text()).strip() if h else norm_ws(ch_full.get_text(" ")).strip()[:200]
        chapter_title = re.sub(r"^Chapter\s+\d+\s*-\s*", "", raw).strip()
        chapter_last_amended = parse_date(ch_full.select_one(".amended"))
    if not chapter_title:
        nav = soup.select_one(f'a.chapter-title[href="/{article_slug}/chapter-{chapter_num}"]')
        if nav:
            chapter_title = re.sub(r"^Chapter\s+\d+\s*-\s*", "", norm_ws(nav.get_text()).strip())

    # parent map from left nav
    parent_of: dict[str, str | None] = {}
    for nav_item in soup.select("article.nav-item[data-section]"):
        sid = nav_item["data-section"].strip()
        holder = nav_item.find_parent("div", class_="section-children")
        parent_of[sid] = holder["data-children-for"].strip() if holder and holder.has_attr("data-children-for") else None

    records = []
    for art in soup.select("article.node--type-section.node--view-mode-default"):
        sid = (art.get("data-section") or art.get("id") or "").strip()
        if not sid:
            continue
        node_id = art.get("data-node-id")
        h3 = art.select_one(".section-header-wrapper h3")
        title = norm_ws(h3.get_text()).strip() if h3 else ""
        last_amended = parse_date(art.select_one(".amended"))
        body = art.select_one(".sec-body")
        md, col = ("", None)
        definitions = []
        if body:
            # the definitions section (12-10) carries a structured defined-terms view
            dt_view = body.select_one(".view-defined-terms")
            if dt_view:
                definitions = parse_definitions(dt_view, sid)
                dt_view.decompose()
            md, col = html_to_markdown(body)
            if definitions:
                md = md + "\n\n" + "\n\n".join(
                    f"### {d['term']}\n\n_{d['applicability']}" + (f" · last amended {d['last_amended']}" if d['last_amended'] else "") + f"_\n\n{d['definition_md']}"
                    for d in definitions)
        text = markdown_to_text(md)
        districts = sorted(set(DISTRICT_RE.findall(title + " " + text)))
        rec = {
            "id": sid,
            "node_id": int(node_id) if node_id and node_id.isdigit() else None,
            "article": article_roman,
            "article_num": roman_to_int(article_roman),
            "article_title": article_title,
            "chapter": chapter_num,
            "chapter_title": chapter_title,
            "title": title,
            "level": section_level(sid),
            "parent": parent_of.get(sid, numeric_parent(sid)),
            "url": f"{BASE}/{article_slug}/chapter-{chapter_num}/{sid}",
            "last_amended": last_amended,
            "defined_terms": sorted(col.defined_terms) if col else [],
            "cross_refs": sorted(col.cross_refs) if col else [],
            "images": col.images if col else [],
            "districts": districts,
            "body_md": md,
            "text": text,
            "definitions": definitions,
        }
        records.append(rec)
    meta = {
        "article": article_roman, "article_num": roman_to_int(article_roman), "article_title": article_title,
        "chapter": chapter_num, "chapter_title": chapter_title, "chapter_last_amended": chapter_last_amended,
        "url": f"{BASE}/{article_slug}/chapter-{chapter_num}", "slug": f"{article_slug}/chapter-{chapter_num}",
    }
    return meta, records


def parse_definitions(view, source_sid):
    out = []
    for term_art in view.select("article.node--type-defined-term"):
        variants = norm_ws(term_art.select_one(".defined-term__variants").get_text(" ")).strip() if term_art.select_one(".defined-term__variants") else ""
        for d in term_art.select(".paragraph--type--definition"):
            title = d.select_one(".definition__title")
            term = norm_ws(title.get_text()).strip() if title else ""
            app = d.select_one(".definition__applicability")
            applicability = norm_ws(app.get_text()).strip() if app else ""
            last_amended = parse_date(d.select_one(".definition__amended-date"))
            body = d.select_one(".definition__definition")
            md, col = html_to_markdown(body) if body else ("", None)
            src = re.match(r"FROM \[(\d{1,3}-\d{2,4})\]", md)
            out.append({
                "term": term,
                "variants": variants,
                "applicability": applicability,
                "scope": "general" if applicability.lower().startswith("general") else "chapter",
                "last_amended": last_amended,
                "source_section": src.group(1) if src else source_sid,
                "definition_md": md,
                "text": markdown_to_text(md),
                "defined_terms": sorted(col.defined_terms) if col else [],
                "cross_refs": sorted(col.cross_refs) if col else [],
                "url": f"{BASE}/article-i/chapter-2/12-10#term-{term}",
            })
    return out


def parse_appendix(path: pathlib.Path):
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    slug = path.stem
    main = soup.select_one("main") or soup.body
    node = main.select_one("article.node--type-appendix, article.node--type-child-appendix") if main else None
    h = (node or main).select_one("h1, h2, .page-title")
    title = norm_ws(h.get_text()).strip() if h else slug
    last_amended = parse_date((node or main).select_one(".amended")) or parse_date((node or main).select_one("time"))
    # strip nav/leftnav/actions
    for sel in (".leftnav", "nav", ".action-links", ".action-link-content", ".breadcrumb", ".region-sidebar-first",
                ".block-system-breadcrumb-block", ".view-amendment-history-popup", "header", "footer"):
        for x in (node or main).select(sel):
            x.decompose()
    body = (node or main).select_one(".field--name-body") or (node or main)
    md, col = html_to_markdown(body)
    text = markdown_to_text(md)
    return {
        "id": slug,
        "title": title,
        "url": f"{BASE}/{slug}",
        "last_amended": last_amended,
        "cross_refs": sorted(col.cross_refs),
        "images": col.images,
        "districts": sorted(set(DISTRICT_RE.findall(text))),
        "body_md": md,
        "text": text,
    }


def write_md(path: pathlib.Path, front: dict, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000).strip()
    path.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")


def main():
    DATA.mkdir(exist_ok=True)
    toc: dict = {}
    n_sections = 0
    all_defs = []
    with (DATA / "sections.jsonl").open("w", encoding="utf-8") as sj:
        for path in sorted(RAW_CH.glob("*.html"), key=lambda p: (roman_to_int(p.stem.split("__")[0].split("-")[1]), int(p.stem.split("chapter-")[1]))):
            meta, recs = parse_chapter(path)
            art = toc.setdefault(meta["article"], {"article": meta["article"], "article_num": meta["article_num"],
                                                   "article_title": meta["article_title"], "chapters": []})
            art["chapters"].append({**{k: meta[k] for k in ("chapter", "chapter_title", "chapter_last_amended", "url", "slug")},
                                    "sections": [{"id": r["id"], "title": r["title"], "level": r["level"], "parent": r["parent"],
                                                  "last_amended": r["last_amended"]} for r in recs]})
            for r in recs:
                front = {k: r[k] for k in ("id", "title", "article", "article_title", "chapter", "chapter_title", "level",
                                           "parent", "url", "last_amended", "node_id", "districts", "defined_terms", "cross_refs")}
                heading = f"# {r['id']} {r['title']}".rstrip()
                write_md(OUT / meta["slug"] / f"{r['id']}.md", front, heading + "\n\n" + r["body_md"])
                sj.write(json.dumps(r, ensure_ascii=False) + "\n")
                all_defs.extend(r["definitions"])
                n_sections += 1
            print(f"{meta['article']:>4} ch{meta['chapter']:<2} {len(recs):4} sections  {meta['chapter_title']}")
    with (DATA / "definitions.jsonl").open("w", encoding="utf-8") as dj:
        for d in all_defs:
            dj.write(json.dumps(d, ensure_ascii=False) + "\n")

    appendices = []
    for path in sorted(RAW_AP.glob("*.html")):
        r = parse_appendix(path)
        write_md(OUT / "appendices" / f"{r['id']}.md",
                 {k: r[k] for k in ("id", "title", "url", "last_amended", "districts", "cross_refs")},
                 f"# {r['title']}\n\n{r['body_md']}")
        appendices.append({k: r[k] for k in ("id", "title", "url", "last_amended")})
        print(f" app {len(r['text']):>7} chars  {r['title'][:70]}")
    with (DATA / "appendices.jsonl").open("w", encoding="utf-8") as aj:
        for path in sorted(RAW_AP.glob("*.html")):
            aj.write(json.dumps(parse_appendix(path), ensure_ascii=False) + "\n")

    (DATA / "toc.json").write_text(json.dumps({"articles": sorted(toc.values(), key=lambda a: a["article_num"]),
                                               "appendices": appendices}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{n_sections} sections, {len(all_defs)} definitions, {len(appendices)} appendices")


if __name__ == "__main__":
    main()
