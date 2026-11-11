#!/usr/bin/env python3
"""Fetch and parse the NYC Housing Maintenance Code (Administrative Code title 27, chapter 2) into data/hmc/ and
sections/hmc/.

The city publishes the code as one PDF (the HTML edition sits behind a browser challenge). It is read with pdftotext
and split into subchapters, articles and sections (§ 27-2074), each section becoming an entry cited as `hmc:27-2074`.
The PDF carries no per-section amendment dates, so entries record the PDF's own date instead.

  .venv/bin/python scripts/hmc.py [--force]       needs pdftotext: brew install poppler
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lawkit import Fetcher, http_date, nice_title, now, pdf_pages, reflow, write_jsonl, write_md  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw/hmc"
DATA = ROOT / "data/hmc"
OUT = ROOT / "sections/hmc"
PDF = "https://www.nyc.gov/assets/buildings/pdf/HousingMaintenanceCode.pdf"
SOURCE = "https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-67683"  # Title 27 Ch 2 in the official online edition

SUBCH_RE = re.compile(r"^\s*SUBCHAPTER (\d+):\s*$")
ART_RE = re.compile(r"^\s*Article (\d+):\s*$")
SEC_RE = re.compile(r"^\s*§\s*(27-\d{4}(?:\.\d+)?)\s+(.+?)\.?\s*$")
PARA_START = re.compile(r"^([a-z]\.|\(\d+\)|\([a-z]+\)|\d+\.)\s|^(Exceptions?:|Notes?:)(\s|$)")
HEADER = {"TITLE 27 – CHAPTER 2", "TITLE 27 - CHAPTER 2", "NEW YORK CITY HOUSING MAINTENANCE CODE"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-download the PDF")
    a = ap.parse_args()

    f = Fetcher(RAW, delay=0.7, force=a.force)
    pdf, info = f.download(PDF, "HousingMaintenanceCode.pdf")
    doc_date = http_date(info.get("last_modified"))
    pages = pdf_pages(pdf)
    if OUT.exists():
        shutil.rmtree(OUT)

    entries: list[dict] = []
    sub = art = None  # current heading ids
    sub_n = art_n = 0
    pending: dict | None = None  # a SUBCHAPTER/Article line whose title lines follow
    cur: dict | None = None
    cur_lines: list[str] = []

    def close():
        nonlocal cur, cur_lines
        if cur is not None:
            cur["body_md"] = reflow(cur_lines, PARA_START, indent_unit=2)
            entries.append(cur)
        cur, cur_lines = None, []

    def finish_pending():
        nonlocal pending, sub, art
        if pending is None:
            return
        title = nice_title(" ".join(pending["lines"])) if pending["kind"] == "sub" else " ".join(pending["lines"])
        if pending["kind"] == "sub":
            sub = f"subchapter-{pending['n']}"
            entries.append({"id": sub, "title": f"Subchapter {pending['n']}: {title}", "level": 1, "parent": None,
                            "url": f"{PDF}#page={pending['page']}", "last_amended": None, "place": "", "body_md": "",
                            "sort_key": f"{pending['n']:02d}"})
        else:
            art = f"{sub}-article-{pending['n']}"
            entries.append({"id": art, "title": f"Article {pending['n']}: {title}", "level": 2, "parent": sub,
                            "url": f"{PDF}#page={pending['page']}", "last_amended": None, "place": f"Subch. {sub_n}",
                            "body_md": "", "sort_key": f"{sub_n:02d}.{pending['n']:02d}"})
        pending = None

    for pno, page in enumerate(pages, 1):
        for raw in page.split("\n"):
            line = raw.rstrip()
            s = line.strip()
            if not s:
                if pending is not None and pending["lines"]:
                    finish_pending()
                elif cur is not None:
                    cur_lines.append("")
                continue
            if s in HEADER or re.fullmatch(r"\d{1,3}", s):
                continue
            m = SUBCH_RE.match(s)
            if m:
                close(); finish_pending()
                sub_n = int(m.group(1)); pending = {"kind": "sub", "n": sub_n, "lines": [], "page": pno}
                continue
            m = ART_RE.match(s)
            if m:
                close(); finish_pending()
                art_n = int(m.group(1)); pending = {"kind": "art", "n": art_n, "lines": [], "page": pno}
                continue
            if pending is not None:
                pending["lines"].append(s)
                continue
            m = SEC_RE.match(s)
            if m:
                close()
                sid = m.group(1)
                num, _, dec = sid[3:].partition(".")
                cur = {"id": sid, "title": m.group(2).strip(), "level": 3, "parent": art, "url": f"{PDF}#page={pno}",
                       "last_amended": None, "place": f"Subch. {sub_n}, Art. {art_n}",
                       "sort_key": f"{sub_n:02d}.{art_n:02d}.{int(num):04d}.{int(dec or 0):02d}"}
                continue
            if cur is not None:
                cur_lines.append(line)
    close()

    for e in (x for x in entries if x["level"] == 3):
        write_md(OUT / e["parent"].split("-article-")[0] / f"article-{e['parent'].split('-article-')[1]}" / f"{e['id']}.md",
                 {"id": e["id"], "title": e["title"], "code": "NYC Housing Maintenance Code", "place": e["place"],
                  "url": e["url"], "pdf_date": doc_date},
                 f"# HMC § {e['id']} {e['title']}\n\n{e['body_md']}")
    n = write_jsonl(DATA / "sections.jsonl", entries)
    meta = {"source": PDF, "official_online": SOURCE, "fetched_at": f.first_fetch_at or now(), "document_date": doc_date,
            "latest_amendment": None, "entries": n, "sections": sum(1 for e in entries if e["level"] == 3)}
    (DATA / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {n} entries ({meta['sections']} sections) to data/hmc/; PDF dated {doc_date}")
    for e in entries[:5]:
        print(f"   {e['id']:<24} L{e['level']} {e['title'][:50]:<51} {len(e['body_md']):5} chars")


if __name__ == "__main__":
    main()
