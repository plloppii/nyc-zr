#!/usr/bin/env python3
"""Fetch and parse the New York State Multiple Dwelling Law into data/mdl/ and sections/mdl/.

The official text lives at https://www.nysenate.gov/legislation/laws/MDW, which refuses plain HTTP clients (a browser
challenge) and whose API needs a key. newyork.public.law mirrors the same text as static pages and cites the official
URL and update date on every section, so that is what is read. Each section record keeps the official nysenate URL.

  .venv/bin/python scripts/mdl.py [--only article-3] [--force]

Pages are cached under raw/mdl/. The outline is articles -> (titles ->) sections; headings become entries with an
empty body, like Zoning Resolution group headings, so `zr.py toc mdl` and `get mdl:article-3` work the same way.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lawkit import Fetcher, norm, now, parse_date, write_jsonl, write_md  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw/mdl"
DATA = ROOT / "data/mdl"
OUT = ROOT / "sections/mdl"
MIRROR = "https://newyork.public.law/laws/"
LAW = "n.y._multiple_dwelling_law"
OFFICIAL = "https://www.nysenate.gov/legislation/laws/MDW"

HEADING_RE = re.compile(rf"^{re.escape(LAW)}_article_(\d+(?:-[a-z])?)(?:_title_(\d+(?:-[a-z])?))?$")
SECTION_RE = re.compile(rf"^{re.escape(LAW)}_section_(\d+(?:-[a-z])?)$")


def key(num: str, width: int) -> str:
    """'26-a' -> '0026a', '5' -> '0005' (sorts after '0004z'), so suffixed sections follow their base number."""
    m = re.match(r"^(\d+)(?:-([a-z]))?$", num)
    return f"{int(m.group(1)):0{width}d}{m.group(2) or ''}"


def children(page_html: str) -> list[dict]:
    """The entries a heading page lists: relative links to articles, titles or sections, with number and name."""
    soup = BeautifulSoup(page_html, "lxml")
    out, seen = [], set()
    for a in soup.select(f'a[href^="{LAW}_"]'):
        href = a["href"].split("#")[0]
        if href in seen or a.find_parent(id="sibling-nav") is not None:
            continue
        seen.add(href)
        num = a.select_one(".number")
        name = a.select_one(".name")
        if name is None:
            continue
        # the name block is "Name<br><small>Sections 26–37</small>": keep the first line only
        name_text = norm(name.find(string=True, recursive=False) or name.get_text(" "))
        name_text = re.sub(r"\s*--\s*(\w)", lambda m: " — " + m.group(1).upper(), name_text)
        name_text = re.sub(r"\b(Class) ([a-c])\b", lambda m: f"{m.group(1)} {m.group(2).upper()}", name_text)  # "Class a" in the mirror
        out.append({"href": href, "number": norm(num.get_text()) if num else "", "name": name_text})
    return out


def section_body(soup: BeautifulSoup) -> str:
    body = soup.select_one("#leaf-statute-body")
    if body is None:
        return ""
    for a in body.select("a.pragmatic"):  # the mirror renders each cross-reference twice; keep the statute's own words
        a.decompose()
    for div in body.select("div.d-print-none"):  # the "Source:" footer
        div.decompose()
    blocks = body.select("section.outline")
    if not blocks:
        return norm(body.get_text(" "))
    lines = []
    for b in blocks:
        m = re.search(r"level-(\d+)", " ".join(b.get("class", [])))
        level = int(m.group(1)) if m else 0
        marker = b.find(["h2", "h3", "h4", "h5", "h6"])
        mk = norm(marker.get_text()) if marker else ""
        if marker:
            marker.decompose()
        text = norm(b.get_text(" "))
        lines.append("  " * level + (f"{mk} {text}" if mk else text))
    return "\n\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="one heading id, e.g. article-3 (for a quick check)")
    ap.add_argument("--force", action="store_true", help="refetch pages already cached under raw/mdl/")
    ap.add_argument("--delay", type=float, default=0.7)
    a = ap.parse_args()

    f = Fetcher(RAW, delay=a.delay, force=a.force)
    if not a.only and OUT.exists():
        shutil.rmtree(OUT)  # start from an empty tree so repealed or renumbered sections do not linger
    records: list[dict] = []
    latest = ""

    index = f.get(MIRROR + LAW, "index")
    articles = children(index)
    print(f"{len(articles)} articles")
    for art in articles:
        m = HEADING_RE.match(art["href"])
        if not m or m.group(2):
            continue
        anum = m.group(1)
        aid = f"article-{anum}"
        if a.only and a.only != aid:
            continue
        akey = key(anum, 3)
        records.append({"id": aid, "title": f"Article {anum.upper()}: {art['name']}", "level": 1, "parent": None,
                        "url": MIRROR + art["href"], "last_amended": None, "place": "", "body_md": "", "sort_key": akey})
        page = f.get(MIRROR + art["href"], aid)
        kids = children(page)
        titles = [k for k in kids if HEADING_RE.match(k["href"]) and HEADING_RE.match(k["href"]).group(2)]
        # sections listed directly under the article (articles without titles), then each title's sections
        groups = [(None, None, [k for k in kids if SECTION_RE.match(k["href"])])]
        for t in titles:
            tm = HEADING_RE.match(t["href"])
            tnum = tm.group(2)
            tid = f"{aid}-title-{tnum}"
            records.append({"id": tid, "title": f"Title {tnum.upper()}: {t['name']}", "level": 2, "parent": aid,
                            "url": MIRROR + t["href"], "last_amended": None, "place": f"Art. {anum.upper()}",
                            "body_md": "", "sort_key": f"{akey}.{key(tnum, 2)}"})
            tpage = f.get(MIRROR + t["href"], tid)
            groups.append((tid, tnum, [k for k in children(tpage) if SECTION_RE.match(k["href"])]))
        n_art = 0
        for parent, tnum, secs in groups:
            for s in secs:
                snum = SECTION_RE.match(s["href"]).group(1)
                spage = f.get(MIRROR + s["href"], f"section-{snum}")
                soup = BeautifulSoup(spage, "lxml")
                name = soup.select_one("#number_and_name #name")
                title = norm(name.get_text()) if name else s["name"]
                src = soup.select_one("#footer-source-link")
                url = (src["href"].replace("­", "") if src else f"{OFFICIAL}/{snum.upper()}")
                cite = soup.select_one("#leaf-statute-body cite")
                um = re.search(r"updated ([A-Za-z]{3,9}\.? \d{1,2}, \d{4})", cite.get_text(" ") if cite else "")
                updated = parse_date(um.group(1)) if um else None
                latest = max(latest, updated or "")
                place = f"Art. {anum.upper()}" + (f", Title {tnum.upper()}" if tnum else "")
                body = section_body(soup)
                rec = {"id": snum, "title": title, "level": 3 if tnum else 2, "parent": parent or aid, "url": url,
                       "last_amended": updated, "place": place, "body_md": body,
                       "sort_key": f"{akey}.{key(tnum, 2) if tnum else '00'}.{key(snum, 4)}"}
                records.append(rec)
                folder = OUT / aid / (f"title-{tnum}" if tnum else "")
                write_md(folder / f"{snum}.md",
                         {"id": snum, "title": title, "law": "NY Multiple Dwelling Law", "article": anum.upper(),
                          "title_num": tnum.upper() if tnum else None, "url": url, "updated": updated,
                          "mirror": MIRROR + s["href"]},
                         f"# MDL § {snum} {title}\n\n{body}")
                n_art += 1
        print(f"  {aid:<14} {n_art:4} sections  {art['name']}")

    if not a.only:
        n = write_jsonl(DATA / "sections.jsonl", records)
        meta = {"source": OFFICIAL, "mirror": MIRROR + LAW, "fetched_at": f.first_fetch_at or now(),
                "latest_amendment": latest or None, "entries": n,
                "sections": sum(1 for r in records if r["body_md"]), "headings": sum(1 for r in records if not r["body_md"])}
        (DATA / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {n} entries ({meta['sections']} sections) to data/mdl/; latest update in the text {latest}")
    else:
        print(f"--only: {len(records)} entries parsed, nothing written to data/")
        for r in records[:6]:
            print(f"   {r['id']:<18} L{r['level']} parent={r['parent']} key={r['sort_key']} {r['title'][:50]} updated={r['last_amended']} {len(r['body_md'])} chars")


if __name__ == "__main__":
    main()
